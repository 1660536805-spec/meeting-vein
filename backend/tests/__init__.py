"""端到端测试包。

覆盖：mock 会议对话输入 → 双流（ASR / 光标）平行汇入 → 双 Agent 编排
（分析 Agent → MeetingSummary → 图同步 Agent → GraphUpdateOp）→ 双存储
落库（Store A 关系图 / Store B 元数据）。并对照设计文档逐条核检产物合规性。
"""
