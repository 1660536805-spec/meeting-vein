"""协同办公平台 V2.0 项目周会 端到端结构测试。

读 tests/500step.txt（「发言人：内容」逐字稿，350 句、8 位发言人）→ 逐句驱动双 Agent 管线
（filter→analyze→sync→update）→ 校验最终看板图结构是否符合「技术项目周会」的期望形态。

运行（backend 目录）：
    ..\\.venv\\Scripts\\python.exe tests/project_e2e.py             # 走 .env 配置的真实 LLM
    ..\\.venv\\Scripts\\python.exe tests/project_e2e.py --mock      # 强制 MockLLM（仅验链路/方向）
    ..\\.venv\\Scripts\\python.exe tests/project_e2e.py --limit 40  # 只跑前 40 句（快速冒烟）

校验目标（对应「技术项目周会」期望图形态）：
  C1. n_issue_root 为议题根，label = 会议标题「协同办公平台 V2.0 项目周会」
  C2. 根下 ≥2 个子节点（议题分组：需求变更 / 进度 / 权限 / 测试 / 上线等）
  C3. 全图 ≥1 个 action 节点（行动项：整理文档 / 提交脚本 / 排期等）
  C4. 全图 ≥1 个 conclusion 节点（会议结论 / 敲定事项）
  C5. 全部边方向 source 为 target 的祖先（父→子，禁止子→父/兄弟互指）
  C6. 无游离节点（所有节点都可从根到达）
  C7. 所有节点 metadata_refs 均可反查 Store B 原始论据
  C8. 节点类型∈6类、边 relation∈5类（受控枚举）
  C9. 主题覆盖：逐字稿的关键议题在节点 label 中均有体现（分组各自命中其一）
  C10.发言归属可溯源：存在节点，其 metadata_refs 反查到 Store B 记录并携带非空 speaker_ref
      （注：节点 data.speaker_ref 在当前管线中不经 GraphOp 传递，归属信息以 Store B 记录为准）
输出：树形大纲（含 relation 与 speaker）+ 各项 PASS/FAIL；全部通过退出码 0，否则 1。
"""
from __future__ import annotations
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MEETING_ID = "mtg_project"
MEETING_TITLE = "协同办公平台 V2.0 项目周会"
PROJECT_TXT = Path(__file__).with_name("500step.txt")

# 节点 6 类 / 边 relation 5 类（Design §0.1 / Storage §3.3/§3.4）
NODE_TYPES = {"point", "evidence", "issue", "conclusion", "action", "conflict"}
EDGE_RELATIONS = {"subordinate", "support", "oppose", "duplicate", "replace"}

# 逐字稿的关键议题分组：每组至少命中其一关键词（出现在任意节点 label 中）
TOPIC_GROUPS = {
    "需求变更": ["抄送", "已读", "未读", "文件上传", "上传校验", "格式校验", "校验"],
    "整体进度": ["进度", "完成度", "联调"],
    "权限风险": ["权限", "越权", "角色", "菜单"],
    "测试安排": ["测试", "用例", "压测", "回归"],
    "上线发布": ["上线", "灰度", "回滚", "部署", "发布"],
    "运维监控": ["监控", "告警", "观察期", "值班"],
    "缺陷与风险": ["缺陷", "风险", "迁移", "应急预案", "演练"],
}

LINE_RE = re.compile(r"^([^：:]{1,20})[：:]\s*(.+)$")


def parse_transcript(path: Path):
    """解析「发言人：内容」行 → [(speaker, text)]；续行并入上一条。"""
    turns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        m = LINE_RE.match(line)
        if m:
            turns.append((m.group(1).strip(), m.group(2).strip()))
        elif turns:                                        # 无冒号续行并入上一条
            turns[-1] = (turns[-1][0], turns[-1][1] + " " + line)
    return turns


async def run_project(use_mock: bool, limit: int | None):
    os.environ.setdefault("AMO_DATA_ROOT", tempfile.mkdtemp(prefix="amo_project_"))
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

    turns = parse_transcript(PROJECT_TXT)
    if limit:
        turns = turns[:limit]
    print(f"[input] {len(turns)} 句发言（{PROJECT_TXT.name}）")

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
        if (i + 1) % 10 == 0 or i == len(turns) - 1 or err:
            print(f"  batch {i + 1}/{len(turns)} [{speaker}] {text[:24]}…"
                  + ("（LLM 失败，待补跑）" if err else ""))
        if err:
            failed.append((i, speaker, text))
    if failed:                                             # 失败批次补跑一轮（限流恢复后整合进来）
        print(f"[retry] 补跑 {len(failed)} 个失败批次")
        for i, speaker, text in failed:
            err = await drive(i, speaker, text)
            print(f"  retry [{speaker}] {text[:24]}… " + ("（仍失败，跳过）" if err else "ok"))
    return store_a, store_b


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
            parent_of.setdefault(e["t"], e["s"])            # 多父取首条（树状约束）
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


def _type(nodes: dict, nid: str) -> str:
    return (nodes.get(nid, {}).get("data") or {}).get("type")


