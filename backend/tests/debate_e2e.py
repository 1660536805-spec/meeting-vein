"""豆腐脑辩论赛 端到端结构测试：读 豆腐脑辩论赛.txt → 逐句驱动双 Agent → 校验图结构。

运行（backend 目录）：
    ..\\.venv\\Scripts\\python.exe tests/debate_e2e.py            # 走 .env 配置的真实 LLM
    ..\\.venv\\Scripts\\python.exe tests/debate_e2e.py --mock     # 强制 MockLLM（仅验链路/方向）

校验目标（对应「甜咸豆腐脑辩论赛」期望图形态）：
  C1. n_issue_root 为议题根，label = 会议标题「甜咸豆腐脑辩论赛」
  C2. 根下 ≥2 个观点(point)子节点（观点A 甜豆腐脑 / 观点B 咸豆腐脑）
  C3. 每个观点下 ≥1 个论据子节点（evidence 或更深层节点）
  C4. 全部边方向 source 为 target 的祖先（父→子，禁止子→父/兄弟互指）
  C5. 无游离节点（所有节点都可从根到达）
输出：树形大纲（含 relation 与派系）+ 各项 PASS/FAIL；全部通过退出码 0，否则 1。
"""
from __future__ import annotations
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MEETING_ID = "mtg_debate"
MEETING_TITLE = "甜咸豆腐脑辩论赛"
DEBATE_TXT = Path(__file__).with_name("豆腐脑辩论赛.txt")

# 期望的核心观点关键词（观点A/B 各自至少命中其一）
VIEWPOINT_KEYWORDS = {
    "观点A(甜)": ["甜"],
    "观点B(咸)": ["咸"],
}


def parse_debate(path: Path):
    """解析「发言人：内容」行 → [(speaker, text)]；跳过空行/无冒号行。"""
    turns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(主持人|正方A|反方B)[：:]\s*(.+)$", line)
        if m:
            turns.append((m.group(1), m.group(2).strip()))
    return turns


async def run_debate(use_mock: bool):
    os.environ.setdefault("AMO_DATA_ROOT", tempfile.mkdtemp(prefix="amo_debate_"))
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

    turns = parse_debate(DEBATE_TXT)
    print(f"[input] {len(turns)} 句发言（{DEBATE_TXT.name}）")

    async def drive(i, speaker, text):
        u = from_cli_text(MEETING_ID, text, speaker_ref=f"cli:{speaker}", display_name=speaker)
        store_b.put(u.utterance_id, {"meta_id": u.utterance_id, "kind": "utt", "text": text,
                                     "speaker_ref": u.speaker.speaker_ref,
                                     "start_offset_ms": 0, "end_offset_ms": 0,
                                     "source": u.source})
        r = await agent.run(MEETING_ID, [u], [],
                            meeting_title=MEETING_TITLE if i == 0 else None)
        return bool(r.get("error"))

    failed = []
    for i, (speaker, text) in enumerate(turns):
        err = await drive(i, speaker, text)
        print(f"  batch {i + 1}/{len(turns)} [{speaker}] {text[:24]}…"
              + ("（LLM 失败，待补跑）" if err else ""))
        if err:
            failed.append((i, speaker, text))
    if failed:                                             # 失败批次补跑一轮（限流恢复后整合进来）
        print(f"[retry] 补跑 {len(failed)} 个失败批次")
        for i, speaker, text in failed:
            err = await drive(i, speaker, text)
            print(f"  retry [{speaker}] {text[:24]}… " + ("（仍失败，跳过）" if err else "ok"))
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

    # C3（对齐验收）：观点A(甜)/观点B(咸) 两个核心观点各 ≥1 个论据子节点，
    # 且全图至少各出现一条 support 与 oppose 边（观点下既有支持又有反驳）。
    # 注意排他匹配：「无论甜咸…」这类总结句同时含两字，不算任何一派核心观点。
    def _lab(k):
        return nodes[k]["data"].get("label", "")
    core_a = [k for k in viewpoints if "甜" in _lab(k) and "咸" not in _lab(k)]
    core_b = [k for k in viewpoints if "咸" in _lab(k) and "甜" not in _lab(k)]
    a_ok = bool(core_a) and all(len(children.get(k, [])) >= 1 for k in core_a)
    b_ok = bool(core_b) and all(len(children.get(k, [])) >= 1 for k in core_b)
    rels = {e["rel"] for e in edges}
    results.append((a_ok and b_ok and {"support", "oppose"} <= rels,
                    f"C3 甜/咸核心观点各 ≥1 论据 且全图含 support+oppose 边"
                    f"（甜 {[nodes[k]['data']['label'] for k in core_a]}"
                    f" / 咸 {[nodes[k]['data']['label'] for k in core_b]} / relations={sorted(rels)}）"))

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

    # 派系覆盖：甜/咸关键词至少各出现在一个节点 label 中
    labels = " ".join(c["data"].get("label", "") for c in nodes.values())
    faction_ok = all(any(kw in labels for kw in kws) for kws in VIEWPOINT_KEYWORDS.values())
    results.append((faction_ok, "C6 甜/咸两派观点关键词均出现"))

    print("\n===== 校验结果 =====")
    all_ok = True
    for ok, msg in results:
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    return all_ok


def main():
    use_mock = "--mock" in sys.argv
    store_a = asyncio.run(run_debate(use_mock))
    cells = store_a.load(MEETING_ID)
    print(f"\n[board] {len([c for c in cells if c.get('shape') != 'edge'])} 节点 / "
          f"{len([c for c in cells if c.get('shape') == 'edge'])} 边 / version={store_a.version(MEETING_ID)}")
    ok = evaluate(cells)
    print(f"\n===== 结论：{'全部通过' if ok else '存在未达标项，需继续优化提示词/代码'} =====")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
