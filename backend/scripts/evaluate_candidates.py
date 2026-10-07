#!/usr/bin/env python3
"""Evaluate Mock or externally generated predictions against the gold CSV.

Real mode consumes predictions from a caller-provided JSONL file; this script
never calls a provider or transmits source utterances.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

from app.candidate_evaluation import evaluation_report

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "backend/tests/annotations/debate-200-gold.csv"


def mock_predictions(rows):
    from app.llm import MockLLM

    utterances = [row["utterance"] for row in rows]
    meta_ids = [f"utt_{row['line_no']}" for row in rows]
    llm = MockLLM()
    summary = llm.analyze(utterances, meta_ids, "远程办公与固定坐班")
    root = {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "辩题"}}
    operations = llm.sync(summary, [root], [])
    node_by_meta = {}
    for op in operations.operations:
        if op.op == "add_node":
            for meta_id in op.meta_ids:
                node_by_meta[meta_id] = op.node
    predictions = {}
    for insight in summary.insights:
        for meta_id in insight.evidence:
            line_no = int(meta_id.rsplit("_", 1)[1])
            predictions[line_no] = {
                "type": insight.type,
                "relation": insight.relation_to_related,
                "ownership_anchor": insight.related_to,
                "evidence_line_no": line_no,
                "node_id": node_by_meta.get(meta_id),
            }
    return predictions


def load_real_predictions(path: Path):
    predictions = {}
    with path.open(encoding="utf-8") as stream:
        for line_no, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            item = json.loads(raw)
            source_line = int(item["line_no"])
            if source_line in predictions:
                raise ValueError(f"duplicate prediction for source line {source_line}")
            predictions[source_line] = item
    return predictions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--predictions", type=Path,
                        help="real 模式 JSONL；每行含 line_no/type/relation/ownership_anchor/evidence_line_no/node_id")
    parser.add_argument("--gold", type=Path, default=GOLD)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "real" and not args.predictions:
        parser.error("real mode requires --predictions; no provider is called automatically")
    with args.gold.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    predictions = mock_predictions(rows) if args.mode == "mock" else load_real_predictions(args.predictions)
    report = evaluation_report(rows, predictions, args.mode)
    report["gold_file"] = str(args.gold)
    report["prediction_file"] = str(args.predictions) if args.predictions else "MockLLM runtime output"
    if args.mode == "mock":
        report["limitations"].append(
            "Mock 不产出说话人或候选议题归属；anchor accuracy 按标注的关系目标严格计算。")
        report["limitations"].append(
            "Mock evidence_link 41/41 仅验证输入 meta_id 原样绑定，不代表语义归属准确率。")
    output = args.output or ROOT / f"doc/reports/candidate-quality-{args.mode}-{date.today().isoformat()}.json"
    report["report_file"] = str(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
