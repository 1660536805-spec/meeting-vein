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
    ap.add_argument("--model", default="未记录")
    args = ap.parse_args()

    real = json.loads(args.real.read_text(encoding="utf-8"))
    mock = json.loads(args.mock.read_text(encoding="utf-8")) if args.mock and args.mock.exists() else None
    prediction_file = args.predictions or Path(real.get("prediction_file", ""))
    prediction_count = None
    if prediction_file.is_file():
        prediction_count = sum(bool(line.strip()) for line in prediction_file.open(encoding="utf-8"))

    tc = real["type_confusion"]
    cc = real["conflict_confusion"]
    dedup = real["deduplication"]
    asg = real["assignment"]
    m_yes = cc["matrix"]["yes"]
    if cc["count"]:
        conflict_prec = m_yes["yes"] / (m_yes["yes"] + cc["matrix"]["no"]["yes"]) if (m_yes["yes"] + cc["matrix"]["no"]["yes"]) else 0.0
        conflict_rec = m_yes["yes"] / (m_yes["yes"] + m_yes["no"]) if (m_yes["yes"] + m_yes["no"]) else 0.0
        conflict_f1 = 2 * conflict_prec * conflict_rec / (conflict_prec + conflict_rec) if (conflict_prec + conflict_rec) else 0.0
    else:
        conflict_prec = conflict_rec = conflict_f1 = None

    L = []
    A = L.append
    A(f"# 会脉 · 候选质量评测报告（真实 LLM）")
    A("")
    A(f"**评测日期**：{date.today().isoformat()}　**模型**：`{args.model}`")
    A(f"**被测管线**：`app.orchestrator.BoardAgent`（LangGraph 双 Agent，analyze→sync→commit），逐句批次（batch=1，与线上默认 debounce=0 一致）")
    A(f"**金标**：`{real.get('gold_file')}`（辩论-200，人工标注）")
    A(f"**预测文件**：`{real.get('prediction_file')}`")
    if prediction_count is not None:
        A(f"**成功提交预测**：{prediction_count}/{real['sample_rows']} 句；失败 {real['sample_rows'] - prediction_count} 句计为缺失。")
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
    A(f"- **关系目标评分**：{real['relation_confusion']['count']}/{real['relation_labeled_rows']} 条可评分；"
      f"其余 {real.get('unscored_relation_rows', 0)} 条不可评分，缺少人工目标行或可比较的节点预测。")
    A(f"- **冲突（oppose）检测**：precision {pct(conflict_prec)} / recall {pct(conflict_rec)} / F1 {pct(conflict_f1)}。")
    A(f"- **重复簇去重**（pairwise）：precision {pct(dedup['precision'])} / recall {pct(dedup['recall'])} / F1 {pct(dedup['f1'])}"
      + (f"（Mock 基线 F1 {pct(mock['deduplication']['f1'])}）。" if mock else "。"))
    A(f"- **溯源绑定**：{asg['evidence_link']['matrix']['yes']['yes']}/{asg['evidence_link']['count']} 条候选绑定到来源句；该指标不检验语义关联是否正确。")
    A("")
    A("---")
    A("")
    A("## 2. 实验设置")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| 样本 | 辩论赛-200 句（`backend/tests/辩论赛200句.txt`）|")
    A(f"| 人工标注候选 | {real['human_labeled_types']} 条（仅辩论类有金标）|")
    A(f"| 关系标注行 | {real['relation_labeled_rows']} 条；其中 {real['relation_confusion']['count']} 条可评分 |")
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
    A(f"- 目标节点归属：{pct(asg['anchor_accuracy'])}（正确 {asg.get('correct_anchor_assignments', 0)}/{asg.get('scored_anchor_rows', 0)} 条可评分；金标共 {asg['gold_anchor_rows']} 条）；不可评分项目不记为 0%。")
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
    if "debate-200-gold.csv" in real.get("gold_file", ""):
        A("- **类型口径待仲裁**：辩论赛金标把部分开篇立场和末尾重申标为 `conclusion`，"
          "但产品提示词仅把已达成的决议或共识标为 `conclusion`。错误切片中有 7 条因此被预测为 `point`；"
          "须由第二标注者统一口径后重算，不能直接把模型改成将辩论立场当作已确认决议。")
    for x in real.get("limitations", []):
        A(f"- {x}")
    A("")
    A("补充（本次实跑观察）：")
    A(f"- **可评分覆盖率**：{real['relation_confusion']['count']}/{real['relation_labeled_rows']} 条关系有人工目标行及可比较的节点预测；"
      "未覆盖项不参与关系准确率和目标归属准确率。")
    A("- **标注规模**：当前辩论样本的类型、关系、重复正例数量较少；不能外推到其他会议场景。")
    A("")
    A("---")
    A("")
    A("## 5. 复现命令")
    A("")
    A("```bash")
    A("cd backend")
    A("# 1) 用真实 LLM + 生产管线生成预测（逐句批次）")
    A("../.venv/bin/python scripts/generate_real_predictions.py --batch-size 1 --passes 4 \\")
    A("    --output ../doc/reports/predictions-real-debate200-v2.jsonl")
    A("# 2) 对金标评分")
    A("../.venv/bin/python scripts/evaluate_candidates.py --mode real \\")
    A("    --predictions ../doc/reports/predictions-real-debate200-v2.jsonl \\")
    A("    --output ../doc/reports/candidate-quality-real-2026-10-10-v2.json")
    A("# 3) 渲染本报告")
    A("../.venv/bin/python scripts/render_quality_report.py \\")
    A("    --real ../doc/reports/candidate-quality-real-2026-10-10-v2.json \\")
    A("    --mock ../doc/reports/candidate-quality-mock-2026-10-10-v2.json \\")
    A("    --predictions ../doc/reports/predictions-real-debate200-v2.jsonl \\")
    A("    --model deepseek-chat --out ../doc/reports/candidate-quality-report-2026-10-10-v2.md")
    A("```")
    A("")
    A("> 说明：真实预测生成脚本会把测试语料发送到所配置的模型端点；评分脚本只读取本地文件。"
      "请同时保存模型名、提示词版本、运行日志和失败行，才可复核预测来源。")
    A("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[out] 报告 → {args.out}")


if __name__ == "__main__":
    main()
