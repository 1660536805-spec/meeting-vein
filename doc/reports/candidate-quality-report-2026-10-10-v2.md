# 会脉 · 候选质量评测报告（真实 LLM）

**评测日期**：2026-10-10　**模型**：`deepseek-chat`
**被测管线**：`app.orchestrator.BoardAgent`（LangGraph 双 Agent，analyze→sync→commit），逐句批次（batch=1，与线上默认 debounce=0 一致）
**金标**：`/Users/leo/Downloads/工作文件/开元造物黑客松/ai_meeting_organizer-fusion/backend/tests/annotations/debate-200-gold.csv`（辩论-200，人工标注）
**预测文件**：`doc/reports/predictions-real-debate200-v2.jsonl`
**成功提交预测**：197/200 句；失败 3 句计为缺失。

---

## 1. 结论摘要

在 41 条人工标注候选上，真实 LLM 的**类型分类准确率 53.7%**（macro-F1 34.8%），相对 Mock 基线 43.9% 提升 9.8 个百分点。

- **关系目标评分**：10/11 条可评分；其余 1 条不可评分，缺少人工目标行或可比较的节点预测。
- **冲突（oppose）检测**：precision 100.0% / recall 14.3% / F1 25.0%。
- **重复簇去重**（pairwise）：precision 0.0% / recall 0.0% / F1 0.0%（Mock 基线 F1 0.0%）。
- **溯源绑定**：39/41 条候选绑定到来源句；该指标不检验语义关联是否正确。

---

## 2. 实验设置

| 项 | 值 |
|---|---|
| 样本 | 辩论赛-200 句（`backend/tests/辩论赛200句.txt`）|
| 人工标注候选 | 41 条（仅辩论类有金标）|
| 关系标注行 | 11 条；其中 10 条可评分 |
| 模型 | deepseek-chat |
| 管线 | 生产 BoardAgent（analyze + sync 双 Agent）|
| 批次 | batch=1（逐句）|
| 评测器 | `backend/scripts/evaluate_candidates.py --mode real` |

---

## 3. 结果明细

### 3.1 候选类型混淆矩阵

| 标注\预测 | __missing__ | action | conclusion | conflict | evidence | issue | other | point | question |
|---|---|---|---|---|---|---|---|---|---|
| **__missing__** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **action** | 0 | 2 | 0 | 0 | 1 | 0 | 1 | 1 | 0 |
| **conclusion** | 0 | 0 | 1 | 0 | 1 | 0 | 0 | 7 | 0 |
| **conflict** | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| **evidence** | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| **issue** | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **other** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **point** | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 15 | 0 |
| **question** | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 1 | 2 |

准确率 **53.7%**（22/41）；各类型：

| 类型 | 支持数 | precision | recall | F1 |
|---|---|---|---|---|
| point | 18 | 62.5% | 83.3% | 71.4% |
| conclusion | 9 | 50.0% | 11.1% | 18.2% |
| action | 5 | 100.0% | 40.0% | 57.1% |
| question | 5 | 100.0% | 40.0% | 57.1% |
| evidence | 2 | 25.0% | 100.0% | 40.0% |
| conflict | 1 | 0.0% | 0.0% | 0.0% |
| issue | 1 | 0.0% | 0.0% | 0.0% |

### 3.2 冲突（oppose）检测

|  | 预测有冲突 | 预测无冲突 |
|---|---|---|
| **标注有冲突** | 1 | 6 |
| **标注无冲突** | 0 | 3 |

precision 100.0% / recall 14.3% / F1 25.0%

### 3.3 重复簇去重（pairwise）

| 指标 | 值 |
|---|---|
| 金标正对 | 3 |
| 预测正对 | 0 |
| TP / FP / FN | 0 / 0 / 3 |
| precision / recall / F1 | 0.0% / 0.0% / 0.0% |

### 3.4 溯源绑定

- evidence_link：39/41 候选绑定到来源句。
- 目标节点归属：20.0%（正确 2/10 条可评分；金标共 11 条）；不可评分项目不记为 0%。

### 3.5 与 Mock 基线对比

| 指标 | Mock | 真实 LLM |
|---|---|---|
| 类型准确率 | 43.9% | 53.7% |
| 类型 macro-F1 | 29.0% | 34.8% |
| 去重 F1 | 0.0% | 0.0% |

---

## 4. 发现与局限（诚实披露）

- **类型口径待仲裁**：辩论赛金标把部分开篇立场和末尾重申标为 `conclusion`，但产品提示词仅把已达成的决议或共识标为 `conclusion`。错误切片中有 7 条因此被预测为 `point`；须由第二标注者统一口径后重算，不能直接把模型改成将辩论立场当作已确认决议。
- 真实 LLM 结果须由独立 predictions 文件提供；本工具不会发送会议原文到外部服务。

补充（本次实跑观察）：
- **可评分覆盖率**：10/11 条关系有人工目标行及可比较的节点预测；未覆盖项不参与关系准确率和目标归属准确率。
- **标注规模**：当前辩论样本的类型、关系、重复正例数量较少；不能外推到其他会议场景。

---

## 5. 复现命令

```bash
cd backend
# 1) 用真实 LLM + 生产管线生成预测（逐句批次）
../.venv/bin/python scripts/generate_real_predictions.py --batch-size 1 --passes 4 \
    --output ../doc/reports/predictions-real-debate200-v2.jsonl
# 2) 对金标评分
../.venv/bin/python scripts/evaluate_candidates.py --mode real \
    --predictions ../doc/reports/predictions-real-debate200-v2.jsonl \
    --output ../doc/reports/candidate-quality-real-2026-10-10-v2.json
# 3) 渲染本报告
../.venv/bin/python scripts/render_quality_report.py \
    --real ../doc/reports/candidate-quality-real-2026-10-10-v2.json \
    --mock ../doc/reports/candidate-quality-mock-2026-10-10-v2.json \
    --predictions ../doc/reports/predictions-real-debate200-v2.jsonl \
    --model deepseek-chat --out ../doc/reports/candidate-quality-report-2026-10-10-v2.md
```

> 说明：真实预测生成脚本会把测试语料发送到所配置的模型端点；评分脚本只读取本地文件。请同时保存模型名、提示词版本、运行日志和失败行，才可复核预测来源。
