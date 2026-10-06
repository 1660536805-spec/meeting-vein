"""功能点单元测试（stdlib unittest，无需联网）。

覆盖 `checks.evaluate_design_points`（DP-01..DP-15）未直接触及的「已实现功能行为」：
  - StoreA.apply_graph_update 五类算子（add_node / link / merge_as_duplicate /
    replace / set_importance）及其 lock/edit 仲裁细节；
  - StoreB 元数据反查（缺失返回 None / 批量过滤缺失 id / 往返一致）；
  - Adapter 归一化映射（腾讯 asr-push / FunASR / X6 事件）字段正确性；
  - Orchestrator 路由（_route_init：首会 gen_initial 建议题根，已存在则 load_board）。

这些测试直接作用于模块 API，不依赖整场会议编排，与 DP 合规核检互补。
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures import MEETING_ID
from app.storage import StoreA, StoreB
from app.models import NodeData, GraphUpdateOp, GraphOp, make_node_cell
from app.tools.graph_tools import GraphTools
from app import adapters
from app.orchestrator import BoardAgent, AgentState
from app.llm import MockLLM


class TestStoreAGraphOps(unittest.TestCase):
    """StoreA.apply_graph_update 五类算子 + lock/edit 仲裁细节。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="amo_fstore_")
        self.sa = StoreA(self.root)

    def _seed_issue(self, mid=MEETING_ID):
        nd = NodeData(type="issue", label="议题", metadata_refs=[])
        self.sa.save(mid, [make_node_cell("n_issue_root", nd, x=10, y=10)])

    # —— add_node ——
    def test_add_node_creates_cell(self):
        self._seed_issue()
        op = GraphUpdateOp(operations=[GraphOp(
            op="add_node", node="n_p_x", node_type="point", label="观点X", meta_ids=["utt_1"])])
        by = {c["id"]: c for c in self.sa.apply_graph_update(MEETING_ID, op)}
        self.assertIn("n_p_x", by)
        self.assertEqual(by["n_p_x"]["data"]["type"], "point")
        self.assertEqual(by["n_p_x"]["data"]["label"], "观点X")
        self.assertEqual(by["n_p_x"]["data"]["metadata_refs"], ["utt_1"])
        self.assertEqual(by["n_p_x"]["data"]["mention_count"], 1)

    def test_add_node_duplicate_merges(self):
        self._seed_issue()
        op = GraphUpdateOp(operations=[
            GraphOp(op="add_node", node="n_p_x", node_type="point", label="观点X", meta_ids=["utt_1"]),
            GraphOp(op="add_node", node="n_p_x", node_type="point", label="观点X", meta_ids=["utt_2"]),
        ])
        by = {c["id"]: c for c in self.sa.apply_graph_update(MEETING_ID, op)}
        # 同 id 重复 add → 合并：mention_count=2，metadata_refs 去重累加
        self.assertEqual(by["n_p_x"]["data"]["mention_count"], 2)
        self.assertEqual(by["n_p_x"]["data"]["metadata_refs"], ["utt_1", "utt_2"])

    # —— link ——
    def test_link_creates_edge(self):
        self._seed_issue()
        op = GraphUpdateOp(operations=[
            GraphOp(op="add_node", node="n_p_x", node_type="point", label="观点X", meta_ids=["utt_1"]),
            GraphOp(op="link", source="n_p_x", target="n_issue_root", relation="support"),
        ])
        cells = self.sa.apply_graph_update(MEETING_ID, op)
        edges = [c for c in cells if c.get("shape") == "edge"]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["data"]["relation"], "support")
        self.assertEqual(edges[0]["source"]["cell"], "n_p_x")
        self.assertEqual(edges[0]["target"]["cell"], "n_issue_root")

    # —— merge_as_duplicate ——
    def test_merge_as_duplicate_removes_child(self):
        self._seed_issue()
        op = GraphUpdateOp(operations=[
            GraphOp(op="add_node", node="n_p_a", node_type="point", label="A", meta_ids=["utt_1"]),
            GraphOp(op="add_node", node="n_p_b", node_type="point", label="B", meta_ids=["utt_2"]),
            GraphOp(op="merge_as_duplicate", parent="n_p_a", node="n_p_b", meta_ids=["utt_3"]),
        ])
        by = {c["id"]: c for c in self.sa.apply_graph_update(MEETING_ID, op)}
        self.assertNotIn("n_p_b", by, "子节点应被合并删除")
        self.assertIn("n_p_a", by)
        # 父节点 version 自增，metadata_refs 累加 utt_3
        self.assertGreaterEqual(by["n_p_a"]["data"]["version"], 2)
        self.assertIn("utt_3", by["n_p_a"]["data"]["metadata_refs"])

    # —— replace + lock ——
    def test_replace_blocked_by_lock(self):
        self._seed_issue()
        nd = NodeData(type="point", label="原始", metadata_refs=["utt_1"])
        self.sa.save(MEETING_ID, [make_node_cell("n_p_x", nd, x=1, y=1)])
        sb = StoreB(self.root)
        tools = GraphTools(self.sa, sb)
        tools.lock_node(MEETING_ID, "n_p_x", "ent:user_zhang", True)
        res = self.sa.apply_graph_update(MEETING_ID, GraphUpdateOp(operations=[
            GraphOp(op="replace", node="n_p_x", label="被篡改", meta_ids=[])]))
        by = {c["id"]: c for c in res}
        self.assertEqual(by["n_p_x"]["data"]["label"], "原始", "锁定节点 replace 被跳过")

    def test_replace_respects_text_edited_flag(self):
        self._seed_issue()
        nd = NodeData(type="point", label="原始", metadata_refs=["utt_1"],
                      edit={"text_edited": True, "type_edited": False,
                            "position_frozen": False, "importance_override": False})
        self.sa.save(MEETING_ID, [make_node_cell("n_p_x", nd, x=1, y=1)])
        res = self.sa.apply_graph_update(MEETING_ID, GraphUpdateOp(operations=[
            GraphOp(op="replace", node="n_p_x", label="新文本", node_type=None, meta_ids=[])]))
        by = {c["id"]: c for c in res}
        self.assertEqual(by["n_p_x"]["data"]["label"], "原始",
                         "text_edited=True 时 replace 不覆盖 label（即便未锁）")

    def test_replace_respects_type_edited_flag(self):
        self._seed_issue()
        nd = NodeData(type="point", label="原始", metadata_refs=["utt_1"],
                      edit={"text_edited": False, "type_edited": True,
                            "position_frozen": False, "importance_override": False})
        self.sa.save(MEETING_ID, [make_node_cell("n_p_x", nd, x=1, y=1)])
        res = self.sa.apply_graph_update(MEETING_ID, GraphUpdateOp(operations=[
            GraphOp(op="replace", node="n_p_x", label=None, node_type="evidence", meta_ids=[])]))
        by = {c["id"]: c for c in res}
        self.assertEqual(by["n_p_x"]["data"]["type"], "point",
                         "type_edited=True 时 replace 不改 type")

    def test_replace_appends_metadata_refs(self):
        self._seed_issue()
        nd = NodeData(type="point", label="原始", metadata_refs=["utt_1"])
        self.sa.save(MEETING_ID, [make_node_cell("n_p_x", nd, x=1, y=1)])
        res = self.sa.apply_graph_update(MEETING_ID, GraphUpdateOp(operations=[
            GraphOp(op="replace", node="n_p_x", label="原始", meta_ids=["utt_2"])]))
        by = {c["id"]: c for c in res}
        self.assertEqual(by["n_p_x"]["data"]["metadata_refs"], ["utt_1", "utt_2"])

    # —— set_importance（此前有 cell 未绑定 bug，已修）——
    def test_set_importance_applies_when_unlocked(self):
        self._seed_issue()
        nd = NodeData(type="point", label="原始", metadata_refs=["utt_1"],
                      importance={"level": "normal", "score": 0.0, "manual_override": False})
        self.sa.save(MEETING_ID, [make_node_cell("n_p_x", nd, x=1, y=1)])
        self.sa.apply_graph_update(MEETING_ID, GraphUpdateOp(operations=[
            GraphOp(op="set_importance", node="n_p_x", importance="high")]))
        by = {c["id"]: c for c in self.sa.load(MEETING_ID)}
        self.assertEqual(by["n_p_x"]["data"]["importance"]["level"], "high",
                         "未 override 时 AI 可设重要性")

    def test_set_importance_respects_override(self):
        self._seed_issue()
        # 关键：importance_override 在 edit 字典（非 importance 字典）
        nd = NodeData(type="point", label="原始", metadata_refs=["utt_1"],
                      importance={"level": "low", "score": 0.0, "manual_override": False},
                      edit={"text_edited": False, "type_edited": False,
                            "position_frozen": False, "importance_override": True})
        self.sa.save(MEETING_ID, [make_node_cell("n_p_x", nd, x=1, y=1)])
        self.sa.apply_graph_update(MEETING_ID, GraphUpdateOp(operations=[
            GraphOp(op="set_importance", node="n_p_x", importance="high")]))
        by = {c["id"]: c for c in self.sa.load(MEETING_ID)}
        self.assertEqual(by["n_p_x"]["data"]["importance"]["level"], "low",
                         "edit.importance_override=True 时 AI 的 set_importance 被拒绝")


