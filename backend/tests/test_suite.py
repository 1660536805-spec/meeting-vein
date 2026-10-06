"""端到端测试套件（stdlib unittest，无需安装 pytest）。

运行：backend> ..\\.venv\\Scripts\\python.exe -m tests.test_suite
或：    backend> ..\.venv\Scripts\python.exe tests/test_suite.py
"""
import os
import sys
import tempfile
import unittest

# 允许以脚本方式直接运行（python tests/test_suite.py）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures import (  # noqa: E402
    make_asr_utterances, make_cursor_events, MEETING_ID, MEETING_TITLE,
)
from tests import harness  # noqa: E402
from tests import checks  # noqa: E402
from tests.functional_tests import (  # noqa: E402
    TestStoreAGraphOps, TestStoreBMeta, TestAdapters, TestOrchestratorRouting,
    TestUserCommandOps, TestSkillProgressiveLoad,
)


class TestE2EPipeline(unittest.TestCase):
    """整场会议端到端：mock 输入 → 双流 → 双 Agent → 双存储。"""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="amo_e2e_")
        cls.result = harness.run_full_meeting(
            cls.root, MEETING_ID, MEETING_TITLE,
            make_asr_utterances(), make_cursor_events(),
        )

    # ---- 流程正确性 ----
    def test_asr_batch_produces_board(self):
        cells = self.result["sa"].load(MEETING_ID)
        self.assertGreaterEqual(len(cells), 1, "ASR 批次应至少产出初始 issue 节点")

    def test_meeting_summary_and_graphop_exist(self):
        st = self.result["states"][0]
        self.assertIsNotNone(st.meeting_summary)
        self.assertIsNotNone(st.llm_output)
        self.assertGreater(len(st.llm_output.operations), 0, "sync 应产出 GraphUpdateOp")

    def test_cursor_batch_does_not_overwrite(self):
        before = len(self.result["sa"].load(MEETING_ID))
        # 光标批次已在 setUpClass 跑完；此处复跑一次确认幂等不重建
        r2 = harness.run_full_meeting(
            self.root + "_c2", MEETING_ID, MEETING_TITLE,
            make_asr_utterances(), make_cursor_events(),
        )
        after = len(r2["sa"].load(MEETING_ID))
        self.assertEqual(after, before, "光标批次不应重建/覆盖关系图")


class TestDesignCompliance(unittest.TestCase):
    """逐条核检设计文档要点（DP-01 … DP-15 + DP-14）。"""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="amo_dp_")
        cls.result = harness.run_full_meeting(
            cls.root, MEETING_ID, MEETING_TITLE,
            make_asr_utterances(), make_cursor_events(),
        )
        cls.dps = checks.evaluate_design_points(cls.result)
        cls.by_id = {d["id"]: d for d in cls.dps}

    def _check(self, did):
        d = self.by_id[did]
        self.assertTrue(d["passed"], f"{did} 未通过：{d['evidence']}")

    def test_DP01_dual_storage(self):
        self._check("DP-01")

    def test_DP02_graph_refinement(self):
        self._check("DP-02")

    def test_DP03_metadata_refs(self):
        self._check("DP-03")

    def test_DP04_reverse_lookup(self):
        self._check("DP-04")

    def test_DP05_x6_isomorphic(self):
        self._check("DP-05")

    def test_DP06_controlled_types(self):
        self._check("DP-06")

    def test_DP07_lock_edit_fields(self):
        self._check("DP-07")

    def test_DP08_asr_cursor_isomorphic(self):
        self._check("DP-08")

    def test_DP09_dual_agent_handoff(self):
        self._check("DP-09")

    def test_DP10_analyzer_no_board(self):
        self._check("DP-10")

    def test_DP11_cursor_no_overwrite(self):
        self._check("DP-11")

    def test_DP12_filler_filtered(self):
        self._check("DP-12")

    def test_DP13_cursor_throttle_200(self):
        self._check("DP-13")

    def test_DP15_mascot_state_machine(self):
        self._check("DP-15")

    def test_DP14_lock_priority(self):
        self._check("DP-14")


class TestExpertPrompts(unittest.TestCase):
    """会议专家技能包（prompts.yaml experts）：按专家追加解析/绘图提示词。"""

    def test_all_experts_registered(self):
        from app import prompts
        self.assertEqual(set(prompts.EXPERTS), {"general", "debate", "proposal", "task"})
        for entry in prompts.EXPERTS.values():
            self.assertTrue(str(entry.get("label", "")).strip())

    def test_debate_addendum_carries_oppose_rule(self):
        from app import prompts
        addendum = prompts.expert_addendum("debate")
        self.assertIn("辩论", addendum)
        self.assertIn("oppose", addendum)

    def test_unknown_or_empty_expert_falls_back_to_empty(self):
        from app import prompts
        self.assertEqual(prompts.expert_addendum("general"), "")
        self.assertEqual(prompts.expert_addendum(None), "")
        self.assertEqual(prompts.expert_addendum("no_such_expert"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
