import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.candidate_evaluation import evaluation_report


ROOT = Path(__file__).resolve().parents[2]


class QualityReportRendererTests(unittest.TestCase):
    def test_unscorable_legacy_relations_are_reported_as_unavailable(self):
        rows = [
            {"line_no": "1", "gold_type": "point", "relation_anchor": ""},
            {"line_no": "2", "gold_type": "point", "relation_anchor": "oppose:first",
             "relation_target_line_no": "1"},
        ]
        report = evaluation_report(rows, {
            1: {"type": "point", "node_id": "n1", "evidence_line_no": 1},
            2: {"type": "point", "node_id": "n2", "relation": "oppose",
                "evidence_line_no": 2},
        }, "real")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "metrics.json"
            output = Path(directory) / "report.md"
            source.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(ROOT / "backend/scripts/render_quality_report.py"),
                "--real", str(source), "--out", str(output),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = output.read_text(encoding="utf-8")
            self.assertIn("不可评分", content)
            self.assertIn("0/1", content)
            self.assertNotIn("anchor_accuracy 恒为 0", content)


if __name__ == "__main__":
    unittest.main()