class TestStoreBMeta(unittest.TestCase):
    """StoreB 元数据反查（Design_StructureGraph_Storage §2/§5）。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="amo_fmeta_")
        self.sb = StoreB(self.root)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.sb.get("utt_nonexist"))

    def test_get_many_filters_missing(self):
        self.sb.put("utt_1", {"meta_id": "utt_1", "text": "a"})
        got = self.sb.get_many(["utt_1", "utt_ghost"])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["text"], "a")

    def test_put_get_roundtrip(self):
        rec = {"meta_id": "utt_9", "kind": "utt", "text": "观点", "speaker_ref": "ent:u"}
        self.sb.put("utt_9", rec)
        self.assertEqual(self.sb.get("utt_9"), rec)


class TestAdapters(unittest.TestCase):
    """适配层归一化映射字段正确性（ASR_UnifiedSchema / Research_CursorIntent_Capture）。"""

    def test_from_tencent_asr_push(self):
        u = adapters.from_tencent_asr_push("mtg1", {
            "sid": "s1", "speaker": {"userid": "zhang", "nickname": "张三"},
            "content": {"text": "渠道自助分析", "translate": "x"}, "speech_time": 1000})
        self.assertEqual(u.utterance_id, "s1")
        self.assertEqual(u.speaker.speaker_ref, "ms:zhang")
        self.assertTrue(u.speaker.is_resolved)
        self.assertEqual(u.text, "渠道自助分析")
        self.assertTrue(u.is_final)

    def test_from_funasr_sentence_info(self):
        u = adapters.from_funasr_sentence_info(
            "mtg1", {"spk": "2", "sentence": "口径对齐", "start": 1.0, "end": 2.0})
        self.assertEqual(u.speaker.speaker_ref, "spk:2")
        self.assertFalse(u.speaker.is_resolved)
        self.assertEqual(u.text, "口径对齐")

    def test_from_x6_event(self):
        e = adapters.from_x6_event("mtg1", {
            "event_id": "c1", "gesture_type": "drag_node",
            "target": {"node_id": "n_issue_root"}, "x": 10, "y": 20,
            "actor": "local:user", "is_final": True})
        self.assertEqual(e.event_id, "c1")
        self.assertEqual(e.gesture_type, "drag_node")
        self.assertEqual(e.target.node_id, "n_issue_root")
        self.assertEqual(e.actor.user_ref, "local:user")
        self.assertTrue(e.is_final)

    def test_adapter_isomorphic_shared_fields(self):
        u = adapters.from_tencent_asr_push("mtg1", {"sid": "s", "speaker": {}, "content": {"text": "x"}})
        e = adapters.from_x6_event("mtg1", {"gesture_type": "hover"})
        shared = {"meeting_id", "session_id", "seq", "start_offset_ms", "end_offset_ms",
                  "received_at_ms", "is_final", "is_partial", "source", "raw_ref"}
        uf = set(u.__dataclass_fields__.keys())
        ef = set(e.__dataclass_fields__.keys())
        self.assertTrue(shared <= uf and shared <= ef, "ASR/光标流共享字段缺失")
        # 内容载体各异
        self.assertIn("text", uf) and self.assertIn("speaker", uf)
        self.assertIn("gesture_type", ef) and self.assertIn("target", ef)


class TestOrchestratorRouting(unittest.TestCase):
    """_route_init：首会 gen_initial（建议题根），已存在则 load_board（Design_CursorCapture 双流）。"""

    def _agent(self, root):
        return BoardAgent(StoreA(root), StoreB(root), MockLLM())

    def test_route_init_gen_initial_when_empty(self):
        root = tempfile.mkdtemp(prefix="amo_froute_")
        st = AgentState(meeting_id=MEETING_ID, meeting_title="新会")
        self.assertEqual(self._agent(root)._route_init(st), "gen_initial")

    def test_route_init_load_board_when_exists(self):
        root = tempfile.mkdtemp(prefix="amo_froute_")
        StoreA(root).save(MEETING_ID, [make_node_cell(
            "n_issue_root", NodeData(type="issue", label="议题", metadata_refs=[]), x=0, y=0)])
        st = AgentState(meeting_id=MEETING_ID)
        self.assertEqual(self._agent(root)._route_init(st), "load_board")

    def test_gen_initial_creates_issue_root_empty_refs(self):
        root = tempfile.mkdtemp(prefix="amo_froute_")
        agent = self._agent(root)
        st = AgentState(meeting_id=MEETING_ID, meeting_title="议题标题")
        asyncio.run(agent.gen_initial_node(st))          # 仅跑 gen_initial 节点
        cells = agent.store_a.load(MEETING_ID)
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]["id"], "n_issue_root")
        self.assertEqual(cells[0]["data"]["type"], "issue")
        self.assertEqual(cells[0]["data"]["metadata_refs"], [], "首会议题根无来源论据")


class TestUserCommandOps(unittest.TestCase):
    """用户指令类算子：mark_node（灰化/删除线）与 move_node（改挂父节点）。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="amo_fcmd_")
        self.sa = StoreA(self.root)

    def _seed_tree(self, mid=MEETING_ID):
        """root ← A ← B、root ← C；返回 cells。"""
        def nd(label, t="point"):
            return NodeData(type=t, label=label, metadata_refs=[])
        cells = [
            make_node_cell("n_issue_root", nd("议题", "issue"), 10, 10),
            make_node_cell("n_p_a", nd("观点A"), 10, 110),
            make_node_cell("n_p_b", nd("观点B"), 10, 210),
            make_node_cell("n_p_c", nd("观点C"), 240, 110),
            {"id": "e_root__a", "shape": "edge",
             "source": {"cell": "n_issue_root"}, "target": {"cell": "n_p_a"},
             "data": {"relation": "subordinate"}},
            {"id": "e_a__b", "shape": "edge",
             "source": {"cell": "n_p_a"}, "target": {"cell": "n_p_b"},
             "data": {"relation": "support"}},
            {"id": "e_root__c", "shape": "edge",
             "source": {"cell": "n_issue_root"}, "target": {"cell": "n_p_c"},
             "data": {"relation": "subordinate"}},
        ]
        self.sa.save(mid, cells)
        return cells

    # —— mark_node ——
    def test_mark_gray_sets_cmd(self):
        self._seed_tree()
        op = GraphUpdateOp(operations=[GraphOp(
            op="mark_node", node="n_p_a", mark="gray", reason="暂不考虑")])
        by = {c["id"]: c for c in self.sa.apply_graph_update(MEETING_ID, op)}
        cmd = by["n_p_a"]["data"]["cmd"]
        self.assertEqual(cmd["mark"], "gray")
        self.assertEqual(cmd["reason"], "暂不考虑")
        self.assertEqual(cmd["by"], "user")
        self.assertIn("n_p_a", by)                        # 节点保留不删除

    def test_mark_strike_sets_cmd(self):
        self._seed_tree()
        op = GraphUpdateOp(operations=[GraphOp(
            op="mark_node", node="n_p_b", mark="strike", reason="已废弃")])
        by = {c["id"]: c for c in self.sa.apply_graph_update(MEETING_ID, op)}
        self.assertEqual(by["n_p_b"]["data"]["cmd"]["mark"], "strike")

    def test_mark_locked_node_skipped(self):
        self._seed_tree()
        self.sa.apply_user_operation(MEETING_ID, "n_p_a", "lock", {"locked": True})
        op = GraphUpdateOp(operations=[GraphOp(
            op="mark_node", node="n_p_a", mark="gray", reason="x")])
        by = {c["id"]: c for c in self.sa.apply_graph_update(MEETING_ID, op)}
        self.assertNotIn("cmd", by["n_p_a"]["data"])

    def test_mark_unknown_mark_ignored(self):
        self._seed_tree()
        op = GraphUpdateOp(operations=[GraphOp(
            op="mark_node", node="n_p_a", mark="hide", reason="x")])
        by = {c["id"]: c for c in self.sa.apply_graph_update(MEETING_ID, op)}
        self.assertNotIn("cmd", by["n_p_a"]["data"])

    def test_mark_missing_node_noop(self):
        self._seed_tree()
        op = GraphUpdateOp(operations=[GraphOp(
            op="mark_node", node="n_missing", mark="gray", reason="x")])
        by = {c["id"]: c for c in self.sa.apply_graph_update(MEETING_ID, op)}
        self.assertNotIn("cmd", by["n_p_a"]["data"])

    # —— move_node ——
    def test_move_reparents_node(self):
        self._seed_tree()
        # B 原挂在 A 下 → 移动到 C 下
        op = GraphUpdateOp(operations=[GraphOp(
            op="move_node", node="n_p_b", parent="n_p_c", reason="归组到C")])
        cells = self.sa.apply_graph_update(MEETING_ID, op)
        by = {c["id"]: c for c in cells}
        edge = by["e_a__b"]
        self.assertEqual(edge["source"]["cell"], "n_p_c")     # 父边 source 改写
        self.assertEqual(edge["target"]["cell"], "n_p_b")
        self.assertEqual(edge["data"]["relation"], "subordinate")
        self.assertEqual(by["n_p_b"]["data"]["cmd"]["mark"], "move")
        self.assertEqual(by["n_p_b"]["data"]["cmd"]["reason"], "归组到C")

    def test_move_rejects_cycle(self):
        self._seed_tree()
        # A 的父边指向 root，B 在 A 下 → 把 A 移到 B 下会成环，应被拒绝
        op = GraphUpdateOp(operations=[GraphOp(
            op="move_node", node="n_p_a", parent="n_p_b", reason="x")])
        cells = self.sa.apply_graph_update(MEETING_ID, op)
        by = {c["id"]: c for c in cells}
        self.assertEqual(by["e_root__a"]["source"]["cell"], "n_issue_root")
        self.assertNotIn("cmd", by["n_p_a"]["data"])

    def test_move_rejects_root_and_self(self):
        self._seed_tree()
        op = GraphUpdateOp(operations=[
            GraphOp(op="move_node", node="n_issue_root", parent="n_p_a", reason="x"),
            GraphOp(op="move_node", node="n_p_a", parent="n_p_a", reason="x"),
        ])
        cells = self.sa.apply_graph_update(MEETING_ID, op)
        by = {c["id"]: c for c in cells}
        self.assertEqual(by["e_root__a"]["source"]["cell"], "n_issue_root")
        self.assertNotIn("cmd", by["n_p_a"]["data"])

    def test_move_creates_edge_for_stray(self):
        self._seed_tree()
        nd = NodeData(type="point", label="游离D", metadata_refs=[])
        self.sa.apply_graph_update(MEETING_ID, GraphUpdateOp(operations=[
            GraphOp(op="add_node", node="n_p_d", node_type="point", label="游离D")]))
        op = GraphUpdateOp(operations=[GraphOp(
            op="move_node", node="n_p_d", parent="n_issue_root", reason="收编游离")])
        by = {c["id"]: c for c in self.sa.apply_graph_update(MEETING_ID, op)}
        edge = by.get("e_n_issue_root__n_p_d")
        self.assertIsNotNone(edge)
        self.assertEqual(edge["source"]["cell"], "n_issue_root")
        self.assertEqual(by["n_p_d"]["data"]["cmd"]["mark"], "move")

    def test_move_to_locked_parent_skipped(self):
        self._seed_tree()
        self.sa.apply_user_operation(MEETING_ID, "n_p_c", "lock", {"locked": True})
        op = GraphUpdateOp(operations=[GraphOp(
            op="move_node", node="n_p_b", parent="n_p_c", reason="x")])
        cells = self.sa.apply_graph_update(MEETING_ID, op)
        by = {c["id"]: c for c in cells}
        self.assertEqual(by["e_a__b"]["source"]["cell"], "n_p_a")   # 未被改写
        self.assertNotIn("cmd", by["n_p_b"]["data"])


