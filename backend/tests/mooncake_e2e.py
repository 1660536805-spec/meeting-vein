"""中秋月饼讨论会 端到端结构测试：读 中秋月饼讨论会.txt → 分批驱动双 Agent → 校验图结构。

与 debate_e2e.py 同框架，差异：
  - 输入 70 句（5 人 A-E 自由讨论），按窗口分批驱动（每批 7 句，共 10 批），
    模拟真实语音流的窗口化批处理，避免 70 次全量推理。
  - 判据对齐多口味讨论形态（非两派对垒）：口味观点覆盖 / 正反边 / 结尾行动决议。

运行（backend 目录）：
    ..\\.venv\\Scripts\\python.exe tests/mooncake_e2e.py            # 走 .env 配置的真实 LLM
    ..\\.venv\\Scripts\\python.exe tests/mooncake_e2e.py --mock     # 强制 MockLLM（仅验链路/方向）

校验目标（对应「中秋月饼口味讨论会」期望图形态）：
  C1. n_issue_root 为议题根（issue）
  C2. 根下 ≥2 个观点(point)子节点（五仁 / 莲蓉蛋黄 / 冰皮 / 流心奶黄…多个口味观点）
  C3. 五仁 / 莲蓉 / 冰皮 三大口味观点关键词均出现在某 point 节点
  C4. 全部边方向 source 为 target 的祖先（父→子，禁止子→父/兄弟互指）
  C5. 无游离节点（所有节点都可从根到达）
  C6. 全图含 support 与 oppose 边（讨论有赞有反：反五仁 / 护五仁 / 反榴莲…）
  C7. 产出收尾节点（action/conclusion）：一起买多种口味 / 团圆分享
输出：树形大纲（含 relation）+ 各项 PASS/FAIL；全部通过退出码 0，否则 1。
"""
from __future__ import annotations
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MEETING_ID = "mtg_mooncake"
MEETING_TITLE = "中秋月饼口味讨论会"
MOONCAKE_TXT = Path(__file__).with_name("中秋月饼讨论会.txt")
BATCH_SIZE = 7

# 三大口味观点：各自至少命中一个 point 节点的 label
FLAVOR_KEYWORDS = ["五仁", "莲蓉", "冰皮"]
# 收尾行动/结论关键词（结尾共识：一起买几种口味、团圆才是核心）
ENDING_KEYWORDS = ["买", "团圆", "分享", "中秋"]


def parse_discussion(path: Path):
    """解析「发言人：内容」行 → [(speaker, text)]；发言人 1-4 字（A-E / 主持人 等）。"""
    turns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([^\s：:]{1,4})[：:]\s*(.+)$", line)
        if m:
            turns.append((m.group(1), m.group(2).strip()))
    return turns


async def run_discussion(use_mock: bool):
    os.environ.setdefault("AMO_DATA_ROOT", tempfile.mkdtemp(prefix="amo_mooncake_"))
    from app.storage import StoreA, StoreB
    from app.adapters import from_cli_text
    from app.orchestrator import BoardAgent
    from app import config as app_config

    store_a, store_b = StoreA(os.environ["AMO_DATA_ROOT"]), StoreB(os.environ["AMO_DATA_ROOT"])
    if use_mock:
        from app.llm import MockLLM
        llm = MockLLM()
        print("[mode] MockLLM（--mock：仅验证链路与方向，结构平铺属预期）")
    else:
        from app.llm import build_llm
        llm = build_llm()
        print(f"[mode] {type(llm).__name__}（model={app_config.CONFIG.llm_model}）")
    agent = BoardAgent(store_a, store_b, llm)

    turns = parse_discussion(MOONCAKE_TXT)
    print(f"[input] {len(turns)} 句发言（{MOONCAKE_TXT.name}），按每批 {BATCH_SIZE} 句驱动")

    async def drive(batch_idx, batch):
        uts = []
        for speaker, text in batch:
            u = from_cli_text(MEETING_ID, text, speaker_ref=f"cli:{speaker}", display_name=speaker)
            store_b.put(u.utterance_id, {"meta_id": u.utterance_id, "kind": "utt", "text": text,
                                         "speaker_ref": u.speaker.speaker_ref,
                                         "start_offset_ms": 0, "end_offset_ms": 0,
                                         "source": u.source})
            uts.append(u)
        r = await agent.run(MEETING_ID, uts, [],
                            meeting_title=MEETING_TITLE if batch_idx == 0 else None)
        return bool(r.get("error"))

    batches = [turns[i:i + BATCH_SIZE] for i in range(0, len(turns), BATCH_SIZE)]
    failed = []
    for bi, batch in enumerate(batches):
        head = (f"batch {bi + 1}/{len(batches)} [{batch[0][0]}] {batch[0][1][:22]}…"
                f"（{len(batch)} 句）")
        err = await drive(bi, batch)
        print(head + ("（LLM 失败，待补跑）" if err else ""))
        if err:
            failed.append((bi, batch))
    if failed:                                             # 失败批次补跑一轮（限流恢复后整合进来）
        print(f"[retry] 补跑 {len(failed)} 个失败批次")
        for bi, batch in failed:
            err = await drive(bi, batch)
            print(f"  retry batch {bi + 1} " + ("（仍失败，跳过）" if err else "ok"))
    return store_a


