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

输出每行（JSONL）字段对齐评测器：
  line_no, type, relation, ownership_anchor, evidence_line_no, node_id
说明：ownership_anchor 需要标注集私有的语义锚键（relation_anchor 的后缀），
真实 LLM 无从得知，故置 null —— 评测器由此得出 anchor_accuracy=0 属预期，非模型能力缺陷。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

GOLD = ROOT / "backend/tests/annotations/debate-200-gold.csv"
MID = "mtg_quality_eval"
_PREF = {"oppose": 5, "support": 4, "duplicate": 3, "subordinate": 2, "derive": 1, "replace": 0}


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

    def _endpoint(v):
        return (v or {}).get("cell") if isinstance(v, dict) else v

    rel_by_node: dict[str, set] = {}
    for c in cells:
        if str(c.get("shape")) == "edge" or c.get("source") is not None:
            rel = (c.get("data") or {}).get("relation")
            for nid in (_endpoint(c.get("source")), _endpoint(c.get("target"))):
                if nid:
                    rel_by_node.setdefault(str(nid), set()).add(rel)

    def _relation_for(node_id: str):
        rels = {r for r in (rel_by_node.get(str(node_id)) or set()) if r}
        return max(rels, key=lambda r: _PREF.get(r, -1)) if rels else None

    node_by_meta: dict[int, str] = {}
    for c in cells:
        if str(c.get("shape")) != "edge" and str(c.get("id", "")).startswith("n_"):
            for r in ((c.get("data") or {}).get("metadata_refs") or []):
                m = re.search(r"(\d+)$", str(r))
                if m:
                    node_by_meta[int(m.group(1))] = c.get("id")
    for line_no, node_id in node_by_meta.items():
        ins = insights_by_line.setdefault(line_no, {})
        if not ins.get("relation"):
            ins["relation"] = _relation_for(node_id)

    print(f"[board] cells={len(cells)} nodes_with_refs={len(node_by_meta)} "
          f"lines_with_insight={len(insights_by_line)} ok_lines={len(ok_lines)} "
          f"failed_lines={sorted(set(l for l, _ in pending))[:20]}")
    return insights_by_line, node_by_meta


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

    insights_by_line, node_by_meta = asyncio.run(
        drive(items, args.batch_size, args.sleep, args.passes, args.cooldown_wait))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with args.output.open("w", encoding="utf-8") as fh:
        for line_no in sorted(set(insights_by_line) | set(node_by_meta)):
            ins = insights_by_line.get(line_no, {})
            fh.write(json.dumps({
                "line_no": line_no,
                "type": ins.get("type"),
                "relation": ins.get("relation"),
                "ownership_anchor": None,     # 标注集私有锚键，模型不可知（见文件头说明）
                "evidence_line_no": line_no,
                "node_id": node_by_meta.get(line_no),
            }, ensure_ascii=False) + "\n")
            n += 1
    print(f"[out] {n} 条预测（覆盖 {len(items)} 句中 {len(insights_by_line)} 句）→ {args.output}")


if __name__ == "__main__":
    main()