def evaluate(cells: list, store_b=None) -> bool:
    nodes, edges, children, parent_of, root = build_tree(cells)

    rel_of: dict = {}
    for e in edges:
        rel_of[(e["s"], e["t"])] = e["rel"]

    def speakers_of(nid) -> list:
        """节点归属发言人：经 metadata_refs 反查 Store B 记录的非空 speaker_ref。"""
        out = []
        for m in (nodes[nid].get("data") or {}).get("metadata_refs") or []:
            rec = store_b.get(m) if store_b is not None else None
            spk = (rec or {}).get("speaker_ref")
            if spk and spk not in out:
                out.append(spk)
        return out

    print("\n===== 图结构大纲 =====")

    def walk(nid, depth, seen):
        d = nodes[nid].get("data", {})
        spk = ",".join(speakers_of(nid)) or "-"
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

    # C1 议题根
    root_label = (nodes.get(root, {}).get("data") or {}).get("label")
    ok = bool(root) and _type(nodes, root) == "issue" and root_label == MEETING_TITLE
    results.append((ok, f"C1 议题根存在且为 issue，label={root_label!r}"))

    # C2 根下议题分组 ≥2
    root_kids = children.get(root, []) if root else []
    results.append((len(root_kids) >= 2,
                    f"C2 根下子节点 ≥2（实际 {len(root_kids)}："
                    f"{[ (nodes[k].get('data') or {}).get('label') for k in root_kids ]}）"))

    # C3 行动项
    actions = [nid for nid in nodes if _type(nodes, nid) == "action"]
    results.append((len(actions) >= 1,
                    f"C3 action 节点 ≥1（实际 {len(actions)}："
                    f"{[ (nodes[k].get('data') or {}).get('label') for k in actions ][:5]}）"))

    # C4 结论
    conclusions = [nid for nid in nodes if _type(nodes, nid) == "conclusion"]
    results.append((len(conclusions) >= 1,
                    f"C4 conclusion 节点 ≥1（实际 {len(conclusions)}："
                    f"{[ (nodes[k].get('data') or {}).get('label') for k in conclusions ][:5]}）"))

    # C5 边方向
    anc_ok = True
    if root:
        reach = {nid: descendants(children, nid) for nid in nodes}
        for e in edges:
            if e["s"] not in nodes or e["t"] not in nodes:
                continue
            if e["t"] not in reach.get(e["s"], set()):
                anc_ok = False
                print(f"  [方向违规] {e['s']} -{e['rel']}-> {e['t']}（source 不是 target 的祖先）")
    results.append((anc_ok, "C5 全部边方向 source 为 target 祖先（父→子）"))

    # C6 游离节点
    reachable = (descendants(children, root) | {root}) if root else set()
    stray = [nid for nid in nodes if nid not in reachable]
    results.append((not stray, f"C6 无游离节点（实际 {'无' if not stray else stray}）"))

    # C7 metadata_refs 反查
    refs_ok = True
    if store_b is not None:
        miss = []
        for nid in nodes:
            for m in (nodes[nid].get("data") or {}).get("metadata_refs") or []:
                if store_b.get(m) is None:
                    refs_ok = False
                    miss.append(f"{nid}→{m}")
        results.append((refs_ok, f"C7 metadata_refs 全部可反查 Store B"
                                 f"（{'全部命中' if refs_ok else '缺失 '+' ; '.join(miss[:5])}）"))

    # C8 受控枚举
    types_ok = all(_type(nodes, nid) in NODE_TYPES for nid in nodes)
    rel_ok = all(e["rel"] in EDGE_RELATIONS for e in edges)
    bad_types = sorted({_type(nodes, nid) for nid in nodes if _type(nodes, nid) not in NODE_TYPES})
    bad_rels = sorted({e["rel"] for e in edges if e["rel"] not in EDGE_RELATIONS})
    results.append((types_ok and rel_ok,
                    f"C8 节点类型∈6类、边 relation∈5类"
                    f"（越界类型={bad_types or '无'} 越界relation={bad_rels or '无'}）"))

    # C9 主题覆盖
    labels = " ".join((nodes[nid].get("data") or {}).get("label", "") for nid in nodes)
    group_hits = {g: [kw for kw in kws if kw in labels] for g, kws in TOPIC_GROUPS.items()}
    missing = [g for g, hits in group_hits.items() if not hits]
    results.append((not missing,
                    f"C9 议题覆盖 {len(TOPIC_GROUPS)} 组"
                    f"（{'全命中' if not missing else '缺失: ' + str(missing)}；命中={ {g: v[:2] for g, v in group_hits.items() if v} }）"))

    # C10 发言归属（经 Store B 反查）
    cli_nodes = [nid for nid in nodes
                 if any(str(s).startswith("cli:") for s in speakers_of(nid))]
    all_spk = sorted({s for nid in nodes for s in speakers_of(nid)})
    results.append((bool(cli_nodes),
                    f"C10 至少一个节点的论据可溯源到 cli:* 发言人"
                    f"（实际 {len(cli_nodes)} 个节点；发言人={all_spk}）"))

    print("\n===== 校验结果 =====")
    all_ok = True
    for ok, msg in results:
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    return all_ok


def main():
    use_mock = "--mock" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    store_a, store_b = asyncio.run(run_project(use_mock, limit))
    cells = store_a.load(MEETING_ID)
    print(f"\n[board] {len([c for c in cells if c.get('shape') != 'edge'])} 节点 / "
          f"{len([c for c in cells if c.get('shape') == 'edge'])} 边 / version={store_a.version(MEETING_ID)}")
    ok = evaluate(cells, store_b)
    print(f"\n===== 结论：{'全部通过' if ok else '存在未达标项，需继续优化提示词/代码'} =====")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