def build_tree(cells: list):
    """cells → (nodes{id:cell}, edges[{s,t,relation}], children{pid:[cid]}, parent_of, root_id)"""
    nodes = {c["id"]: c for c in cells if c.get("shape") != "edge"}
    edges = [{"s": (c.get("source") or {}).get("cell"), "t": (c.get("target") or {}).get("cell"),
              "rel": (c.get("data") or {}).get("relation", "support")}
             for c in cells if c.get("shape") == "edge"]
    children: dict = {}
    parent_of: dict = {}
    for e in edges:
        if e["s"] in nodes and e["t"] in nodes:
            children.setdefault(e["s"], []).append(e["t"])
            parent_of.setdefault(e["t"], e["s"])          # 多父取首条（树状约束）
    root = "n_issue_root" if "n_issue_root" in nodes else None
    return nodes, edges, children, parent_of, root


def descendants(children: dict, start: str) -> set:
    out, stack = set(), [start]
    while stack:
        for ch in children.get(stack.pop(), []):
            if ch not in out:
                out.add(ch)
                stack.append(ch)
    return out


def evaluate(cells: list) -> bool:
    nodes, edges, children, parent_of, root = build_tree(cells)

    # 打印树形大纲（含 relation）
    print("\n===== 图结构大纲 =====")

    rel_of: dict = {}
    for e in edges:
        rel_of[(e["s"], e["t"])] = e["rel"]

    def walk(nid, depth, seen):
        d = nodes[nid].get("data", {})
        spk = d.get("speaker_ref") or "-"
        p = parent_of.get(nid)
        head = "ROOT" if p is None else f"-{rel_of.get((p, nid), '?')}->"
        print(f"{'    ' * depth}{head} {nid} [{d.get('type')}] {d.get('label')} (speaker={spk})")
        for ch in children.get(nid, []):
            if ch in seen:
                print(f"{'    ' * (depth + 1)}… 环引用 {ch}")
                continue
            walk(ch, depth + 1, seen | {ch})

    if root:
        walk(root, 0, {root})
    for nid in nodes:
        if nid != root and nid not in descendants(children, root or ""):
            d = nodes[nid].get("data", {})
            print(f"  (游离) {nid} [{d.get('type')}] {d.get('label')}")

    # ---- 校验 ----
    results: list[tuple[bool, str]] = []
    ok = bool(root) and nodes[root].get("data", {}).get("type") == "issue"
    results.append((ok, f"C1 议题根存在且为 issue（label={nodes.get(root, {}).get('data', {}).get('label')!r}）"))

    root_kids = children.get(root, []) if root else []
    viewpoints = [k for k in root_kids if nodes[k].get("data", {}).get("type") == "point"]
    results.append((len(viewpoints) >= 2,
                    f"C2 根下观点(point)子节点 ≥2（实际 {len(viewpoints)}："
                    f"{[nodes[k]['data']['label'] for k in viewpoints]}）"))

    # C3：三大口味观点各出现在某 point 节点（多口味讨论应建多个观点分支，而非平铺论据）
    def _lab(k):
        return nodes[k]["data"].get("label", "")
    point_labels = " ".join(_lab(k) for k in nodes if nodes[k]["data"].get("type") == "point")
    hits = [kw for kw in FLAVOR_KEYWORDS if kw in point_labels]
    results.append((len(hits) == len(FLAVOR_KEYWORDS),
                    f"C3 口味观点覆盖 {'/'.join(FLAVOR_KEYWORDS)}（point 命中 {hits}）"))

    anc_ok = True
    if root:
        reach = {nid: descendants(children, nid) for nid in nodes}
        for e in edges:
            if e["s"] not in nodes or e["t"] not in nodes:
                continue
            if e["t"] not in reach.get(e["s"], set()):
                anc_ok = False
                print(f"  [方向违规] {e['s']} -{e['rel']}-> {e['t']}（source 不是 target 的祖先）")
    results.append((anc_ok, "C4 全部边方向 source 为 target 祖先（父→子）"))

    reachable = descendants(children, root) | {root} if root else set()
    stray = [nid for nid in nodes if nid not in reachable]
    results.append((not stray, f"C5 无游离节点（实际 {'无' if not stray else stray}）"))

    # C6：讨论有赞有反（C 反五仁、A 反冰皮、D 反榴莲…应同时出现 support 与 oppose 边）
    rels = {e["rel"] for e in edges}
    results.append(({"support", "oppose"} <= rels,
                    f"C6 全图含 support+oppose 边（relations={sorted(rels)}）"))

    # C7：结尾行动/结论决议（一起买多种口味各吃各的 → action/conclusion 节点）
    tail = [k for k in nodes
            if nodes[k]["data"].get("type") in ("action", "conclusion")
            and any(kw in _lab(k) for kw in ENDING_KEYWORDS)]
    tail_desc = [f"{nodes[k]['data']['type']}:{_lab(k)}" for k in tail]
    results.append((bool(tail), f"C7 收尾行动/结论节点（实际 {tail_desc}）"))

    print("\n===== 校验结果 =====")
    all_ok = True
    for ok, msg in results:
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    return all_ok


def main():
    use_mock = "--mock" in sys.argv
    store_a = asyncio.run(run_discussion(use_mock))
    cells = store_a.load(MEETING_ID)
    print(f"\n[board] {len([c for c in cells if c.get('shape') != 'edge'])} 节点 / "
          f"{len([c for c in cells if c.get('shape') == 'edge'])} 边 / version={store_a.version(MEETING_ID)}")
    ok = evaluate(cells)
    print(f"\n===== 结论：{'全部通过' if ok else '存在未达标项，需继续优化提示词/代码'} =====")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
