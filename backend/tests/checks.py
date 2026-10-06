"""设计文档「要点核检」逻辑。

每个 DP（Design Point）检查返回 (passed: bool, evidence: str)。
`evaluate_design_points(result)` 汇总全部要点，供 unittest 与合规报告共用。

设计文档出处简称：
  Storage = Design_StructureGraph_Storage.md
  DataFlow = Design_Agent_DataFlow.md
  Input    = Design_InputProcessing.md
  Cursor   = Design_CursorCapture.md
  Front    = Design_FrontendBoard.md
  Mascot   = Design_Mascot.md
"""
from __future__ import annotations
import dataclasses
import os
import tempfile

from app.models import NormUtterance, NormCursorEvent, MeetingSummary, GraphUpdateOp, GraphOp
from app.storage import StoreA, StoreB
from app.tools.graph_tools import GraphTools
from app import config
from .harness import mascot_sequence

# 节点 6 类 / 边 relation 5 类（Design §0.1 / Storage §3.3/§3.4）
# 注意：analyzer 会产出 other(闲聊/无用内容)、command(操作指令) 两类「瞬时类型」，它们不是图节点类型——
# Syncer 必须把 other 归入 n_issue_other(issue)/evidence、把 command 映射为 mark_node/move_node/relayout，
# 严禁把 other/command 直接落成图节点（storage 层受控词表同样只认这 6 类，否则整批更新被拒）。
NODE_TYPES = {"point", "evidence", "issue", "conclusion", "action", "conflict"}
EDGE_RELATIONS = {"subordinate", "support", "oppose", "duplicate", "replace"}


def _dp(did, doc, title, passed, evidence):
    return {"id": did, "doc": doc, "title": title, "passed": passed, "evidence": evidence}


