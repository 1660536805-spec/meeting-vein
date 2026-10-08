# Real LLM Candidate Evaluation — 2026-10-07

**Status: not run.** No real-provider prediction file was available for this evaluation.

The evaluator has an explicit `--mode real --predictions <jsonl>` path and does not call a provider. This keeps model choice, credentials, and transmission of the 200 meeting utterances outside an implicit test run. The report must be regenerated from actual real-LLM predictions before making any quality claim.

Expected JSONL fields per source row: `line_no`, `type`, `relation`, `ownership_anchor`, `evidence_line_no`, and `node_id`. `ownership_anchor` must use the annotation's semantic anchor key (the suffix in `relation_anchor`), not a model-generated node ID. Missing outputs count as missing predictions in the confusion matrices; Mock results are never copied into this report.
