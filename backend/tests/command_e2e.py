"""用户指令类文本 端到端测试：种子看板 → 逐条推送指令发言 → 校验 cmd 标记与挂接迁移。

链路：指令发言 → analyzer 识别 type=command（summary 原样保留指令文本，含指代对象名）
      → syncer 按意图映射 mark_node(gray/strike) / move_node（label 匹配换算节点 id）
      → StoreA 落 cell.data.cmd = {mark, reason, by:"user"}（前端按 mark 渲染灰化/删除线）。

运行（backend 目录）：
    ..\\.venv\\Scripts\\python.exe tests/command_e2e.py            # 真实 LLM（.env 配置）
    ..\\.venv\\Scripts\\python.exe tests/command_e2e.py --mock     # MockLLM 仅验链路（cmd 判据会 FAIL 属预期）

判据：
  S1. 种子看板含 五仁/莲蓉蛋黄/冰皮/榴莲 四个口味观点（各命中一个 point 节点）
  V1. 「暂时不考虑五仁」→ 五仁节点 data.cmd.mark == "gray" 且 reason/by 齐备（灰化=暂不考虑）
  V2. 「删除榴莲节点」→ 榴莲节点 data.cmd.mark == "strike" 且 reason/by 齐备（软删除：节点保留）
  V3. 「把莲蓉蛋黄移到冰皮下」→ 莲蓉蛋黄 父边改挂冰皮节点，且 data.cmd.mark == "move"
  V4. cmd 公共字段：by == "user"；指令处理后无游离节点（软删除节点仍在树内可达）
输出：图结构大纲（含 cmd 标记与 relation）+ 各项 PASS/FAIL；全部通过退出码 0，否则 1。
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MEETING_ID = "mtg_command"
MEETING_TITLE = "中秋月饼口味讨论会"

# 种子讨论（1 批 7 句）：建立 4 个口味观点 + 1 条反榴莲攻击句
SEED = [
    ("主持人", "今天开个短会，大家把中秋买月饼的想法聊一聊，我来记重点。"),
    ("小A", "我先说，我家老人就认五仁月饼，果仁多、口感扎实，每年都买。"),
    ("小B", "五仁太老了，我更倾向莲蓉蛋黄，甜度适中，蛋黄还寓意团圆。"),
    ("小C", "冰皮月饼这几年很火，冷藏后口感清爽，聚会摆盘也好看。"),
    ("小D", "榴莲月饼最近挺流行，榴莲控基本人手一盒，香味浓郁。"),
    ("小A", "榴莲那个味道太冲，我们办公室一半人接受不了。"),
    ("主持人", "好，目前就是五仁、莲蓉蛋黄、冰皮、榴莲四个方向，大家再想想。"),
]

# 三条指令（逐条成批推送，避免与普通发言混批）
COMMANDS = [
    ("暂时不考虑五仁月饼这点，我们先聚焦其他口味。", "gray"),
    ("把榴莲月饼这个节点删除掉，刚才大家都说不接受。", "strike"),
    ("把莲蓉蛋黄这个节点移动到冰皮月饼节点下面，放一起对比。", "move"),
]

FLAVORS = ["五仁", "莲蓉蛋黄", "冰皮", "榴莲"]


async def run_flow(use_mock: bool):
    os.environ.setdefault("AMO_DATA_ROOT", tempfile.mkdtemp(prefix="amo_command_"))
    from app.storage import StoreA, StoreB
    from app.adapters import from_cli_text
    from app.orchestrator import BoardAgent
    from app import config as app_config

    store_a, store_b = StoreA(os.environ["AMO_DATA_ROOT"]), StoreB(os.environ["AMO_DATA_ROOT"])
    if use_mock:
        from app.llm import MockLLM
        llm = MockLLM()
        print("[mode] MockLLM（--mock：仅验链路，cmd 判据会 FAIL 属预期）")
    else:
        from app.llm import build_llm
        llm = build_llm()
        print(f"[mode] {type(llm).__name__}（model={app_config.CONFIG.llm_model}）")
    agent = BoardAgent(store_a, store_b, llm)

    async def drive(speaker, text, first):
        u = from_cli_text(MEETING_ID, text, speaker_ref=f"cli:{speaker}", display_name=speaker)
        store_b.put(u.utterance_id, {"meta_id": u.utterance_id, "kind": "utt", "text": text,
                                     "speaker_ref": u.speaker.speaker_ref,
                                     "start_offset_ms": 0, "end_offset_ms": 0,
                                     "source": u.source})
        r = await agent.run(MEETING_ID, [u], [],
                            meeting_title=MEETING_TITLE if first else None)
        return r

    # 1) 种子批次
    print(f"[seed] 推送 {len(SEED)} 句建立看板…")
    for spk, text in SEED:
        r = await drive(spk, text, first=(spk, text) == SEED[0])
        if r.get("error"):
            print(f"  [seed-err] {r['error']}")
    # 失败补跑一轮
    if store_a.exists(MEETING_ID) and not store_a.load(MEETING_ID):
        print("[seed-retry] 看板为空，补跑种子批次…")
        for spk, text in SEED:
            await drive(spk, text, first=True)

    # 2) 指令批次：逐条推送，打印 LLM 产出的 ops 便于调优
    for i, (text, want) in enumerate(COMMANDS):
        print(f"\n[cmd {i + 1}/{len(COMMANDS)}] want={want}  「{text}」")
        r = await drive("主持人", text, first=False)
        if r.get("error"):
            print(f"  [cmd-err] {r['error']}（补跑一次）")
            r = await drive("主持人", text, first=False)
        op = r.get("llm_output")
        ops = getattr(op, "operations", None) or []
        for g in ops:
            print(f"  [op] {g.op} node={g.node} parent={getattr(g, 'parent', None)} "
                  f"mark={getattr(g, 'mark', None)} reason={getattr(g, 'reason', None)}")
        if not ops:
            print("  [op] （本批无操作产出）")
    return store_a


def build_tree(cells: list):
    nodes = {c["id"]: c for c in cells if c.get("shape") != "edge"}
    edges = [{"s": (c.get("source") or {}).get("cell"), "t": (c.get("target") or {}).get("cell"),
              "rel": (c.get("data") or {}).get("relation", "support")}
             for c in cells if c.get("shape") == "edge"]
    children: dict = {}
    parent_of: dict = {}
    for e in edges:
        if e["s"] in nodes and e["t"] in nodes:
            children.setdefault(e["s"], []).append(e["t"])
            parent_of.setdefault(e["t"], e["s"])
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


def find_by_kw(nodes: dict, kw: str):
    for nid, c in nodes.items():
        if kw in (c.get("data", {}).get("label") or ""):
            return nid
    return None


def evaluate(cells: list, skip_cmd: bool) -> bool:
    nodes, edges, children, parent_of, root = build_tree(cells)

    print("\n===== 图结构大纲（含 cmd 标记） =====")
    rel_of = {(e["s"], e["t"]): e["rel"] for e in edges}

    def cmd_tag(nid):
        cmd = nodes[nid].get("data", {}).get("cmd") or {}
        return f" <cmd={cmd.get('mark')}|{cmd.get('reason', '')}>" if cmd.get("mark") else ""

    def walk(nid, depth, seen):
        d = nodes[nid].get("data", {})
        p = parent_of.get(nid)
        head = "ROOT" if p is None else f"-{rel_of.get((p, nid), '?')}->"
        print(f"{'    ' * depth}{head} {nid} [{d.get('type')}] {d.get('label')}{cmd_tag(nid)}")
        for ch in children.get(nid, []):
            if ch in seen:
                print(f"{'    ' * (depth + 1)}… 环引用 {ch}")
                continue
            walk(ch, depth + 1, seen | {ch})

    if root:
        walk(root, 0, {root})
    reachable = descendants(children, root) | {root} if root else set()
    for nid in nodes:
        if nid not in reachable:
            d = nodes[nid].get("data", {})
            print(f"  (游离) {nid} [{d.get('type')}] {d.get('label')}")

    results: list[tuple[bool, str]] = []

    # S1：四个口味观点全部建出
    hits = [kw for kw in FLAVORS if find_by_kw(nodes, kw)]
    results.append((len(hits) == len(FLAVORS),
                    f"S1 口味观点覆盖 {'/'.join(FLAVORS)}（point 命中 {hits}）"))

    if not skip_cmd:
        # V1/V2/V3：逐条指令的落图结果
        nid_wuren = find_by_kw(nodes, "五仁")
        cmd = nodes.get(nid_wuren, {}).get("data", {}).get("cmd") or {}
        results.append((cmd.get("mark") == "gray" and bool(cmd.get("reason")) and cmd.get("by") == "user",
                        f"V1 五仁灰化 gray（实际 {cmd}）"))

        nid_liuzhi = find_by_kw(nodes, "榴莲")
        cmd = nodes.get(nid_liuzhi, {}).get("data", {}).get("cmd") or {}
        results.append((cmd.get("mark") == "strike" and bool(cmd.get("reason")) and cmd.get("by") == "user",
                        f"V2 榴莲删除线 strike（实际 {cmd}；节点{'保留' if nid_liuzhi in nodes else '被物理删除'}）"))

        nid_lrh = find_by_kw(nodes, "莲蓉蛋黄")
        nid_bingpi = find_by_kw(nodes, "冰皮")
        moved = nid_lrh and nid_bingpi and parent_of.get(nid_lrh) == nid_bingpi
        cmd = nodes.get(nid_lrh, {}).get("data", {}).get("cmd") or {}
        results.append((moved and cmd.get("mark") == "move",
                        f"V3 莲蓉蛋黄改挂冰皮下 + move 标记（实际 parent={parent_of.get(nid_lrh)}，"
                        f"期望={nid_bingpi}，cmd={cmd}）"))

    # V4：cmd.by 均为 user；无游离节点（软删除节点仍在树内）
    bad_by = [nid for nid, c in nodes.items()
              if (c.get("data", {}).get("cmd") or {}).get("mark")
              and (c["data"]["cmd"].get("by") != "user")]
    results.append((not bad_by, f"V4a 全部 cmd.by == user（违规 {bad_by or '无'}）"))
    stray = [nid for nid in nodes if nid not in reachable]
    results.append((not stray, f"V4b 无游离节点（实际 {stray or '无'}）"))

    print("\n===== 校验结果 =====")
    all_ok = True
    for ok, msg in results:
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    return all_ok


def main():
    use_mock = "--mock" in sys.argv
    store_a = asyncio.run(run_flow(use_mock))
    cells = store_a.load(MEETING_ID)
    print(f"\n[board] {len([c for c in cells if c.get('shape') != 'edge'])} 节点 / "
          f"{len([c for c in cells if c.get('shape') == 'edge'])} 边 / version={store_a.version(MEETING_ID)}")
    ok = evaluate(cells, skip_cmd=use_mock)
    print(f"\n===== 结论：{'全部通过' if ok else '存在未达标项，需继续优化提示词/代码'} =====")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
