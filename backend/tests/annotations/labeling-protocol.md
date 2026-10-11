# 候选质量标注协议（待人工执行）

`scheme-200-labeling-queue.csv` 与 `task-200-labeling-queue.csv` 是空白标注队列，**不是金标**。每行原文来自同名 200 句样本；只有完成双人独立标注、分歧仲裁并冻结版本后，才能用于最终盲测。

## 字段

- `gold_type`：`issue`（讨论议题）、`point`（可争论主张）、`evidence`（事实、数据或例子）、`conclusion`（明确达成的决议）、`action`（可执行任务）、`conflict`（独立冲突焦点）、`question`（待回答问题）、`other`（无关/无信息）、`command`（操作看板的指令）。若一句含多个候选，记录主候选并在备注说明拆分，后续升级为多候选格式。
  辩论开篇立场和末尾重复立场仍属 `point`；除非会议真的形成共同决定，不能因为出现“综上”就标 `conclusion`。
- `relation_anchor`：`support:<目标说明>`、`oppose:<目标说明>`、`subordinate:<目标说明>`、`duplicate:<簇号>`；关系方向按“当前行对目标行”解释，标注时必须填写 `relation_target_line_no`。没有可靠目标时留空并在备注说明，不能凭相邻句猜测。
- `duplicate_cluster`：语义相同的句子共享簇号；仅措辞相似或同一主题不算重复。
- `review_status`：`draft`、`agreed`、`adjudicated`。`annotator`、`reviewer` 分别由标注者填写。

## 流程与隔离

1. 两名标注者分别填写独立副本。不要查看模型预测、现有错误切片或对方答案。
2. 仲裁者逐行解决类型、目标行和重复簇分歧；保留原始两份副本与仲裁说明。
3. 方案讨论与任务安排各预留连续的最终盲测区，**在冻结前不得用于提示词修改**。连续划分可能有时间相关性，报告中披露这一点。
4. 冻结 CSV 的 SHA-256、标注日期、标注者与仲裁者，然后运行真实模型生成预测。报告每类正例数、可评分关系数、缺失预测和置信区间。

现有 `debate-200-gold.csv` 的关系目标是首轮补标，见 `relation-target-review.md`，尚未完成第二人复核。当前报告只能用于开发诊断。