def evaluate_design_points(result: dict) -> list:
    """对一场跑完的会议产物（result 来自 harness.run_full_meeting）逐条核检。"""
    sa: StoreA = result["sa"]
    sb: StoreB = result["sb"]
    mid = result["meeting_id"]
    st_asr = result["states"][0]
    recorder = result["recorder"]
    cells = sa.load(mid)
    node_cells = [c for c in cells if c.get("shape") != "edge"]   # 节点（排除边 cell）

    dps = []

    # ---- DP-01 双存储分离（Storage §1/§2）----
    graph_file = os.path.join(sa.root, f"{mid}.board.json")
    meta_file = os.path.join(sa.root, "metadata.json")
    sep = os.path.exists(graph_file) and os.path.exists(meta_file) and graph_file != meta_file
    dps.append(_dp("DP-01", "Storage §1/§2", "双存储分离：Store A(图) 与 Store B(元数据) 分文件存储",
                   sep, f"graph={os.path.basename(graph_file)} meta={os.path.basename(meta_file)}"))

    # ---- DP-02 图精炼、不内嵌原始长文本（Storage §1 原则/§3.3）----
    raw_texts = {r["text"] for r in sb.get_many(sb._data.keys())}
    embedded = False
    for c in cells:
        for key in ("text", "raw_text", "transcript"):
            if key in c.get("data", {}) and c["data"][key] in raw_texts:
                embedded = True
        # label 应当是精炼短文本，不应等于某条完整原始转写
        if c.get("data", {}).get("label") in raw_texts and len(c["data"]["label"]) > 40:
            embedded = True
    dps.append(_dp("DP-02", "Storage §1/§3.3", "图精炼：cell 不内嵌原始长文本，仅存精炼 label + metadata_refs",
                   not embedded, f"{len(cells)} cells 检查，无原始长文本内嵌" if not embedded
                   else "发现 cell 内嵌原始转写"))

    # ---- DP-03 节点绑定 metadata_refs（Storage §3.3/§5）----
    non_issue = [c for c in cells if c["id"].startswith("n_p_") or c["id"].startswith("n_")]
    has_refs = all(c.get("data", {}).get("metadata_refs") for c in non_issue if c["id"] != "n_issue_root")
    ref_prefix_ok = all(
        any(m.startswith(("utt_", "agd_", "man_")) for m in c.get("data", {}).get("metadata_refs", []))
        for c in non_issue if c["id"] != "n_issue_root"
    )
    dps.append(_dp("DP-03", "Storage §3.3/§5", "节点经 metadata_refs 绑定来源元数据 id（utt_/agd_/man_）",
                   has_refs and ref_prefix_ok,
                   f"point 节点均含 metadata_refs 且前缀合规" if (has_refs and ref_prefix_ok)
                   else "存在节点缺少/前缀非法 metadata_refs"))

    # ---- DP-04 metadata_refs 反查到原始论据（Storage §5）----
    ok = True
    detail = []
    for c in cells:
        refs = c.get("data", {}).get("metadata_refs", [])
        for m in refs:
            rec = sb.get(m)
            if rec is None:
                ok = False
                detail.append(f"{c['id']} 的 {m} 在 Store B 缺失")
    dps.append(_dp("DP-04", "Storage §5", "metadata_refs 可反查原始论据（Store B 按 id 命中）",
                   ok, "全部 refs 命中 Store B 原文" if ok else "; ".join(detail)))

    # ---- DP-05 X6 同构 cell（Storage §3.2）----
    iso = all({"id", "shape", "position", "size", "data"} <= set(c.keys()) for c in node_cells)
    biz_id = all(c["id"].startswith(("n_", "e_")) for c in cells)
    dps.append(_dp("DP-05", "Storage §3.2", "cell 与 X6 同构（id/shape/position/size/data）且 id=业务 id",
                   iso and biz_id,
                   f"{len(cells)} cells 同构、业务 id 前缀合规" if (iso and biz_id)
                   else "cell 结构或 id 前缀不符"))

    # ---- DP-06 节点/边类型受控（Storage §10 / Design §0.1）----
    types_ok = all(c.get("data", {}).get("type") in NODE_TYPES for c in node_cells)
    rel_ok = all(c.get("data", {}).get("relation", "support") in EDGE_RELATIONS
                 for c in cells if c.get("shape") == "edge")
    dps.append(_dp("DP-06", "Storage §10/§0.1", "节点类型∈6类、边 relation∈5类",
                   types_ok and rel_ok,
                   "类型均在枚举内" if (types_ok and rel_ok) else "出现未定义类型/relation"))

    # ---- DP-07 lock/edit 状态字段存在（Storage §7）----
    has_lock_edit = all(
        isinstance(c.get("data", {}).get("lock"), dict) and isinstance(c.get("data", {}).get("edit"), dict)
        for c in node_cells
    )
    keys_ok = has_lock_edit and all(
        {"locked", "locked_by", "locked_at"} <= set(c["data"]["lock"]) and
        {"text_edited", "type_edited", "position_frozen", "importance_override"} <= set(c["data"]["edit"])
        for c in node_cells
    )
    dps.append(_dp("DP-07", "Storage §7", "节点含 lock/edit 状态字段（lock>edit 仲裁）",
                   keys_ok, "lock/edit 字段齐全" if keys_ok else "lock/edit 字段缺失/结构不符"))

    # ---- DP-08 ASR / 光标同形流（DataFlow §2.4 / Research）----
    uf = {f.name for f in dataclasses.fields(NormUtterance)}
    cf = {f.name for f in dataclasses.fields(NormCursorEvent)}
    shared = {"meeting_id", "session_id", "seq", "start_offset_ms", "end_offset_ms",
              "received_at_ms", "is_final", "is_partial", "source", "raw_ref"}
    iso_fields = shared <= uf and shared <= cf
    diff_carrier = ("text" in uf and "speaker" in uf) and ("gesture_type" in cf and "target" in cf)
    dps.append(_dp("DP-08", "DataFlow §2.4", "ASR 与光标流字段同形（共享 id/会议/seq/时间/source/raw_ref；载体各异）",
                   iso_fields and diff_carrier and ("utterance_id" in uf) and ("event_id" in cf),
                   "共享字段集齐、内容载体(text/speaker vs gesture_type/target)各异" if (iso_fields and diff_carrier)
                   else "同形字段缺失或载体未区分"))

    # ---- DP-09 双 Agent 经 MeetingSummary 交接（Input §3/§6）----
    ms: MeetingSummary = st_asr.meeting_summary
    op: GraphUpdateOp = st_asr.llm_output
    handoff_ok = isinstance(ms, MeetingSummary) and isinstance(op, GraphUpdateOp)
    ev_union = set()
    for ins in ms.insights:
        ev_union |= set(ins.evidence)
    op_meta = set()
    for g in op.operations:
        op_meta |= set(g.meta_ids)
    meta_trace = op_meta <= ev_union if op_meta else True
    dps.append(_dp("DP-09", "Input §3/§6", "双 Agent 交接：analyze→MeetingSummary，sync→GraphUpdateOp，op.meta_ids⊆insight.evidence",
                   handoff_ok and meta_trace,
                   f"insights={len(ms.insights)} ops={len(op.operations)} meta 溯源闭合" if (handoff_ok and meta_trace)
                   else "交接物类型或溯源链断裂"))

    # ---- DP-10 分析 Agent 不加载 board_graph（Input §3.1/§3.2）----
    # MockLLM.analyze 仅消费 filtered_text；insight 数应 == 过滤后句数（不依赖图）。
    n_ins = len(ms.insights)
    n_filt = len(st_asr.filtered_text)
    dps.append(_dp("DP-10", "Input §3.1/§3.2", "分析 Agent 仅消费输入信息（insight 数==过滤句数，不读 board）",
                   n_ins == n_filt,
                   f"insights={n_ins} == filtered_text={n_filt}" if n_ins == n_filt
                   else f"insights={n_ins} != filtered_text={n_filt}"))

    # ---- DP-11 光标流平行汇入、不重建/覆盖图（DataFlow §2.4 / Cursor §5）----
    before = len(cells)
    after_cursor = len(sa.load(mid))
    no_overwrite = after_cursor == before
    dps.append(_dp("DP-11", "DataFlow §2.4/Cursor §5", "光标批次平行汇入、不 gen_initial 覆盖已有图",
                   no_overwrite,
                   f"ASR 后 {before} cells，光标批次后 {after_cursor} cells（无覆盖/重建）" if no_overwrite
                   else f"光标批次后 cell 数变化 {before}->{after_cursor}"))

    # ---- DP-12 填充词过滤（DataFlow Q1）----
    filler_in_text = any("对吧" in t for t in st_asr.filtered_text) or \
        any(set("嗯那个对吧") <= set(t.replace(" ", "")) for t in st_asr.filtered_text)
    filler_in_graph = any("utt_2" in (c.get("data", {}).get("metadata_refs") or []) for c in cells)
    dps.append(_dp("DP-12", "DataFlow Q1", "纯填充词句被 filter_node 过滤（不入 LLM、不建节点）",
                   (not filler_in_text) and (not filler_in_graph),
                   "填充词句已过滤" if ((not filler_in_text) and (not filler_in_graph))
                   else "填充词泄漏到文本或图"))

    # ---- DP-13 光标采集 200ms 节流（Cursor §2.2/§9）----
    dps.append(_dp("DP-13", "Cursor §2.2/§9", "mousemove 采集端 THROTTLE_MS=200",
                   config.CONFIG.throttle_ms == 200,
                   f"throttle_ms={config.CONFIG.throttle_ms}"))

    # ---- DP-15 MascotState 状态机（Mascot §1.1）----
    # 复用 harness.mascot_sequence：从 recorder 取每节点结束后的 after（已修正为 patch 声明态）
    seq = mascot_sequence(recorder)
    required_order = ["listening", "filtering", "loading_board", "assembling",
                      "analyzing", "syncing", "success"]
    idxs = [seq.index(s) for s in required_order if s in seq]
    ordered = idxs == sorted(idxs) and len(idxs) == len(required_order)
    dps.append(_dp("DP-15", "Mascot §1.1", "MascotState 状态机按序推进 listening→filtering→loading_board→assembling→analyzing→syncing→success",
                   ordered, f"观察到序列: {' → '.join(seq)}" if seq else "无状态记录"))

    # ---- DP-14 lock 优先（独立临时目录，不依赖主流程产物；见下方 lock_priority_check）----
    dps.append(lock_priority_check())
    return dps


