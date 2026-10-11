#!/usr/bin/env python3
"""用【真实 LLM + 生产编排器管线】跑辩论-200 语料，产出候选预测 JSONL。

供 `evaluate_candidates.py --mode real --predictions <jsonl>` 使用。

溯源（可复核）：
  - LLM：`app.llm.build_llm()`，读 `backend/.env`（OPENAI_MODEL / BASE_URL / API_KEY）。
  - 管线：与线上完全一致的 `app.orchestrator.BoardAgent`（LangGraph 双 Agent：analyze→sync→commit）。
  - 输入：`backend/tests/annotations/debate-200-gold.csv` 的 utterance 列，meta_id 固定为 `utt_<line_no>`。
  - 映射：行号 = utterance_id 后缀；节点归属 = Store A 落库后的 node.data.metadata_refs。

健壮性（P2②熔断）：上游偶发 APIConnectionError 连续 5 次即熔断 30s，冷却内全部快速失败。
本脚本按「限流 + 多轮重试（跨冷却窗口）」消除瞬时抖动导致的整段丢失；仅在 analyze 阶段
失败时才丢弃该句（此时 checkpointer 仍残留上一批 summary，必须忽略以免污染）。

输出每行（JSONL）包含 node_id 和 relation_edges（实际图边的目标节点与关系），
评测器通过人工标注的目标发言行解析对应节点，再对比源节点与目标节点。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

GOLD = ROOT / "backend/tests/annotations/debate-200-gold.csv"
MID = "mtg_quality_eval"


def summarize_model_message(message) -> dict:
    """Keep a reproducible response fingerprint without archiving meeting text."""
    content = getattr(message, "content", None) or ""
    calls = getattr(message, "tool_calls", None) or []
    tools = []
    for call in calls:
        function = getattr(call, "function", None)
        arguments = getattr(function, "arguments", "") or ""
        tools.append({"name": getattr(function, "name", None),
                      "arguments_sha256": hashlib.sha256(arguments.encode()).hexdigest(),
                      "arguments_chars": len(arguments)})
    try:
        decoded = json.loads(content)
        shape = ({"keys": sorted(decoded), "insight_count": len(decoded.get("insights", []))}
                 if isinstance(decoded, dict) else None)
    except (ValueError, TypeError):
        shape = None
    return {"content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "content_chars": len(content), "json_shape": shape, "tool_calls": tools}


def project_relation_edges(cells: list[dict], node_by_meta: dict[int, str]) -> dict[int, list[dict]]:
    """Project actual graph edges to the source rows linked to each endpoint."""
    by_node: dict[str, set[tuple[str, str]]] = {}
    for cell in cells:
        if cell.get("shape") != "edge" and cell.get("source") is None:
            continue
        source = cell.get("source")
        target = cell.get("target")
        source = source.get("cell") if isinstance(source, dict) else source
        target = target.get("cell") if isinstance(target, dict) else target
        relation = (cell.get("data") or {}).get("relation")
        if not source or not target or not relation:
            continue
        by_node.setdefault(str(source), set()).add((str(target), str(relation)))
        by_node.setdefault(str(target), set()).add((str(source), str(relation)))
    return {line: [{"target_node_id": other, "relation": relation}
                   for other, relation in sorted(by_node.get(str(node_id), set()))]
            for line, node_id in node_by_meta.items()}


def build_prediction_records(insights_by_line: dict[int, dict], node_by_meta: dict[int, str],
                             relation_edges_by_line: dict[int, list[dict]],
                             committed_lines: set[int]) -> list[dict]:
    """Only committed batches can be counted as predictions from the full pipeline."""
    records = []
    for line_no in sorted(committed_lines):
        ins = insights_by_line.get(line_no, {})
        records.append({
            "line_no": line_no,
            "type": ins.get("type"),
            "relation": ins.get("relation"),
            "relation_edges": relation_edges_by_line.get(line_no, []),
            "evidence_line_no": line_no if line_no in insights_by_line else None,
            "node_id": node_by_meta.get(line_no),
        })
    return records


def load_rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


async def drive(items, batch_size: int, sleep_s: float, max_passes: int, cooldown_wait: float):
    os.environ.setdefault("AMO_DATA_ROOT", tempfile.mkdtemp(prefix="amo_quality_"))
    from app import config as app_config
    from app.adapters import from_cli_text
    from app.llm import build_llm, MockLLM
    from app.orchestrator import BoardAgent
    from app.storage import StoreA, StoreB

    llm = build_llm()
    if isinstance(llm, MockLLM):
        print("[FATAL] build_llm() 回退到了 MockLLM —— 真实 LLM 未启用，评测将无意义。", file=sys.stderr)
        raise SystemExit(2)
    response_summaries: list[dict] = []
    current_batch: list[int] = []
    original_completion = getattr(llm, "_completion", None)
    if original_completion is not None:
        def recorded_completion(messages, **options):
            started = time.monotonic()
            try:
                result = original_completion(messages, **options)
            except Exception as exc:
                response_summaries.append({"line_numbers": list(current_batch),
                                           "elapsed_ms": round((time.monotonic() - started) * 1000),
                                           "error_type": type(exc).__name__})
                raise
            response_summaries.append({"line_numbers": list(current_batch),
                                       "elapsed_ms": round((time.monotonic() - started) * 1000),
                                       **summarize_model_message(result)})
            return result
        llm._completion = recorded_completion
    print(f"[mode] {type(llm).__name__} model={app_config.CONFIG.llm_model} "
          f"base_url={app_config.CONFIG.llm_base_url}")

    store_a, store_b = StoreA(os.environ["AMO_DATA_ROOT"]), StoreB(os.environ["AMO_DATA_ROOT"])
    agent = BoardAgent(store_a, store_b, llm)
    print(f"[input] {len(items)} 句发言，batch_size={batch_size} max_passes={max_passes}")

    insights_by_line: dict[int, dict] = {}
    pending = list(items)
    ok_lines: set[int] = set()
    t0 = time.monotonic()

    for pass_no in range(1, max_passes + 1):
        if not pending:
            break
        if pass_no > 1:
            print(f"[pass {pass_no}] 重试 {len(pending)} 句；等待熔断冷却 {cooldown_wait:.0f}s…")
            time.sleep(cooldown_wait)
        still: list[tuple[int, str]] = []
        batches = [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]
        for bi, chunk in enumerate(batches, 1):
            current_batch[:] = [line_no for line_no, _ in chunk]
            utterances = []
            chunk_lines = set()
            for line_no, text in chunk:
                chunk_lines.add(line_no)
                u = from_cli_text(MID, text, speaker_ref="cli:debate", display_name="辩手",
                                  utterance_id=f"utt_{line_no}")
                store_b.put(u.utterance_id, {"meta_id": u.utterance_id, "kind": "utt", "text": text,
                                             "speaker_ref": u.speaker.speaker_ref,
                                             "start_offset_ms": 0, "end_offset_ms": 0,
                                             "source": u.source})
                utterances.append(u)
            title = "远程办公与固定坐班，谁更适合未来企业" if (pass_no == 1 and bi == 1) else None
            try:
                res = await agent.run(MID, utterances, [], meeting_title=title)
            except Exception as exc:                       # noqa: BLE001
                still.extend(chunk)
                print(f"  [{pass_no}] batch {bi}/{len(batches)} lines={chunk[0][0]} 异常：{exc}")
                continue
            err = res.get("error")
            ms = res.get("meeting_summary")
            analyze_failed = bool(err) and "analyze failed" in str(err)
            if not analyze_failed and ms is not None:       # sync/parse 失败不污染 analyze 结果
                for ins in (ms.insights or []):
                    for meta_id in (ins.evidence or []):
                        m = re.search(r"(\d+)$", str(meta_id))
                        if m and int(m.group(1)) in chunk_lines:
                            insights_by_line[int(m.group(1))] = {"type": ins.type,
                                                                 "relation": ins.relation_to_related}
                if not err:
                    ok_lines.update(chunk_lines)
            if err:
                still.extend(chunk)
                print(f"  [{pass_no}] batch {bi}/{len(batches)} lines={chunk[0][0]} ERR:{err}")
            if sleep_s:
                time.sleep(sleep_s)
        pending = still
        print(f"[pass {pass_no}] 完成，仍失败 {len(pending)} 句（累计 {time.monotonic() - t0:.0f}s）")

    # 节点归属：Store A 落库后 node.data.metadata_refs → meta_id
    cells = store_a.load(MID)

    node_by_meta: dict[int, str] = {}
    for c in cells:
        if str(c.get("shape")) != "edge" and str(c.get("id", "")).startswith("n_"):
            for r in ((c.get("data") or {}).get("metadata_refs") or []):
                m = re.search(r"(\d+)$", str(r))
                if m:
                    node_by_meta[int(m.group(1))] = c.get("id")
    relation_edges_by_line = project_relation_edges(cells, node_by_meta)

    print(f"[board] cells={len(cells)} nodes_with_refs={len(node_by_meta)} "
          f"lines_with_insight={len(insights_by_line)} ok_lines={len(ok_lines)} "
          f"failed_lines={sorted(set(l for l, _ in pending))[:20]}")
    return insights_by_line, node_by_meta, relation_edges_by_line, ok_lines, response_summaries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None, help="仅跑前 N 行（试点）")
    ap.add_argument("--sleep", type=float, default=0.4, help="批间休眠秒数（限流保护）")
    ap.add_argument("--passes", type=int, default=4, help="最大轮数（跨熔断冷却重试）")
    ap.add_argument("--cooldown-wait", type=float, default=35.0, help="重试前等待秒数（>熔断冷却）")
    ap.add_argument("--output", type=Path, default=ROOT / "doc/reports/predictions-real-debate200.jsonl")
    args = ap.parse_args()

    rows = load_rows(GOLD)
    if args.limit:
        rows = rows[:args.limit]
    items = [(int(r["line_no"]), r["utterance"].strip()) for r in rows if r["utterance"].strip()]

    started_at = datetime.now(timezone.utc).isoformat()
    try:
        git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        git_dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        git_head, git_dirty = None, None
    insights_by_line, node_by_meta, relation_edges_by_line, committed_lines, responses = asyncio.run(
        drive(items, args.batch_size, args.sleep, args.passes, args.cooldown_wait))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    records = build_prediction_records(
        insights_by_line, node_by_meta, relation_edges_by_line, committed_lines)
    with args.output.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    responses_path = args.output.with_suffix(".responses.jsonl")
    with responses_path.open("w", encoding="utf-8") as fh:
        for response in responses:
            fh.write(json.dumps(response, ensure_ascii=False) + "\n")
    from app import config as app_config
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"schema": "amo.quality-run/v1", "record_kind": "captured_during_run",
                "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(),
                "git_head": git_head, "working_tree_dirty_at_start": git_dirty,
                "model": app_config.CONFIG.llm_model,
                "provider_base_url": app_config.CONFIG.llm_base_url,
                "batch_size": args.batch_size, "sleep_seconds": args.sleep,
                "max_passes": args.passes, "cooldown_seconds": args.cooldown_wait,
                "input_rows": len(items), "committed_prediction_rows": len(records),
                "failed_line_numbers": sorted(set(line for line, _ in items) - committed_lines),
                "input_gold_csv_sha256": digest(GOLD),
                "prompt_yaml_sha256": digest(BACKEND / "app/prompts.yaml"),
                "generator_py_sha256": digest(Path(__file__)),
                "prediction_jsonl_sha256": digest(args.output),
                "response_summary_jsonl_sha256": digest(responses_path),
                "response_summary_file": str(responses_path.relative_to(ROOT)),
                "response_summary_limits": "Hashes, lengths and structure only; raw model text is not archived."}
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[out] {len(records)} 条已提交预测（输入 {len(items)} 句，"
          f"失败 {len(items) - len(committed_lines)} 句）→ {args.output}")


if __name__ == "__main__":
    main()
