"""核心数据模型。

对齐设计文档：
- NormUtterance      ↔ ASR_UnifiedSchema.md
- NormCursorEvent    ↔ Research_CursorIntent_Capture.md（与 NormUtterance 逐字段同形）
- MeetingSummary      ↔ Design_InputProcessing.md §3（分析 Agent→图同步 Agent 交接物）
- GraphUpdateOp       ↔ Design_Agent_DataFlow.md §3（sync_node 产出，update_node 执行）
- StructureGraph cells ↔ Design_StructureGraph_Storage.md §3（X6 fromJSON 同构）

MVP 用 dataclasses（无 pydantic 依赖，纯 stdlib 可跑）；M3 接生产依赖后接口不变。
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass, field
from typing import Optional


def stable_hash(text: str, mod: int | None = None) -> int:
    """跨进程稳定哈希（SHA-1 截断）。

    用于 id 派生 / 布局坐标。不能用内置 hash()——它随 PYTHONHASHSEED 每次进程
    启动变化，重启后同一观点派生出的 id 不同，StoreA 按 id 合并会失效产生重复节点。
    """
    digest = int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:12], 16)
    return digest % mod if mod else digest


# ---------------------------------------------------------------------------
# 1. ASR 统一输入流（与 NormCursorEvent 同形）
# ---------------------------------------------------------------------------
@dataclass
class SpeakerRef:
    speaker_ref: str            # ent: / ms: / spk: 前缀（ASR_UnifiedSchema §4）
    source_id: Optional[str] = None
    display_name: Optional[str] = None
    is_resolved: bool = False


@dataclass
class NormUtterance:
    utterance_id: str
    meeting_id: str
    session_id: Optional[str]
    seq: int
    speaker: SpeakerRef
    text: str
    language: Optional[str]
    start_offset_ms: int
    end_offset_ms: int
    received_at_ms: int
    is_final: bool
    is_partial: bool
    translation: Optional[str] = None
    source: str = "asr"
    raw_ref: Optional[str] = None


# ---------------------------------------------------------------------------
# 2. 光标统一输入流（与 NormUtterance 同形；差异在 gesture/target 与 actor）
# ---------------------------------------------------------------------------
@dataclass
class ActorRef:
    user_ref: str               # ent: / local: / anon: 前缀
    device: str = "desktop"
    is_resolved: bool = True


@dataclass
class CursorTarget:
    node_id: Optional[str] = None
    node_type: Optional[str] = None
    edge_id: Optional[str] = None


@dataclass
class NormCursorEvent:
    event_id: str
    meeting_id: str
    session_id: Optional[str]
    seq: int
    actor: ActorRef
    gesture_type: str           # hover / click / dblclick / drag_node / collapse / canvas_hold
    target: CursorTarget
    pointer: dict                # {"x": int, "y": int}
    intent_hint: Optional[str] = None
    start_offset_ms: int = 0
    end_offset_ms: int = 0
    received_at_ms: int = 0
    is_final: bool = True
    is_partial: bool = False
    source: str = "x6_canvas"
    raw_ref: Optional[str] = None


# ---------------------------------------------------------------------------
# 3. 双 Agent 交接物：MeetingSummary（分析 Agent 产出）
# ---------------------------------------------------------------------------
@dataclass
class InsightRecord:
    type: str                   # point / evidence / issue / conclusion / action / conflict
    summary: str
    speaker_ref: Optional[str] = None
    evidence: list = field(default_factory=list)        # meta_id 列表 → 溯源
    confidence: float = 0.5
    importance_hint: Optional[str] = None
    related_to: Optional[str] = None                     # 关联节点 id
    relation_to_related: Optional[str] = None            # support / oppose / derive / child
    ownership_index: Optional[int] = None                 # 当前候选列表中的父议题建议，不是外部节点 id
    parent_index: Optional[int] = None                    # 当前批次中直接父观点的 insight 序号
    importance_rationale: Optional[str] = None             # adopted / affects_action / repeated / evidence


@dataclass
class MeetingSummary:
    meeting_id: str
    meeting_title: Optional[str] = None
    insights: list = field(default_factory=list)         # List[InsightRecord]
    thought: str = ""


# ---------------------------------------------------------------------------
# 4. 图更新算子：GraphUpdateOp（图同步 Agent 产出，update_node 执行）
# ---------------------------------------------------------------------------
@dataclass
class GraphOp:
    op: str                      # add_node / link / merge_as_duplicate / replace / set_importance / mark_node / move_node / relayout
    node: Optional[str] = None
    node_type: Optional[str] = None
    label: Optional[str] = None
    parent: Optional[str] = None
    edge: Optional[str] = None
    source: Optional[str] = None
    target: Optional[str] = None
    relation: Optional[str] = None       # support / oppose / derive
    importance: Optional[str] = None
    mark: Optional[str] = None           # 用户指令标记：gray(暂不考虑→灰化) / strike(删除→删除线) / move(移动挂接)
    reason: Optional[str] = None         # 用户指令的简要理由（落 cell.data.cmd.reason）
    meta_ids: list = field(default_factory=list)         # 绑定原始论据 metadata_refs
    thought: Optional[str] = None
    confidence: Optional[float] = None
    importance_rationale: Optional[str] = None


@dataclass
class GraphUpdateOp:
    operations: list = field(default_factory=list)        # List[GraphOp]
    thought: str = ""


# ---------------------------------------------------------------------------
# 5. 关系图 cells（Store A，X6 fromJSON 同构）
# ---------------------------------------------------------------------------
@dataclass
class NodeData:
    type: str                    # point / evidence / issue / conclusion / action / conflict
    label: str
    speaker_ref: Optional[str] = None
    importance: dict = field(default_factory=lambda: {"level": "normal", "score": 0.0, "manual_override": False})
    mention_count: int = 1
    edit: dict = field(default_factory=lambda: {
        "text_edited": False, "type_edited": False,
        "position_frozen": False, "importance_override": False})
    lock: dict = field(default_factory=lambda: {"locked": False, "locked_by": None, "locked_at": None})
    resolved: bool = False
    metadata_refs: list = field(default_factory=list)     # meta_id 列表（utt_/agd_/man_）
    confidence: float = 1.0
    needs_confirmation: bool = False
    version: int = 1

    def __post_init__(self):
        if not self.resolved and self.confidence < 0.65:
            self.needs_confirmation = True


def node_data_to_dict(d: NodeData) -> dict:
    return {
        "type": d.type, "label": d.label, "speaker_ref": d.speaker_ref,
        "importance": d.importance, "mention_count": d.mention_count, "edit": d.edit,
        "lock": d.lock, "resolved": d.resolved, "metadata_refs": d.metadata_refs,
        "confidence": d.confidence, "needs_confirmation": d.needs_confirmation,
        "version": d.version,
    }


def make_node_cell(cell_id: str, data: NodeData, x: int = 0, y: int = 0) -> dict:
    """生成 X6 同构的节点 cell。cell id = 业务 id（Design_StructureGraph_Storage §3.2）。"""
    return {
        "id": cell_id,
        "shape": "amo-node",
        "position": {"x": x, "y": y},
        "size": {"width": 220, "height": 64},
        "data": node_data_to_dict(data),
    }


def make_edge_cell(cell_id: str, source: str, target: str, relation: str = "support") -> dict:
    return {
        "id": cell_id,
        "shape": "edge",
        "source": {"cell": source},
        "target": {"cell": target},
        "data": {"relation": relation},
    }


def make_structure_graph(graph_id: str, cells: list, version: int = 1) -> dict:
    """Store A 真相源序列化结构（Design_StructureGraph_Storage §3）。"""
    return {"schema": "amo.board/v1", "graph_id": graph_id, "version": version, "cells": cells}
