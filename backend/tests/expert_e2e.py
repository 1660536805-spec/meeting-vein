"""会议专家场景 E2E：debate/proposal/task 三场景 200 句会议 → 分批驱动双 Agent → 校验图结构。

仿照 debate_e2e.py / mooncake_e2e.py 框架，差异：
  - 每场景 200 句长会话，按窗口分批驱动（每批 7 句，共 29 批），模拟真实语音批处理。
  - 每批 agent.run 携带 expert 字段（debate/proposal/task），走对应专家提示词链路。
  - 各场景独立判据：D（辩论对垒）/ P（方案选型）/ T（任务分工），另含方向/游离/元描述公共判据。

运行（backend 目录）：
    ..\\.venv\\Scripts\\python.exe tests/expert_e2e.py debate       # 辩论赛场景（真实 LLM）
    ..\\.venv\\Scripts\\python.exe tests/expert_e2e.py proposal     # 方案选型场景
    ..\\.venv\\Scripts\\python.exe tests/expert_e2e.py task         # 任务分工场景
    追加 --mock 强制 MockLLM（仅验链路/方向）
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

BATCH_SIZE = 7

SCENARIOS = {
    "debate": dict(txt="辩论赛200句.txt", meeting_id="mtg_expert_debate",
                   title="远程办公与固定坐班辩论赛", expert="debate"),
    "proposal": dict(txt="方案讨论200句.txt", meeting_id="mtg_expert_proposal",
                     title="年度团建方案选型会", expert="proposal"),
    "task": dict(txt="任务安排200句.txt", meeting_id="mtg_expert_task",
                 title="Q4双11营销活动筹备会", expert="task"),
}

META_CONFLICT_WORDS = ("存在分歧", "难分高下", "不分伯仲", "各有优劣", "各有道理")
STANCE_WORDS = ("正方", "反方", "支持派", "反对派")


def parse_discussion(path: Path):
    """解析「发言人：内容」行 → [(speaker, text)]；发言人 1-4 字。"""
    turns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([^\s：:]{1,4})[：:]\s*(.+)$", line)
        if m:
            turns.append((m.group(1), m.group(2).strip()))
    return turns


async def run_scenario(scenario: str, use_mock: bool):
    cfg = SCENARIOS[scenario]
    os.environ.setdefault("AMO_DATA_ROOT", tempfile.mkdtemp(prefix=f"amo_expert_{scenario}_"))
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
        print(f"[mode] {type(llm).__name__}（model={app_config.CONFIG.llm_model}，expert={cfg['expert']}）")
    agent = BoardAgent(store_a, store_b, llm)

    txt = Path(__file__).with_name(cfg["txt"])
    turns = parse_discussion(txt)
    print(f"[input] {len(turns)} 句发言（{txt.name}），按每批 {BATCH_SIZE} 句驱动")

    async def drive(batch_idx, batch):
        uts = []
        for speaker, text in batch:
            u = from_cli_text(cfg["meeting_id"], text, speaker_ref=f"cli:{speaker}", display_name=speaker)
            store_b.put(u.utterance_id, {"meta_id": u.utterance_id, "kind": "utt", "text": text,
                                         "speaker_ref": u.speaker.speaker_ref,
                                         "start_offset_ms": 0, "end_offset_ms": 0,
                                         "source": u.source})
            uts.append(u)
        r = await agent.run(cfg["meeting_id"], uts, [],
                            meeting_title=cfg["title"] if batch_idx == 0 else None,
                            expert=cfg["expert"])
        return bool(r.get("error"))

    batches = [turns[i:i + BATCH_SIZE] for i in range(0, len(turns), BATCH_SIZE)]
    failed = []
    for bi, batch in enumerate(batches):
        head = f"batch {bi + 1}/{len(batches)} [{batch[0][0]}] {batch[0][1][:22]}…（{len(batch)} 句）"
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
    """cells → (nodes{id:cell}, edges[{s,t,rel}], children{pid:[cid]}, parent_of, root_id)"""
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


def print_outline(nodes, edges, children, parent_of, root):
    print("\n===== 图结构大纲 =====")
    rel_of: dict = {(e["s"], e["t"]): e["rel"] for e in edges}

    def walk(nid, depth, seen):
        d = nodes[nid].get("data", {})
        p = parent_of.get(nid)
        head = "ROOT" if p is None else f"-{rel_of.get((p, nid), '?')}->"
        print(f"{'    ' * depth}{head} {nid} [{d.get('type')}] {d.get('label')}")
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


def common_checks(nodes, edges, children, root, prefix: str, start: int):
    """公共判据：边方向祖先 / 无游离 / 无元描述 conflict。返回 [(ok, msg)]，编号 prefixN 顺延。"""
    results: list[tuple[bool, str]] = []
    anc_ok = True
    if root:
        reach = {nid: descendants(children, nid) for nid in nodes}
        for e in edges:
            if e["s"] not in nodes or e["t"] not in nodes:
                continue
            if e["t"] not in reach.get(e["s"], set()):
                anc_ok = False
                print(f"  [方向违规] {e['s']} -{e['rel']}-> {e['t']}（source 不是 target 的祖先）")

    reachable = descendants(children, root) | {root} if root else set()
    stray = [nid for nid in nodes if nid not in reachable]
    bad_conflict = [nodes[k]["data"].get("label", "") for k, c in nodes.items()
                    if c["data"].get("type") == "conflict"
                    and any(w in c["data"].get("label", "") for w in META_CONFLICT_WORDS)]
    checks = [
        (anc_ok, "边方向全部 source 为 target 祖先（父→子）"),
        (not stray, f"无游离节点（实际 {'无' if not stray else stray}）"),
        (not bad_conflict, f"无「存在分歧」类元描述 conflict（实际 {bad_conflict or '无'}）"),
    ]
    for i, (ok, msg) in enumerate(checks):
        results.append((ok, f"{prefix}{start + i} {msg}"))
    return results


def eval_debate(cells) -> list[tuple[bool, str]]:
    nodes, edges, children, parent_of, root = build_tree(cells)
    print_outline(nodes, edges, children, parent_of, root)
    results: list[tuple[bool, str]] = []

    def _lab(k):
        return nodes[k]["data"].get("label", "")

    ok = bool(root) and nodes[root].get("data", {}).get("type") == "issue"
    results.append((ok, f"D1 议题根存在且为 issue（label={nodes.get(root, {}).get('data', {}).get('label')!r}）"))

    root_kids = children.get(root, []) if root else []
    pts = [k for k in root_kids if nodes[k]["data"].get("type") == "point"]
    labels_pts = " ".join(_lab(k) for k in nodes if nodes[k]["data"].get("type") == "point")
    kw_ok = "远程" in labels_pts and "坐班" in labels_pts
    results.append((len(pts) >= 2 and kw_ok,
                    f"D2 根下 point ≥2 且「远程/坐班」均出现在观点 label（根下 point {len(pts)} 个："
                    f"{[_lab(k) for k in pts]}）"))

    developed = [k for k in pts if len(children.get(k, [])) >= 1]
    results.append((len(developed) >= 2,
                    f"D3 ≥2 个观点有子节点展开（实际 {len(developed)} 个：{[_lab(k) for k in developed]}）"))

    rels = {e["rel"] for e in edges}
    results.append(({"support", "oppose"} <= rels,
                    f"D4 全图含 support+oppose 边（relations={sorted(rels)}）"))

    # 立场元描述只禁入参数节点（point/evidence/issue：label 须表达话题主张本身）；
    # conclusion/action 为评委裁定/赛果记录，「正方A/反方B」是被点评对象，允许出现；
    # 评委评述节点（label 含「评委」）及其子树同理豁免——「正方信心贯穿全场」里正反方是评述主语，
    # 不是辩方观点的立场标签。
    def _judge_line(k):
        cur = k
        while cur:
            if "评委" in _lab(cur):
                return True
            cur = parent_of.get(cur)
        return False

    bad_stance = [_lab(k) for k in nodes
                  if nodes[k]["data"].get("type") in ("point", "evidence", "issue")
                  and not _judge_line(k)
                  and any(w in _lab(k) for w in STANCE_WORDS)]
    results.append((not bad_stance,
                    f"D5 参数节点无「正方/反方」类立场元描述 label（结论/赛果/评委评述节点豁免，实际 {bad_stance or '无'}）"))

    results += common_checks(nodes, edges, children, root, "D", 6)

    has_concl = any(c["data"].get("type") == "conclusion" for c in nodes.values())
    results.append((has_concl, "D9 总结陈词产出 conclusion 节点"))
    return results


def eval_proposal(cells) -> list[tuple[bool, str]]:
    nodes, edges, children, parent_of, root = build_tree(cells)
    print_outline(nodes, edges, children, parent_of, root)
    results: list[tuple[bool, str]] = []

    def _lab(k):
        return nodes[k]["data"].get("label", "")

    ok = bool(root) and nodes[root].get("data", {}).get("type") == "issue"
    results.append((ok, f"P1 议题根存在且为 issue（label={nodes.get(root, {}).get('data', {}).get('label')!r}）"))

    root_kids = children.get(root, []) if root else []
    pts = [k for k in root_kids if nodes[k]["data"].get("type") == "point"]
    labels_pts = " ".join(_lab(k) for k in nodes if nodes[k]["data"].get("type") == "point")
    plan_kws = ["温泉", "定向", "轰趴", "海边"]
    hits = [kw for kw in plan_kws if kw in labels_pts]
    results.append((len(pts) >= 3 and len(hits) >= 3,
                    f"P2 根下 point ≥3 且方案关键词命中 ≥3/4（point {len(pts)} 个，命中 {hits}："
                    f"{[_lab(k) for k in pts]}）"))

    rels = {e["rel"] for e in edges}
    results.append(({"support", "oppose"} <= rels,
                    f"P3 全图含 support+oppose 边（relations={sorted(rels)}）"))

    developed_plans = [k for k in pts if len(children.get(k, [])) >= 1]
    results.append((len(developed_plans) >= 2,
                    f"P4 ≥2 个方案观点有子节点（优劣讨论落图，实际 {len(developed_plans)} 个）"))

    tail = [k for k in nodes if nodes[k]["data"].get("type") in ("action", "conclusion")
            and "定向" in _lab(k)]
    tail_desc = [f"{nodes[k]['data']['type']}:{_lab(k)}" for k in tail]
    results.append((bool(tail), f"P5 收尾决议含选定方案「定向」（实际 {tail_desc}）"))

    results += common_checks(nodes, edges, children, root, "P", 6)
    return results


def eval_task(cells) -> list[tuple[bool, str]]:
    nodes, edges, children, parent_of, root = build_tree(cells)
    print_outline(nodes, edges, children, parent_of, root)
    results: list[tuple[bool, str]] = []

    def _lab(k):
        return nodes[k]["data"].get("label", "")

    ok = bool(root) and nodes[root].get("data", {}).get("type") == "issue"
    results.append((ok, f"T1 议题根存在且为 issue（label={nodes.get(root, {}).get('data', {}).get('label')!r}）"))

    actions = {k: _lab(k) for k, c in nodes.items() if c["data"].get("type") == "action"}
    owners = ["小周", "小吴", "小郑", "小冯", "小林"]
    with_owner = [lab for lab in actions.values() if any(o in lab for o in owners)]
    with_deadline = [lab for lab in actions.values() if ("10月" in lab or "11月" in lab)]
    results.append((len(actions) >= 4,
                    f"T2 action 节点 ≥4（实际 {len(actions)}：{list(actions.values())}）"))
    results.append((len(with_owner) >= 2,
                    f"T3 ≥2 个 action label 含负责人（实际 {with_owner}）"))
    results.append((len(with_deadline) >= 2,
                    f"T4 ≥2 个 action label 含截止时间（实际 {with_deadline}）"))

    rels = {e["rel"] for e in edges}
    results.append(("support" in rels,
                    f"T5 存在 support 边（目标背景/论据挂接，relations={sorted(rels)}）"))

    tail = [k for k in nodes if nodes[k]["data"].get("type") in ("conclusion", "action")
            and ("分工" in _lab(k) or "复盘" in _lab(k))]
    tail_desc = [f"{nodes[k]['data']['type']}:{_lab(k)}" for k in tail]
    results.append((bool(tail), f"T6 收尾结论含「分工/复盘」（实际 {tail_desc}）"))

    results += common_checks(nodes, edges, children, root, "T", 7)
    return results


EVALS = {"debate": eval_debate, "proposal": eval_proposal, "task": eval_task}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_mock = "--mock" in sys.argv
    scenario = args[0] if args else ""
    if scenario not in SCENARIOS:
        print(f"用法：python tests/expert_e2e.py {'|'.join(SCENARIOS)} [--mock]（收到：{scenario!r}）")
        sys.exit(2)
    store_a = asyncio.run(run_scenario(scenario, use_mock))
    cfg = SCENARIOS[scenario]
    cells = store_a.load(cfg["meeting_id"])
    print(f"\n[board] {len([c for c in cells if c.get('shape') != 'edge'])} 节点 / "
          f"{len([c for c in cells if c.get('shape') == 'edge'])} 边 / version={store_a.version(cfg['meeting_id'])}")
    results = EVALS[scenario](cells)
    print("\n===== 校验结果 =====")
    all_ok = True
    for ok, msg in results:
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    print(f"\n===== {scenario} 结论：{'全部通过' if all_ok else '存在未达标项，需继续优化提示词/代码'} =====")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
