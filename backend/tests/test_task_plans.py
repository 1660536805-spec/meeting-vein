import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from app import server
from app.llm import MockLLM, OpenAIClient
from app.storage import StoreA, StoreB
from app.task_plans import (PlanTask, TaskPlanStore, PlanConflict, task_order,
                            generate_plan, answer_question, validate_sources)
from app.minutes import render_minutes, review_before_close


def task(key="a", **kwargs):
    return PlanTask(id=key, title=key, source_node_id=key, **kwargs)


class TaskPlanDomainTests(unittest.TestCase):
    def test_parallel_dag_and_invalid_dependencies(self):
        self.assertEqual(task_order([task("a"), task("b"), task("c", depends_on=["a", "b"])]), ["a", "b", "c"])
        for tasks in ([task("a", depends_on=["a"])], [task("a", depends_on=["missing"])],
                      [task("a", depends_on=["b"]), task("b", depends_on=["a"])], [task("a"), task("a")]):
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                task_order(tasks)

    def test_model_scheduling_and_missing_date_proposals(self):
        cells = [{"id": key, "data": {"type": "action"}} for key in ("a", "b", "c")]
        chat = Mock(return_value=json.dumps({"tasks": [task("a", duration_days=2).model_dump(mode="json"),
                         task("b", depends_on=["a"]).model_dump(mode="json"), task("c").model_dump(mode="json")]}))
        plan = generate_plan(cells, [], {}, date(2026, 10, 8), chat)
        by_id = {t["id"]: t for t in plan["tasks"]}
        self.assertEqual(plan["mode"], "ai")
        self.assertEqual(by_id["a"]["due_date"], "2026-10-09")
        self.assertEqual(by_id["b"]["start_date"], "2026-10-10")
        self.assertEqual(by_id["c"]["start_date"], "2026-10-08")
        self.assertIn("dates", by_id["b"]["suggested_fields"])

    def test_extraction_requires_verbatim_current_meeting_source(self):
        utterances = [{"meta_id": "u1", "text": "小李负责写方案"}]
        valid = PlanTask(id="new", title="写方案", source_refs=["u1"], source_quote="小李负责写方案")
        validate_sources([valid], [], utterances)
        for changes in ({"source_refs": ["other_meeting"]}, {"source_quote": "编造的原话"}, {"source_node_id": "other_board"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_sources([valid.model_copy(update=changes)], [], utterances)

    def test_demo_does_not_invent_tasks_or_dependencies_and_keeps_explicit_date(self):
        cells = [{"id": "a", "data": {"type": "action", "label": "交方案", "owner": "小李", "deadline": "2026-10-10"}}]
        result = generate_plan(cells, [{"meta_id": "u1", "text": "随意讨论"}], {}, date(2026, 10, 8))
        self.assertEqual(result["mode"], "demo")
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(result["tasks"][0]["due_date"], "2026-10-10")
        self.assertEqual(result["tasks"][0]["depends_on"], [])

    def test_atomic_write_conflicts_restarts_and_failed_writes(self):
        with tempfile.TemporaryDirectory() as root:
            store = TaskPlanStore(root, "meeting")
            doc = store.write(0, lambda d: d.update(draft={"mode": "demo", "tasks": []}))
            self.assertEqual(TaskPlanStore(root, "meeting").read(), doc)
            with self.assertRaises(PlanConflict):
                store.write(0, lambda d: d.update(draft=None))
            with patch("app.task_plans._atomic_write_json", side_effect=OSError("disk full")), self.assertRaises(OSError):
                store.write(1, lambda d: d.update(draft=None))
            self.assertEqual(store.read(), doc)
            store.path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                store.read()

    def test_question_rejects_foreign_citations(self):
        plan = {"tasks": [task().model_dump(mode="json")]}
        chat = Mock(return_value=json.dumps({"answer": "伪造", "task_ids": ["a"], "source_refs": ["foreign"]}))
        with self.assertRaises(ValueError):
            answer_question("谁做？", plan, [], chat)
        chat.return_value = json.dumps({"answer": "当前会议原话说明分工。", "task_ids": ["a"], "source_refs": ["u1"]})
        result = answer_question("谁做？", plan, [{"meta_id": "u1", "text": "小李负责", "speaker_ref": "甲"}], chat)
        self.assertEqual(result["sources"][0]["text"], "小李负责")

    def test_action_nodes_are_included_in_minutes_and_close_review(self):
        cells = [{"id": "a", "shape": "rect", "data": {"type": "action", "label": "交方案"}}]
        self.assertIn("交方案（负责人", render_minutes("会", [], "ended", cells, []))
        self.assertEqual(review_before_close(cells)["incomplete_actions"][0]["missing"], ["负责人", "期限"])


class TaskPlanRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.boards = StoreA(self.temp.name + "/boards")
        self.sources = StoreB(self.temp.name + "/utterances")
        self.boards.create_meeting("meeting", "项目会", [], "")
        self.cells = [{"id": key, "shape": "rect", "data": {"type": "action", "label": key, "metadata_refs": ["u1"]}} for key in ("a", "b")]
        self.boards.save("meeting", self.cells)
        self.sources.put("u1", {"kind": "utt", "meeting_id": "meeting", "text": "先完成a，然后做b", "speaker_ref": "小李"})
        for name, value in (("store_a", self.boards), ("store_b", self.sources), ("agent", SimpleNamespace(llm=MockLLM()))):
            p = patch.object(server, name, value); p.start(); self.addCleanup(p.stop)
        # No lifespan: unrelated ASR/recovery jobs are outside this route fixture.
        self.client = TestClient(server.app)
        self.base = "/api/meetings/meeting/task-plan"

    def generate(self, version=0):
        response = self.client.post(self.base + "/generate", json={"expected_version": version, "reference_date": "2026-10-08"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def confirm(self, doc):
        tasks = doc["draft"]["tasks"]
        for t in tasks: t["owner"] = "小李"
        response = self.client.post(self.base + "/confirm", json={"expected_version": doc["version"], "tasks": tasks})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_full_lifecycle_replacement_draft_preserves_progress_and_exports(self):
        doc = self.generate()
        self.assertIsNone(doc["confirmed"])
        self.assertEqual(doc["draft"]["mode"], "demo")
        blocked = self.client.post(self.base + "/confirm", json={"expected_version": 1, "tasks": doc["draft"]["tasks"]})
        self.assertEqual(blocked.status_code, 422)
        doc = self.confirm(doc)
        changed = self.client.post(self.base + "/tasks/a/status", json={"expected_version": doc["version"], "status": "done"}).json()
        new_draft = self.generate(changed["version"])
        self.assertEqual(new_draft["confirmed"]["tasks"][0]["status"], "done")
        self.assertEqual(new_draft["draft"]["tasks"][0]["status"], "todo")
        final = self.confirm(new_draft)
        self.assertEqual(final["confirmed"]["tasks"][0]["status"], "done")
        exported = self.client.get(self.base + "/export")
        self.assertIn("已人工确认", exported.text)
        self.assertIn("已完成", exported.text)
        minutes = self.client.get("/api/meetings/meeting/minutes").text
        self.assertIn("会后任务计划", minutes)
        self.assertIn("负责人：小李；期限：2026-10-08；状态：已完成", minutes)
        self.assertEqual(self.boards.load("meeting")[0]["data"].get("owner"), None)
        self.assertIn("task_plan", self.client.get("/api/meetings/meeting/snapshot-file").json())
        self.assertEqual(len(self.boards.list_meetings()), 1)

    def test_cycles_date_conflicts_and_stale_edits_do_not_change_saved_draft(self):
        doc = self.generate()
        tasks = doc["draft"]["tasks"]
        tasks[0]["depends_on"] = ["b"]; tasks[1]["depends_on"] = ["a"]
        rejected = self.client.post(self.base + "/draft", json={"expected_version": 1, "tasks": tasks})
        self.assertEqual(rejected.status_code, 422)
        tasks[0]["depends_on"] = []; tasks[1]["depends_on"] = ["a"]
        saved = self.client.post(self.base + "/draft", json={"expected_version": 1, "tasks": tasks}).json()
        self.assertTrue(saved["draft"]["warnings"])
        for t in tasks: t["owner"] = "小李"
        conflict = self.client.post(self.base + "/confirm", json={"expected_version": 2, "tasks": tasks})
        self.assertEqual(conflict.status_code, 422)
        tasks[1]["start_date"] = tasks[1]["due_date"] = "2026-10-09"
        stale = self.client.post(self.base + "/draft", json={"expected_version": 1, "tasks": tasks})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get(self.base).json()["version"], 2)

    def test_real_client_path_and_scoped_question_citations(self):
        llm = object.__new__(OpenAIClient)
        llm._chat = Mock(return_value=json.dumps({"tasks": [task("a", owner="小李", source_refs=["u1"]).model_dump(mode="json")]}))
        with patch.object(server, "agent", SimpleNamespace(llm=llm)):
            doc = self.generate()
            self.assertEqual(doc["draft"]["mode"], "ai")
            llm._chat.return_value = json.dumps({"answer": "小李做a", "task_ids": ["a"], "source_refs": ["u1"]})
            answer = self.client.post(self.base + "/ask", json={"question": "谁做a", "scope": "draft", "expected_version": 1})
            self.assertEqual(answer.status_code, 200, answer.text)
            self.assertIn("先完成a", answer.json()["sources"][0]["text"])
            llm._chat.return_value = json.dumps({"answer": "别的会", "task_ids": ["a"], "source_refs": ["foreign"]})
            self.assertEqual(self.client.post(self.base + "/ask", json={"question": "谁做", "scope": "draft", "expected_version": 1}).status_code, 502)

    def test_missing_meeting_corrupt_storage_and_token(self):
        self.assertEqual(self.client.get("/api/meetings/absent/task-plan").status_code, 404)
        self.generate()
        path = TaskPlanStore(self.boards.root, "meeting").path
        path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.client.get(self.base).status_code, 503)
        with patch.object(server.config, "CONFIG", SimpleNamespace(api_token="secret")):
            self.assertEqual(self.client.post(self.base + "/generate", json={"expected_version": 0, "reference_date": "2026-10-08"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