class TestSkillProgressiveLoad(unittest.TestCase):
    """x6-graph-ops 技能包渐进注入：L0 常驻 + L1 按场景（边/cmd/大图）命中追加。"""

    def setUp(self):
        from app import skill_loader
        self.sl = skill_loader
        self.sl._SKILL_MD = None                    # 每用例重置缓存，保证真实读文件

    def _node(self, nid, data=None):
        return make_node_cell(nid, NodeData(type="point", label=nid, metadata_refs=[]),
                              x=0, y=0) if data is None else \
               {"id": nid, "shape": "rect", "x": 0, "y": 0, "width": 10, "height": 10,
                "data": data}

    def test_l0_always_present(self):
        """空图也注入 L0 常驻段：含渲染契约关键词，不含 L1 标题。"""
        s = self.sl.load_syncer_skill([])
        self.assertIn("L0 常驻", s)
        self.assertIn("≤40 字符", s)
        self.assertIn("subordinate/support/oppose", s)
        self.assertNotIn("L1 按需", s)

    def test_l1_edge_triggered_by_existing_edges(self):
        """图含边 → 注入 [边] 条目（用户手动弧线保护）。"""
        cells = [self._node("a"), self._node("b"),
                 {"id": "e1", "shape": "edge", "source": {"cell": "a"},
                  "target": {"cell": "b"}, "data": {"relation": "support"}}]
        s = self.sl.load_syncer_skill(cells)
        self.assertIn("L1 按需", s)
        self.assertIn("vertices", s)

    def test_l1_cmd_triggered_by_cmd_data(self):
        """节点带 data.cmd → 注入 [cmd] 条目（灰化/删除线契约）。"""
        cells = [self._node("a", {"type": "point", "label": "a",
                                  "cmd": {"mark": "gray", "reason": "r", "by": "user"}})]
        s = self.sl.load_syncer_skill(cells)
        self.assertIn("L1 按需", s)
        self.assertIn("灰化", s)

    def test_l1_large_graph_triggered_over_threshold(self):
        """节点数超过 MAX_LLM_NODES → 注入 [大图] 截断警示。"""
        cells = [self._node(f"n{i}") for i in range(self.sl.prompts.MAX_LLM_NODES + 1)]
        s = self.sl.load_syncer_skill(cells)
        self.assertIn("L1 按需", s)
        self.assertIn("截断", s)

    def test_l1_not_triggered_below_thresholds(self):
        """少量节点、无边、无 cmd → 只有 L0。"""
        s = self.sl.load_syncer_skill([self._node("a"), self._node("b")])
        self.assertNotIn("L1 按需", s)

    def test_skill_file_cached(self):
        """SKILL.md 仅读取一次：重置缓存后连续两次加载只触发一次文件 IO。"""
        from unittest.mock import patch
        calls = {"n": 0}
        real_read = self.sl._read_text

        def counting(path):
            calls["n"] += 1
            return real_read(path)

        with patch.object(self.sl, "_read_text", counting):
            self.sl._SKILL_MD = None
            self.sl.load_syncer_skill([])
            self.sl.load_syncer_skill([])
        self.assertEqual(calls["n"], 1)

    def test_missing_skill_file_returns_empty(self):
        """SKILL.md 读取失败 → 返回空串（不注入、不阻断推理）。"""
        from unittest.mock import patch
        with patch.object(self.sl, "_read_text", side_effect=OSError("boom")):
            self.sl._SKILL_MD = None
            self.assertEqual(self.sl.load_syncer_skill([]), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
