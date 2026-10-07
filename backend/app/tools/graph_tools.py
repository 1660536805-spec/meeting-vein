"""图操作工具（Agent function calling 执行端）。

工具清单（Design_FrontendBoard §4）：update_graph / get_board / lock_node /
set_importance / search_nodes / create_node。所有写操作尊重 lock/edit，冲突回 skipped。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from ..models import GraphUpdateOp, GraphOp
from ..storage import op_blocked_by_lock_or_edit


@dataclass
class ToolResult:
    ok: bool
    data: Optional[dict] = None
    skipped: list = field(default_factory=list)     # 因 lock/edit 跳过的 node id


class GraphTools:
    def __init__(self, store_a, store_b):
        self.store_a = store_a
        self.store_b = store_b

    # —— 读取 ——
    def get_board(self, graph_id: str) -> ToolResult:
        return ToolResult(ok=True, data={"cells": self.store_a.load(graph_id)})

    def search_nodes(self, graph_id: str, query: str) -> ToolResult:
        q = query.lower()
        hits = [c["id"] for c in self.store_a.load(graph_id)
                if q in str(c.get("data", {}).get("label", "")).lower()]
        return ToolResult(ok=True, data={"node_ids": hits})

    # —— 写入（尊重 lock/edit）——
    def _locked_or_edited(self, cell: dict, op: GraphOp, index: dict = None) -> bool:
        # 复用 StoreA 的 lock/edit 仲裁，避免工具层与落库层两套判定漂移
        return op_blocked_by_lock_or_edit(cell, op, index)

    def update_graph(self, graph_id: str, op: GraphUpdateOp) -> ToolResult:
        cells = self.store_a.load(graph_id)
        index = {c["id"]: c for c in cells}
        applied, skipped = [], []
        for g in op.operations:
            cell = index.get(g.node) if g.node else (index.get(g.target) if g.op == "link" else None)
            if cell and self._locked_or_edited(cell, g, index):
                skipped.append(g.node)
                continue
            if g.op == "merge_as_duplicate":
                target = index.get(g.parent) if g.parent else None
                duplicate = index.get(g.node) if g.node else None
                if ((target and self._locked_or_edited(target, g, index)) or
                        (duplicate and self._locked_or_edited(duplicate, g, index))):
                    skipped.append(g.node)
                    continue
            applied.append(g)
        receipt = {"ok": True, "errors": [], "change_set": {"added": [], "removed": [], "updated": []}}
        if applied:
            _, receipt = self.store_a.commit_graph_update(
                graph_id, GraphUpdateOp(operations=applied, thought=op.thought))
        return ToolResult(ok=receipt["ok"],
                          data={"applied": len(applied) if receipt["ok"] else 0,
                                "skipped": len(skipped), "repair_receipt": receipt}, skipped=skipped)

    def create_node(self, graph_id: str, node_id: str, node_type: str,
                    label: str, meta_ids: list = None, x: int = 0, y: int = 0) -> ToolResult:
        op = GraphUpdateOp(operations=[GraphOp(op="add_node", node=node_id, node_type=node_type,
                                               label=label, meta_ids=meta_ids or [])])
        return self.update_graph(graph_id, op)

    def lock_node(self, graph_id: str, node_id: str, locked_by: str, locked: bool = True) -> ToolResult:
        if not any(c.get("id") == node_id and c.get("shape") != "edge"
                   for c in self.store_a.load(graph_id)):
            return ToolResult(ok=False, data={"error": "node not found"})
        self.store_a.apply_user_operation(graph_id, node_id, "lock",
                                          {"locked": locked}, actor=locked_by)
        return ToolResult(ok=True, data={"locked": locked})

    def set_importance(self, graph_id: str, node_id: str, level: str) -> ToolResult:
        op = GraphUpdateOp(operations=[GraphOp(op="set_importance", node=node_id, importance=level)])
        return self.update_graph(graph_id, op)
