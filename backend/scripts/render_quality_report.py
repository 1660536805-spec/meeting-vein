#!/usr/bin/env python3
"""把 evaluate_candidates 的 JSON 结果渲染成一份中文《候选质量评测报告》（markdown）。

用法：
    python scripts/render_quality_report.py \
        --real doc/reports/candidate-quality-real-<date>.json \
        --mock doc/reports/candidate-quality-mock-2026-10-07.json \
        --predictions doc/reports/predictions-real-debate200.jsonl \
        --out doc/reports/candidate-quality-report-<date>.md
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path


def pct(v):
    return "n/a" if v is None else f"{v * 100:.1f}%"


def macro_f1(per_label: dict) -> float:
    vals = [m["f1"] for m in per_label.values() if m.get("support")]
    return sum(vals) / len(vals) if vals else 0.0


def matrix_table(cm: dict) -> str:
    labels = cm["labels"]
    head = "| 标注\\预测 | " + " | ".join(labels) + " |"
    sep = "|" + "---|" * (len(labels) + 1)
    lines = [head, sep]
    for gold in labels:
        row = [f"**{gold}**"] + [str(cm["matrix"][gold].get(g, 0)) for g in labels]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", type=Path, required=True)
    ap.add_argument("--mock", type=Path, default=None)
    ap.add_argument("--predictions", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="deepseek-chat")
    args = ap.parse_args()

    real = json.loads(args.real.read_text(encoding="utf-8"))
    mock = json.loads(args.mock.read_text(encoding="utf-8")) if args.mock and args.mock.exists() else None

    tc = real["type_confusion"]
    cc = real["conflict_confusion"]
    dedup = real["deduplication"]
    asg = real["assignment"]
    m_yes = cc["matrix"]["yes"]
    conflict_prec = m_yes["yes"] / (m_yes["yes"] + cc["matrix"]["no"]["yes"]) if (m_yes["yes"] + cc["matrix"]["no"]["yes"]) else 0.0
    conflict_rec = m_yes["yes"] / (m_yes["yes"] + m_yes["no"]) if (m_yes["yes"] + m_yes["no"]) else 0.0
    conflict_f1 = 2 * conflict_prec * conflict_rec / (conflict_prec + conflict_rec) if (conflict_prec + conflict_rec) else 0.0

    L = []
    A = L.append
    A(f"# 会脉 · 候选质量评测报告（真实 LLM）")
    A("")
    A(f"**评测日期**：{date.today().isoformat()}　**模型**：`{args.model}`（OpenAI 兼容端点）")
    A(f"**被测管线**：`app.orchestrator.BoardAgent`（LangGraph 双 Agent，analyze→sync→commit），逐句批次（batch=1，与线上默认 debounce=0 一致）")
    A(f"**金标**：`{real.get('gold_file')}`（辩论-200，人工标注）")
    A(f"**预测文件**：`{real.get('prediction_file')}`")
    A("")
    A("---")
    A("")
    A("## 1. 结论摘要")
    A("")
    A(f"在 {real['human_labeled_types']} 条人工标注候选上，真实 LLM 的**类型分类准确率 {pct(tc['accuracy'])}**"
      f"（macro-F1 {pct(macro_f1(tc['per_label']))}）"
      + (f"，相对 Mock 基线 {pct(mock['type_confusion']['accuracy'])} 提升 "
         f"{(tc['accuracy'] - mock['type_confusion']['accuracy']) * 100:.1f} 个百分点。" if mock else "。"))
    A("")
    A(f"- **冲突（oppose）检测**：precision {pct(conflict_prec)} / recall {pct(conflict_rec)} / F1 {pct(conflict_f1)}"
      + (f"（Mock 基线 recall {pct(mock['conflict_confusion']['matrix']['yes']['yes'] / max(1, sum(mock['conflict_confusion']['matrix']['yes'].values())))}）。" if mock else "。"))
    A(f"- **重复簇去重**（pairwise）：precision {pct(dedup['precision'])} / recall {pct(dedup['recall'])} / F1 {pct(dedup['f1'])}"
      + (f"（Mock 基线 F1 {pct(mock['deduplication']['f1'])}）。" if mock else "。"))
    A(f"- **溯源绑定**：{asg['evidence_link']['matrix']['yes']['yes']}/{asg['evidence_link']['count']} 条候选绑定到来源句"
      f"（见 §4 局限：单句批次由兜底逻辑补全）。")
    A("")
    A("---")
    A("")
    A("## 2. 实验设置")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| 样本 | 辩论赛-200 句（`backend/tests/辩论赛200句.txt`）|")
    A(f"| 人工标注候选 | {real['human_labeled_types']} 条（仅辩论类有金标）|")
    A(f"| 关系标注行 | {real['relation_labeled_rows']} 条（oppose/support/duplicate）|")
    A(f"| 模型 | {args.model} |")
    A(f"| 管线 | 生产 BoardAgent（analyze + sync 双 Agent）|")
    A(f"| 批次 | batch=1（逐句）|")
    A(f"| 评测器 | `backend/scripts/evaluate_candidates.py --mode real` |")
    A("")
    A("---")
    A("")
    A("## 3. 结果明细")
    A("")
    A("### 3.1 候选类型混淆矩阵")
    A("")
    A(matrix_table(tc))
    A("")
    A(f"准确率 **{pct(tc['accuracy'])}**（{tc['correct']}/{tc['count']}）；各类型：")
    A("")
    A("| 类型 | 支持数 | precision | recall | F1 |")
    A("|---|---|---|---|---|")
    for lab, m in sorted(tc["per_label"].items(), key=lambda kv: -kv[1]["support"]):
        if not m["support"] and not m["precision"]:
            continue
        A(f"| {lab} | {m['support']} | {pct(m['precision'])} | {pct(m['recall'])} | {pct(m['f1'])} |")
    A("")
    A("### 3.2 冲突（oppose）检测")
    A("")
    A("|  | 预测有冲突 | 预测无冲突 |")
    A("|---|---|---|")
    A(f"| **标注有冲突** | {cc['matrix']['yes']['yes']} | {cc['matrix']['yes']['no']} |")
    A(f"| **标注无冲突** | {cc['matrix']['no']['yes']} | {cc['matrix']['no']['no']} |")
    A("")
    A(f"precision {pct(conflict_prec)} / recall {pct(conflict_rec)} / F1 {pct(conflict_f1)}")
    A("")
    A("### 3.3 重复簇去重（pairwise）")
    A("")
    A("| 指标 | 值 |")
    A("|---|---|")
    A(f"| 金标正对 | {dedup['gold_pairs']} |")
    A(f"| 预测正对 | {dedup['predicted_pairs']} |")
    A(f"| TP / FP / FN | {dedup['true_positive_pairs']} / {dedup['false_positive_pairs']} / {dedup['false_negative_pairs']} |")
    A(f"| precision / recall / F1 | {pct(dedup['precision'])} / {pct(dedup['recall'])} / {pct(dedup['f1'])} |")
    A("")
    A("### 3.4 溯源绑定")
    A("")
    A(f"- evidence_link：{asg['evidence_link']['matrix']['yes']['yes']}/{asg['evidence_link']['count']} 候选绑定到来源句。")
    A(f"- 语义锚键归属（anchor_accuracy）：{pct(asg['anchor_accuracy'])} —— 该项需标注集私有锚键，真实模型无从得知，**不作能力评价**（详见 §4）。")
    A("")
    if mock:
        A("### 3.5 与 Mock 基线对比")
        A("")
        A("| 指标 | Mock | 真实 LLM |")
        A("|---|---|---|")
        A(f"| 类型准确率 | {pct(mock['type_confusion']['accuracy'])} | {pct(tc['accuracy'])} |")
        A(f"| 类型 macro-F1 | {pct(macro_f1(mock['type_confusion']['per_label']))} | {pct(macro_f1(tc['per_label']))} |")
        A(f"| 去重 F1 | {pct(mock['deduplication']['f1'])} | {pct(dedup['f1'])} |")
        A("")
    A("---")
    A("")
    A("## 4. 发现与局限（诚实披露）")
    A("")
    A(real.get("limitations", []))
    for x in real.get("limitations", []):
        pass
    for x in real.get("limitations", []):
        A(f"- {x}")
    A("")
    A("补充（本次实跑观察）：")
    A("- **`evidence_index` 缺失**：真实模型在多句批次下普遍不输出 `evidence_index`，导致跨句溯源绑定丢失；单句批次（batch=1）由管线兜底逻辑自动补全为唯一来源句。建议在提示词中加入强约束或改用「逐句批次」策略以稳定溯源。")
    A("- **类型体系口径差异**：金标把「带数据/事实的论证理由」标为 `point`，而产品提示词要求此类产出 `evidence`；这一口径差异会同时压低 precision 与 recall，属**评测基准与产品字典的对齐问题**，而非单纯模型误差。")
    A("- **语义锚键不可比**：`relation_anchor` 后缀（如 `remote_position`）是标注集私有键，模型无法产出，故 `anchor_accuracy` 恒为 0，不代表关系推断能力。")
    A("")
    A("---")
    A("")
    A("## 5. 复现命令")
    A("")
    A("```bash")
    A("cd backend")
    A("# 1) 用真实 LLM + 生产管线生成预测（逐句批次）")
    A("../.venv/bin/python scripts/generate_real_predictions.py --batch-size 1")
    A("# 2) 对金标评分")
    A("../.venv/bin/python scripts/evaluate_candidates.py --mode real \\")
    A("    --predictions ../doc/reports/predictions-real-debate200.jsonl")
    A("# 3) 渲染本报告")
    A("../.venv/bin/python scripts/render_quality_report.py \\")
    A("    --real ../doc/reports/candidate-quality-real-<date>.json \\")
    A("    --mock ../doc/reports/candidate-quality-mock-2026-10-07.json \\")
    A("    --out  ../doc/reports/candidate-quality-report-<date>.md")
    A("```")
    A("")
    A("> 说明：真实 LLM 模式会把会议原文发送到所配置的外部端点（`OPENAI_BASE_URL`）。"
      "本仓库默认 `.env` 使用 deepseek 端点；评测脚本本身不主动外发，预测文件由独立的 `generate_real_predictions.py` 生成，便于审计。")
    A("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[out] 报告 → {args.out}")


if __name__ == "__main__":
    main()
