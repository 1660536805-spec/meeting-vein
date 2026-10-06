"""元数据工具（Agent function calling 执行端）。

工具清单（Design_FrontendBoard §4）：fetch_metadata / get_node。
前端点击节点时按 metadata_refs 反查原始论据（Design_StructureGraph_Storage §5）。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolResult:
    ok: bool
    data: Optional[dict] = None
    skipped: list = field(default_factory=list)


class MetadataTools:
    def __init__(self, store_a, store_b):
        self.store_a = store_a
        self.store_b = store_b

    def fetch_metadata(self, meta_ids: list) -> ToolResult:
        """按 meta_id 列表反查原始论据（utt_/agd_/man_ 前缀自描述）。"""
        return ToolResult(ok=True, data={"records": self.store_b.get_many(meta_ids)})

    def get_node(self, graph_id: str, node_id: str) -> ToolResult:
        """取单节点完整数据（含 metadata_refs），供前端论点详情面板。"""
        for c in self.store_a.load(graph_id):
            if c["id"] == node_id:
                refs = c.get("data", {}).get("metadata_refs", [])
                return ToolResult(ok=True, data={"node": c, "evidence": self.store_b.get_many(refs)})
        return ToolResult(ok=False, data={"error": "node not found"})
