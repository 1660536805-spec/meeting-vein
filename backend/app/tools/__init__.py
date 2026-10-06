"""Agent 工具接口层（Design_FrontendBoard §4）。

LLM function calling → 后端执行端。所有写操作尊重节点 lock/edit（Design_StructureGraph_Storage §7），
冲突返回 skipped 列表（用户操作优先于 Agent 操作）。
"""