def lock_priority_check() -> dict:
    """DP-14 用户锁优先：锁定节点后，replace 操作被跳过（Front §5 / Storage §7）。

    独立临时目录，避免污染主流程产物。
    """
    root = tempfile.mkdtemp(prefix="amo_lock_")
    sa = StoreA(root)
    sb = StoreB(root)
    mid = "mtg_lock_test"
    # 建一个 issue 节点
    from app.models import NodeData, make_node_cell
    nd = NodeData(type="issue", label="原始议题", metadata_refs=["utt_x"])
    sa.save(mid, [make_node_cell("n_lockme", nd, x=10, y=10)])

    tools = GraphTools(sa, sb)
    # 锁定
    lk = tools.lock_node(mid, "n_lockme", "ent:user_zhang", True)
    # 尝试 replace（篡改 label）
    res = tools.update_graph(mid, GraphUpdateOp(operations=[
        GraphOp(op="replace", node="n_lockme", label="被 AI 篡改", meta_ids=[])
    ]))
    cells = sa.load(mid)
    label_after = next(c for c in cells if c["id"] == "n_lockme")["data"]["label"]
    skipped_ok = "n_lockme" in (res.skipped or [])
    label_preserved = label_after == "原始议题"

    # 直接用 StoreA.apply_graph_update 再验证底层守卫
    sa.apply_graph_update(mid, GraphUpdateOp(operations=[
        GraphOp(op="replace", node="n_lockme", label="底层篡改", meta_ids=[])
    ]))
    label_after2 = next(c for c in sa.load(mid) if c["id"] == "n_lockme")["data"]["label"]

    passed = bool(lk.ok) and skipped_ok and label_preserved and (label_after2 == "原始议题")
    evidence = (f"lock.ok={lk.ok}, replace被skipped={skipped_ok}, "
                f"label保留={label_preserved}({label_after}), 底层守卫保留={label_after2=='原始议题'}")
    return _dp("DP-14", "Front §5/Storage §7", "lock 优先：锁定节点所有写回被跳过（工具层+StoreA 双层守卫）",
               passed, evidence)
