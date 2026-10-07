import json
import os
import tempfile
import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app.llm import LLMError, LLMMetrics, OpenAIClient
from app.orchestrator import BoardAgent
from app.storage import StoreA, StoreB
from app.utterance_ingest import IncomingSpeaker, IncomingUtterance, UtteranceIngestor


class _OpenBreaker:
    failures = 1

    def allow(self):
        return True

    def record_success(self):
        pass

    def record_failure(self):
        return True


class PrivacyAndRecoveryTests(unittest.TestCase):
    def test_upstream_error_text_is_not_written_to_logs_metrics_or_user_error(self):
        private_text = "PRIVATE_MEETING_TEXT sk-test-secret"
        client = object.__new__(OpenAIClient)
        client.model = "test-model"
        client.temperature = 0
        client.timeout = 3
        client.max_retries = 1
        client.max_tokens = 128
        client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **_: (_ for _ in ()).throw(RuntimeError(private_text))
        )))
        metrics = LLMMetrics()

        with patch("app.llm._METRICS", metrics), patch("app.llm._get_breaker", return_value=_OpenBreaker()), \
                patch("app.llm.time.sleep"):
            with self.assertLogs("amo.llm", level="WARNING") as captured:
                with self.assertRaises(LLMError) as raised:
                    client._completion([{"role": "user", "content": private_text}])

        exposed = "\n".join(captured.output) + str(raised.exception) + json.dumps(metrics.snapshot())
        self.assertNotIn(private_text, exposed)
        self.assertNotIn("sk-test-secret", exposed)

    def test_invalid_model_json_does_not_echo_meeting_text_in_exception(self):
        private_text = "PRIVATE_MEETING_TEXT"
        client = object.__new__(OpenAIClient)
        with patch.object(client, "_chat", return_value=f"not json {private_text}"):
            with self.assertRaises(LLMError) as raised:
                client.analyze([private_text], ["meta-1"], "private title")
        self.assertNotIn(private_text, str(raised.exception))
        self.assertNotIn("private title", str(raised.exception))

    def test_orchestrator_error_summary_does_not_echo_llm_exception_text(self):
        private_text = "PRIVATE_MEETING_TEXT sk-orchestrator-secret"

        def fail(*_):
            raise RuntimeError(private_text)

        agent = object.__new__(BoardAgent)
        agent.llm = SimpleNamespace(analyze=fail)
        state = SimpleNamespace(
            filtered_text=["会议内容"], filtered_meta_ids=["meta-1"],
            meeting_title="private title", llm_messages_analyze=[],
        )
        result = asyncio.run(agent.analyze_node(state))
        self.assertNotIn(private_text, result["error"])
        self.assertNotIn("sk-orchestrator-secret", result["error"])

    def test_store_a_failed_atomic_replace_keeps_last_durable_board(self):
        with tempfile.TemporaryDirectory() as root:
            store = StoreA(root)
            original = [{"id": "n_issue_root", "shape": "rect", "data": {"type": "issue", "label": "原始标题"}}]
            store.save("mtg_crash", original, version=1)
            path = store._path("mtg_crash")
            durable_before = open(path, "rb").read()
            store._last_flush["mtg_crash"] = 0
            changed = [{"id": "n_issue_root", "shape": "rect", "data": {"type": "issue", "label": "未持久化标题"}}]

            with patch("app.storage.os.replace", side_effect=OSError("simulated power loss")):
                with self.assertRaises(OSError):
                    store.save("mtg_crash", changed, version=2)

            self.assertEqual(open(path, "rb").read(), durable_before)
            recovered = StoreA(root)
            self.assertEqual(recovered.version("mtg_crash"), 1)
            self.assertEqual(recovered.load("mtg_crash")[0]["data"]["label"], "原始标题")

    def test_store_b_replays_complete_rows_and_ignores_crashed_partial_tail(self):
        with tempfile.TemporaryDirectory() as root:
            shard_dir = os.path.join(root, "metadata_shards")
            os.makedirs(shard_dir)
            path = os.path.join(shard_dir, "mtg_crash.jsonl")
            complete = {"meta_id": "utt_1", "record": {
                "meta_id": "utt_1", "kind": "utt", "meeting_id": "mtg_crash", "text": "已确认保存"
            }}
            with open(path, "w", encoding="utf-8") as journal:
                journal.write(json.dumps(complete, ensure_ascii=False) + "\n")
                journal.write('{"meta_id":"utt_2","record":{"text":"半截')

            recovered = StoreB(root)
            self.assertEqual(recovered.get("utt_1")["text"], "已确认保存")
            self.assertIsNone(recovered.get("utt_2"))

    def test_ingest_status_does_not_persist_upstream_exception_text(self):
        private_text = "PRIVATE_MEETING_TEXT sk-ingest-secret"
        with tempfile.TemporaryDirectory() as root:
            store_a, store_b = StoreA(root), StoreB(root)

            async def drive(*_, **__):
                raise RuntimeError(private_text)

            event = IncomingUtterance(
                utterance_id="evt_1", meeting_id="mtg_privacy", seq=1,
                speaker=IncomingSpeaker(speaker_ref="speaker_1"), text="会议原话不可外泄",
                start_offset_ms=0, end_offset_ms=100, received_at_ms=100,
                source="test",
            )
            ingestor = UtteranceIngestor(store_a, store_b, drive)
            result = asyncio.run(ingestor.ingest(event))
            record = store_b.get(result["meta_id"])

        self.assertEqual(result["state"], "failed")
        self.assertEqual(record["last_error"], "RuntimeError")
        self.assertEqual(result["error"], "RuntimeError")
        self.assertNotIn(private_text, json.dumps(record))
        self.assertNotIn("sk-ingest-secret", json.dumps(result))

    def test_store_b_log_omits_io_exception_text(self):
        private_text = "PRIVATE_MEETING_TEXT sk-storage-secret"
        with tempfile.TemporaryDirectory() as root:
            store = StoreB(root)
            with patch("builtins.open", side_effect=OSError(private_text)):
                with self.assertLogs("amo.storage", level="WARNING") as captured:
                    with self.assertRaises(OSError):
                        store._append("mtg_privacy", "meta_1", {"text": "会议原话"})
        self.assertNotIn(private_text, "\n".join(captured.output))
        self.assertNotIn("sk-storage-secret", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
