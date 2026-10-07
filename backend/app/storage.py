"""双存储实现。

Store A — 节点关系图（后处理派生结果，精炼，X6 cells 同构，见 Design_StructureGraph_Storage §3）
Store B — 语音文本元数据（原始输入，按 meta_id 索引，见同文档 §2）

本地 MVP 用 json 文件作真相源；生产换 jsonb，接口不变。
"""
from __future__ import annotations
import copy
import json
import logging
import math
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from .models import (
    GraphUpdateOp, GraphOp, NodeData, make_node_cell, make_edge_cell, node_data_to_dict, stable_hash,
)
from . import config
from .errors import safe_error_code

log = logging.getLogger("amo.storage")

MEETING_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


def _atomic_write_json(path: str, obj) -> None:
    """原子写 JSON：先写同目录临时文件再 os.replace。

    直接覆盖写若中途崩溃会留下半截文件，真相源即损坏；os.replace 在同文件系统内是原子的。
    """
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())      # 落盘后再 replace，断电不丢真相源
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

# 自动布局参数（与 make_node_cell 的 220×64 尺寸一致）
NODE_W = 220
NODE_H = 64
H_GAP = 40
V_GAP = 80
CENTER_X = 410          # 使单独一根节点落于 x=300（与 gen_initial_node 一致）
ROOT_Y = 40
ORPHAN_COLS = 5         # 孤立节点平铺每行数（避免纵向无限拉长）
SAVE_THROTTLE_MS = 300  # 写盘最小间隔：高频 cursor 流下合并多次写为一次


def is_valid_meeting_id(meeting_id: str) -> bool:
    return isinstance(meeting_id, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", meeting_id))


def _edge_endpoint(endpoint) -> Optional[str]:
    """边端点兼容 {"cell": id} 与裸字符串两种写法。"""
    if isinstance(endpoint, dict):
        return endpoint.get("cell")
    return endpoint


def _uses_parent_ids(index: dict) -> bool:
    return any("parent_id" in (cell.get("data") or {})
               for cell in index.values() if cell.get("shape") != "edge")


def _structural_pairs(index: dict) -> list[tuple[str, str]]:
    """Return unambiguous (parent, child) pairs, excluding semantic relations."""
    nodes = {cell.get("id") for cell in index.values() if cell.get("shape") != "edge"}
    pairs: list[tuple[str, str]] = []
    if _uses_parent_ids(index):
        for cell in index.values():
            if cell.get("shape") == "edge":
                continue
            child = cell.get("id")
            parent = (cell.get("data") or {}).get("parent_id")
            if child in nodes and parent in nodes and child != parent:
                pairs.append((parent, child))
        return pairs

    parents: dict[str, set[str]] = {}
    for cell in index.values():
        if cell.get("shape") != "edge" or (cell.get("data") or {}).get("relation") != "subordinate":
            continue
        source = _edge_endpoint(cell.get("source"))
        target = _edge_endpoint(cell.get("target"))
        if source in nodes and target in nodes and source != target:
            parents.setdefault(target, set()).add(source)
    return [(next(iter(candidates)), child) for child, candidates in parents.items()
            if len(candidates) == 1]


def migrate_board_v1_to_v2(document: dict) -> dict:
    """Convert a v1 snapshot without mutating it or guessing ambiguous parents.

    Legacy ``subordinate`` edges carry structure; all other edge relations are
    semantic and survive conversion unchanged. Nodes with multiple parents,
    missing parents, or participation in a cycle are placed in the explicit
    review state (``parent_id=None``, ``needs_parent_review=True``).
    """
    if not isinstance(document, dict) or document.get("schema") != "amo.board/v1":
        raise ValueError("expected amo.board/v1 document")
    migrated = copy.deepcopy(document)
    cells = migrated.get("cells")
    if not isinstance(cells, list):
        raise ValueError("board cells must be a list")
    nodes = {cell.get("id"): cell for cell in cells
             if isinstance(cell, dict) and cell.get("shape") != "edge" and isinstance(cell.get("id"), str)}
    if len(nodes) != sum(1 for cell in cells if isinstance(cell, dict) and cell.get("shape") != "edge"):
        raise ValueError("board node ids must be unique strings")
    issues = sorted(node_id for node_id, cell in nodes.items()
                    if cell.get("data", {}).get("type") == "issue")
    root_id = "n_issue_root" if "n_issue_root" in nodes else (issues[0] if issues else None)

    incoming: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    semantic_edges = []
    for cell in cells:
        if not isinstance(cell, dict) or cell.get("shape") != "edge":
            continue
        relation = (cell.get("data") or {}).get("relation")
        source, target = _edge_endpoint(cell.get("source")), _edge_endpoint(cell.get("target"))
        if relation == "subordinate":
            if source in nodes and target in nodes:
                incoming[target].append(source)
            continue
        semantic_edges.append(cell)

    parents: dict[str, str] = {}
    ambiguous: set[str] = set()
    for node_id, cell in nodes.items():
        existing = (cell.get("data") or {}).get("parent_id")
        candidates = sorted(set(incoming[node_id]))
        if node_id == root_id and not candidates and existing is None:
            continue
        if existing in nodes and not candidates:
            candidates = [existing]
        if len(candidates) == 1 and candidates[0] != node_id:
            parents[node_id] = candidates[0]
        else:
            ambiguous.add(node_id)

    # Inspect every original candidate edge, including ambiguous multi-parent
    # edges, so pruning ambiguity cannot hide a cycle member.
    cycle_nodes: set[str] = set()
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(node_id: str) -> None:
        state[node_id] = 1
        path.append(node_id)
        for parent_id in sorted(set(incoming[node_id])):
            if state.get(parent_id, 0) == 1:
                cycle_nodes.update(path[path.index(parent_id):])
            elif state.get(parent_id, 0) == 0:
                visit(parent_id)
        path.pop()
        state[node_id] = 2

    for node_id in nodes:
        if state.get(node_id, 0) == 0:
            visit(node_id)

    review = ambiguous | cycle_nodes
    for node_id, cell in nodes.items():
        data = cell.setdefault("data", {})
        if node_id in review:
            data["parent_id"] = None
            data["needs_parent_review"] = True
        else:
            data["parent_id"] = parents.get(node_id)
            data.pop("needs_parent_review", None)

    migrated["schema"] = "amo.board/v2"
    migrated["cells"] = [cell for cell in cells if cell.get("shape") != "edge"] + semantic_edges
    return migrated


def _is_descendant(index: dict, candidate: str, ancestor: str) -> bool:
    """Return whether candidate is below ancestor in the structural tree only."""
    children: dict = {}
    for parent, child in _structural_pairs(index):
        children.setdefault(parent, []).append(child)
    seen, stack = set(), [ancestor]
    while stack:
        for ch in children.get(stack.pop(), []):
            if ch == candidate:
                return True
            if ch not in seen:
                seen.add(ch)
                stack.append(ch)
    return False


def validate_parent_assignment(index: dict, node_id: str, parent_id: str) -> str:
    """Validate one structural move against nodes in the current meeting."""
    node = index.get(node_id)
    parent = index.get(parent_id)
    if node is None or node.get("shape") == "edge":
        raise ValueError("node not found")
    if parent is None or parent.get("shape") == "edge":
        raise ValueError("parent not found")
    if node_id == "n_issue_root":
        raise ValueError("root node cannot be moved")
    if node_id == parent_id:
        raise ValueError("node cannot parent itself")
    if _is_descendant(index, parent_id, node_id):
        raise ValueError("parent assignment would create a cycle")
    return parent_id


class VersionConflict(RuntimeError):
    def __init__(self, current_version: int, cells: list):
        super().__init__("board version changed")
        self.current_version = current_version
        self.cells = copy.deepcopy(cells)


# 触发游离修复的结构性算子：内容型 sync 才修；纯光标/无操作批次不动图（保护 DP-11 语义）
_STRUCTURAL_OPS = {"add_node", "link", "move_node", "merge_as_duplicate"}


def _repair_orphans(index: dict, max_passes: int = 4) -> list:
    """把游离节点自动挂回议题树（P2①，开关 config.auto_repair_orphans）。

    游离按唯一结构关系判断：v1 只使用 subordinate，v2 只使用 parent_id；语义边不建立树归属。
    父节点优先级：共享 metadata_refs 的 issue（共享数多者优先，id 升序稳定）
    → 任意 issue（n_issue_root 优先）→ 根。
    多趟（默认 4）：一趟只能并入「父已在主树」的孤儿；父本身游离的待其挂回后
    下一趟整棵并入。防环：候选父若是该孤儿的子孙则跳过（复用 _is_descendant）。
    返回修复记录列表（写入 receipt.repaired_orphans）。
    """
    repaired = []
    for _ in range(max_passes):
        nodes = [c for c in index.values() if c.get("shape") != "edge"]
        if not nodes:
            break
        if not _uses_parent_ids(index):
            parent_candidates: dict[str, set[str]] = {}
            for cell in index.values():
                if cell.get("shape") != "edge" or (cell.get("data") or {}).get("relation") != "subordinate":
                    continue
                source = _edge_endpoint(cell.get("source"))
                target = _edge_endpoint(cell.get("target"))
                if source in index and target in index:
                    parent_candidates.setdefault(target, set()).add(source)
            for node_id, parents in parent_candidates.items():
                if len(parents) > 1 and node_id in index:
                    index[node_id].setdefault("data", {})["needs_parent_review"] = True
        node_ids = {c["id"] for c in nodes}
        adj: dict = {nid: [] for nid in node_ids}        # 无向连通性（与 relayout 同判据）
        for parent, child in _structural_pairs(index):
            if parent in adj and child in adj:
                adj[parent].append(child)
                adj[child].append(parent)
        if "n_issue_root" in node_ids:
            root = "n_issue_root"
        else:
            root = next((c["id"] for c in nodes if c.get("data", {}).get("type") == "issue"),
                         None) or nodes[0]["id"]
        seen, stack = {root}, [root]
        while stack:
            for nb in adj[stack.pop()]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        orphans = sorted((c for c in nodes if c["id"] not in seen
                          and not c.get("data", {}).get("needs_parent_review")
                          and field_writable(c, "parent_id")),
                         key=lambda c: c["id"])
        if not orphans:
            break
        issues = [c for c in nodes if c.get("data", {}).get("type") == "issue"]
        fallback = sorted(issues, key=lambda c: (c["id"] != "n_issue_root", c["id"]))
        fixed = 0
        for orphan in orphans:
            refs = set(orphan.get("data", {}).get("metadata_refs") or [])
            shared = [c for c in issues
                      if refs & set(c.get("data", {}).get("metadata_refs") or [])]
            shared.sort(key=lambda c: (-len(refs & set(c.get("data", {}).get("metadata_refs") or [])),
                                       c["id"]))
            parent = next((c for c in shared + fallback + [index[root]]
                           if c["id"] != orphan["id"]
                           and not _is_descendant(index, c["id"], orphan["id"])), None)
            if parent is None:
                continue
            if _uses_parent_ids(index):
                orphan.setdefault("data", {})["parent_id"] = parent["id"]
                eid = None
            else:
                eid = f"e_{parent['id']}__{orphan['id']}"
                if eid not in index:
                    index[eid] = make_edge_cell(eid, parent["id"], orphan["id"], "subordinate")
            repair_record = {"node": orphan["id"], "parent": parent["id"]}
            if eid is not None:
                repair_record["edge"] = eid
            repaired.append(repair_record)
            fixed += 1
            adj[parent["id"]].append(orphan["id"])       # 就地刷新连通性，同趟后续判断更准
            adj[orphan["id"]].append(parent["id"])
            if parent["id"] in seen:
                seen.add(orphan["id"])
        if fixed == 0:
            break
    return repaired


def _align_evidence_parents(index: dict) -> list:
    """Use an unambiguous point→evidence support/oppose link as tree ownership.

    The semantic link still records *why* the evidence belongs there. This
    corrects an LLM's broad issue-level parent without guessing from text.
    """
    sources: dict[str, set[str]] = {}
    for edge in index.values():
        if edge.get("shape") != "edge" or (edge.get("data") or {}).get("relation") not in {"support", "oppose"}:
            continue
        source, target = _edge_endpoint(edge.get("source")), _edge_endpoint(edge.get("target"))
        if ((index.get(source) or {}).get("data") or {}).get("type") != "point":
            continue
        if ((index.get(target) or {}).get("data") or {}).get("type") != "evidence":
            continue
        sources.setdefault(target, set()).add(source)
    aligned = []
    for target, parents in sources.items():
        if len(parents) != 1 or not field_writable(index[target], "parent_id"):
            continue
        parent = next(iter(parents))
        if _is_descendant(index, parent, target):
            continue
        if _uses_parent_ids(index):
            old = (index[target].get("data") or {}).get("parent_id")
            if old == parent:
                continue
            index[target].setdefault("data", {})["parent_id"] = parent
        else:
            incoming = [(eid, edge) for eid, edge in index.items()
                        if edge.get("shape") == "edge"
                        and (edge.get("data") or {}).get("relation") == "subordinate"
                        and _edge_endpoint(edge.get("target")) == target]
            if len(incoming) == 1 and _edge_endpoint(incoming[0][1].get("source")) == parent:
                continue
            for eid, _ in incoming:
                index.pop(eid, None)
            eid = f"e_{parent}__{target}"
            if eid in index:
                eid = f"e_tree_{parent}__{target}"
            index[eid] = make_edge_cell(eid, parent, target, "subordinate")
        aligned.append({"node": target, "parent": parent})
    return aligned


# ---------------------------------------------------------------------------
# lock/edit 仲裁（StoreA 落库与 GraphTools 工具层共用，避免双份真相）
# ---------------------------------------------------------------------------
def is_locked(cell: dict) -> bool:
    """节点是否处于持久锁（用户锁定，AI 一律不写）。"""
    return bool(cell.get("data", {}).get("lock", {}).get("locked"))


def field_writable(cell: dict, field: str) -> bool:
    """字段是否可被 AI 覆盖。field ∈ {text, type, importance}。

    用户手改过的字段（edit.*_edited / importance_override）优先，AI 不覆盖。
    """
    data = cell.get("data", {})
    edit = data.get("edit", {})
    manual = data.get("manual_override", {})
    if field == "text":
        return not (edit.get("text_edited") or manual.get("label"))
    if field == "type":
        return not (edit.get("type_edited") or manual.get("type"))
    if field == "parent_id":
        return not manual.get("parent_id")
    if field == "status":
        return not manual.get("status")
    if field == "importance":
        return not (edit.get("importance_override") or manual.get("importance")
                    or data.get("importance", {}).get("manual_override"))
    return True


def op_blocked_by_lock_or_edit(cell: dict, op: GraphOp, index: Optional[dict] = None) -> bool:
    """Single AI-write policy shared by graph tools and the storage commit boundary."""
    if is_locked(cell):
        return True
    if op.op == "replace":
        # Apply non-protected fields and evidence independently; a manual label
        # must not block a valid type update or new source references.
        return False
    if op.op == "set_importance":
        return not field_writable(cell, "importance")
    if op.op in {"move_node", "link"} and (op.op == "move_node" or op.relation == "subordinate"):
        return not field_writable(cell, "parent_id")
    if op.op == "link" and index:
        source = index.get(op.source)
        if source and is_locked(source):
            return True
    if op.op == "move_node" and index:
        parent = index.get(op.parent)
        if parent and is_locked(parent):
            return True
    if op.op == "merge_as_duplicate":
        # A merge removes the duplicate node, so any manual field on either side
        # protects it from deletion even when the surviving node would be writable.
        target = (index or {}).get(op.parent)
        if any(is_locked(item) or any(not field_writable(item, f) for f in
                   ("text", "type", "parent_id", "importance", "status"))
               for item in (cell, target) if item is not None):
            return True
    return False


def relayout(cells: list) -> list:
    """重算节点坐标：以议题为根、按结构父级 BFS 分层，同层水平居中。

    只改 position，不动 id / 数量 / cells 顺序；position_frozen 的节点保持原位。
    孤立节点（不与根连通）依次各自成层，避免与主树重叠。
    """
    nodes = [c for c in cells if c.get("shape") != "edge"]
    if not nodes:
        return cells
    by_id = {c["id"]: c for c in nodes}
    index = {c["id"]: c for c in cells}
    adj: dict = {cid: [] for cid in by_id}
    for parent, child in _structural_pairs(index):
        if parent in adj and child in adj:
            adj[parent].append(child)        # 无向：方向不敏感，仅用于分层
            adj[child].append(parent)

    # 根：优先 n_issue_root，其次首个 issue，再次首个节点
    if "n_issue_root" in by_id:
        root = "n_issue_root"
    else:
        root = next((c["id"] for c in nodes if c.get("data", {}).get("type") == "issue"), None)
        if root is None:
            root = nodes[0]["id"]

    order_index = {c["id"]: i for i, c in enumerate(nodes)}
    levels: list = []
    seen = {root}
    front = [root]
    while front:
        levels.append(front)
        nxt = []
        for nid in front:
            for nb in adj[nid]:
                if nb not in seen:
                    seen.add(nb)
                    nxt.append(nb)
        nxt.sort(key=lambda x: order_index[x])   # 稳定：按原始出现顺序
        front = nxt

    for lv, ids in enumerate(levels):
        movable = [cid for cid in ids
                   if not is_locked(by_id[cid])
                   and not by_id[cid].get("data", {}).get("edit", {}).get("position_frozen")]
        if not movable:
            continue
        y = ROOT_Y + lv * (NODE_H + V_GAP)
        span = len(movable) * NODE_W + (len(movable) - 1) * H_GAP
        left = CENTER_X - span / 2
        for i, cid in enumerate(movable):
            by_id[cid]["position"] = {
                "x": int(round(left + i * (NODE_W + H_GAP))),
                "y": int(y),
            }

    # 孤立节点（不与根连通）横向平铺在主树下方，避免各自成层把画布纵向无限拉长
    orphans = [c["id"] for c in nodes if c["id"] not in seen]
    if orphans:
        base_y = ROOT_Y + len(levels) * (NODE_H + V_GAP)
        cols = max(1, ORPHAN_COLS)
        span = cols * NODE_W + (cols - 1) * H_GAP
        left = CENTER_X - span / 2
        for i, cid in enumerate(orphans):
            cell = by_id[cid]
            if is_locked(cell) or cell.get("data", {}).get("edit", {}).get("position_frozen"):
                continue
            row, col = divmod(i, cols)
            cell["position"] = {
                "x": int(round(left + col * (NODE_W + H_GAP))),
                "y": int(base_y + row * (NODE_H + V_GAP)),
            }
    return cells


class StoreA:
    """关系图真相源。cell id = 业务 id（Design_StructureGraph_Storage §3.2）。"""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        # 内存真相缓存：避免每次读盘；写盘按 SAVE_THROTTLE_MS 合并（高频 cursor 流下显著减 IO）
        self._cache: dict = {}
        self._versions: dict = {}
        self._dirty: set = set()
        self._last_flush: dict = {}
        self._titles: dict = {}
        self._updated_at: dict = {}
        self._schemas: dict = {}
        self._meeting_status: dict = {}
        self._agendas: dict = {}
        self._agenda_texts: dict = {}
        self._lock = threading.RLock()

    def _history_path(self, graph_id: str) -> str:
        return os.path.join(self.root, f"{graph_id}.history.json")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _path(self, graph_id: str) -> str:
        if not is_valid_meeting_id(graph_id):
            raise ValueError("invalid meeting_id")
        return os.path.join(self.root, f"{graph_id}.board.json")

    def _migration_backup_path(self, graph_id: str) -> str:
        return f"{self._path(graph_id)}.v1-backup"

    @staticmethod
    def _write_bytes_atomic(path: str, content: bytes, *, exclusive: bool = False) -> None:
        directory = os.path.dirname(path) or "."
        fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".backup")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            if exclusive:
                os.link(temp_path, path)
                os.unlink(temp_path)
            else:
                os.replace(temp_path, path)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    def load(self, graph_id: str) -> list:
        # 返回深拷贝：调用方（广播/工具/前端快照）可能原地改 cells，
        # 若直接吐缓存引用会污染真相源，后续增量 diff 全部失真。
        if graph_id in self._cache:
            return copy.deepcopy(self._cache[graph_id])
        p = self._path(graph_id)
        cells = []
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                cells = doc.get("cells", [])
                self._versions[graph_id] = int(doc.get("version", 1))
                self._titles[graph_id] = doc.get("title") or ""
                self._updated_at[graph_id] = doc.get("updated_at")
                self._schemas[graph_id] = doc.get("schema", "amo.board/v1")
                self._meeting_status[graph_id] = doc.get("meeting_status", "draft")
                self._agendas[graph_id] = doc.get("agenda", [])
                self._agenda_texts[graph_id] = doc.get("agenda_text", "")
            except Exception as e:
                log.warning("[StoreA] load %s failed (%s)", graph_id, safe_error_code(e))
                # Never cache a corrupt existing board as an empty meeting: a
                # later save would otherwise overwrite the only recovery copy.
                raise RuntimeError("board_storage_unreadable") from None
        self._cache[graph_id] = cells
        self._schemas.setdefault(graph_id, "amo.board/v1")
        self._meeting_status.setdefault(graph_id, "draft")
        self._agendas.setdefault(graph_id, [])
        self._agenda_texts.setdefault(graph_id, "")
        return copy.deepcopy(cells)

    def exists(self, graph_id: str) -> bool:
        return graph_id in self._dirty or os.path.exists(self._path(graph_id))

    def version(self, graph_id: str) -> int:
        self.load(graph_id)
        return self._versions.get(graph_id, 0)

    def updated_at(self, graph_id: str) -> Optional[str]:
        self.load(graph_id)
        return self._updated_at.get(graph_id)

    def save(self, graph_id: str, cells: list, version: Optional[int] = None) -> None:
        """更新内存真相并（节流）落盘。version 缺省按单调递增生成。"""
        if graph_id not in self._cache and os.path.exists(self._path(graph_id)):
            self.load(graph_id)
        self._cache[graph_id] = cells
        self._versions[graph_id] = (self._versions.get(graph_id, 0) + 1) if version is None else version
        self._dirty.add(graph_id)
        self.flush(graph_id, force=False)

    def flush(self, graph_id: str, force: bool = True) -> None:
        """把脏数据落盘。force=False 时受 SAVE_THROTTLE_MS 约束（合并高频写）。"""
        if graph_id not in self._dirty:
            return
        now = time.monotonic()
        if not force and now - self._last_flush.get(graph_id, 0.0) < SAVE_THROTTLE_MS / 1000.0:
            return
        p = self._path(graph_id)
        self._updated_at[graph_id] = self._now_iso()
        _atomic_write_json(p, {"schema": self._schemas.get(graph_id, "amo.board/v1"), "graph_id": graph_id,
                               "version": self._versions.get(graph_id, 1),
                               "title": self._titles.get(graph_id) or None,
                               "meeting_status": self._meeting_status.get(graph_id, "draft"),
                               "agenda": self._agendas.get(graph_id, []),
                               "agenda_text": self._agenda_texts.get(graph_id, ""),
                               "updated_at": self._updated_at[graph_id],
                               "cells": self._cache.get(graph_id, [])})
        self._last_flush[graph_id] = now
        self._dirty.discard(graph_id)

    def flush_all(self) -> None:
        """兜底：把全部脏看板强制落盘（周期任务/关停时调用，保证最终一致）。"""
        for gid in list(self._dirty):
            try:
                self.flush(gid, force=True)
            except Exception as e:
                log.warning("[StoreA] flush %s failed (%s)", gid, safe_error_code(e))

    def list_meetings(self) -> list:
        """返回本地会议列表；旧版看板从议题根节点推导标题。"""
        ids = set(self._dirty)
        suffix = ".board.json"
        for name in os.listdir(self.root):
            if name.endswith(suffix):
                ids.add(name[:-len(suffix)])
        meetings = []
        for graph_id in ids:
            if not is_valid_meeting_id(graph_id):
                continue
            cells = self.load(graph_id)
            nodes = [c for c in cells if c.get("shape") != "edge"]
            edges = [c for c in cells if c.get("shape") == "edge"]
            title = self._titles.get(graph_id) or ""
            if not title:
                root = next((c for c in nodes if c.get("id") == "n_issue_root"), None)
                if root is None:
                    root = next((c for c in nodes if (c.get("data") or {}).get("type") == "issue"), None)
                title = (root or {}).get("data", {}).get("label") or graph_id
            types = {}
            for cell in nodes:
                kind = (cell.get("data") or {}).get("type")
                if kind:
                    types[kind] = types.get(kind, 0) + 1
            meetings.append({
                "meeting_id": graph_id,
                "schema": self._schemas.get(graph_id, "amo.board/v1"),
                "title": title,
                "status": self._meeting_status.get(graph_id, "draft"),
                "agenda": copy.deepcopy(self._agendas.get(graph_id, [])),
                "version": self._versions.get(graph_id, 0),
                "updated_at": self._updated_at.get(graph_id),
                "stats": {"nodes": len(nodes), "edges": len(edges), "types": types},
            })
        meetings.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return meetings

    def title(self, graph_id: str) -> Optional[str]:
        self.load(graph_id)
        return self._titles.get(graph_id) or None

    def migration_preview(self, graph_id: str) -> Optional[dict]:
        """Describe a v1→v2 conversion without changing its source snapshot."""
        if not self.exists(graph_id):
            return None
        self.load(graph_id)
        if graph_id in self._cache:
            source = {
                "schema": self._schemas.get(graph_id, "amo.board/v1"),
                "graph_id": graph_id,
                "version": self._versions.get(graph_id, 1),
                "title": self._titles.get(graph_id) or None,
                "updated_at": self._updated_at.get(graph_id),
                "cells": copy.deepcopy(self._cache[graph_id]),
            }
        else:
            with open(self._path(graph_id), "r", encoding="utf-8") as source_file:
                source = json.load(source_file)
        schema = source.get("schema", "amo.board/v1")
        if schema != "amo.board/v1":
            raise ValueError(f"migration preview requires amo.board/v1, got {schema}")
        migrated = migrate_board_v1_to_v2(source)
        before = {cell.get("id"): cell for cell in source.get("cells", [])
                  if isinstance(cell, dict) and cell.get("shape") != "edge"}
        after = {cell.get("id"): cell for cell in migrated["cells"]
                 if isinstance(cell, dict) and cell.get("shape") != "edge"}
        parent_changes = []
        for node_id in sorted(after):
            old_parent = (before[node_id].get("data") or {}).get("parent_id")
            new_parent = (after[node_id].get("data") or {}).get("parent_id")
            if old_parent != new_parent:
                parent_changes.append({"node_id": node_id, "from": old_parent, "to": new_parent})
        needs_review = sorted(node_id for node_id, cell in after.items()
                              if cell.get("data", {}).get("needs_parent_review"))

        def metadata_ref_count(cells: list) -> int:
            return sum(len((cell.get("data") or {}).get("metadata_refs") or [])
                       for cell in cells if cell.get("shape") != "edge")

        semantic_edges = sum(1 for cell in migrated["cells"] if cell.get("shape") == "edge")
        return {
            "from_schema": schema,
            "to_schema": "amo.board/v2",
            "source_version": int(source.get("version", 1)),
            "parent_changes": parent_changes,
            "needs_review": needs_review,
            "semantic_edges_preserved": semantic_edges,
            "metadata_refs_before": metadata_ref_count(source["cells"]),
            "metadata_refs_after": metadata_ref_count(migrated["cells"]),
        }

    def accept_v1_migration(self, graph_id: str, *, expected_version: int) -> dict:
        """Back up and migrate one v1 board after an explicit version check.

        Callers must deploy v2 readers before exposing this operation.
        """
        if not self.exists(graph_id):
            raise KeyError("meeting not found")
        cells = self.load(graph_id)
        version = self._versions.get(graph_id, 1)
        if version != expected_version:
            raise RuntimeError(f"version conflict: expected {expected_version}, current {version}")
        if self._schemas.get(graph_id, "amo.board/v1") != "amo.board/v1":
            raise RuntimeError("migration requires amo.board/v1")

        path = self._path(graph_id)
        backup_path = self._migration_backup_path(graph_id)
        if os.path.exists(backup_path):
            with open(backup_path, "rb") as f:
                prior_backup = f.read()
            if graph_id in self._dirty or not os.path.exists(path):
                raise FileExistsError(backup_path)
            with open(path, "rb") as f:
                current_bytes = f.read()
            if prior_backup != current_bytes:
                raise FileExistsError(backup_path)
        elif graph_id not in self._dirty and os.path.exists(path):
            with open(path, "rb") as f:
                original_bytes = f.read()
        else:
            source = {"schema": "amo.board/v1", "graph_id": graph_id,
                      "version": version, "title": self._titles.get(graph_id) or None,
                      "updated_at": self._updated_at.get(graph_id), "cells": cells}
            original_bytes = json.dumps(source, ensure_ascii=False, indent=2).encode("utf-8")
        if not os.path.exists(backup_path):
            self._write_bytes_atomic(backup_path, original_bytes, exclusive=True)

        source = {"schema": "amo.board/v1", "graph_id": graph_id,
                  "version": version, "title": self._titles.get(graph_id) or None,
                  "updated_at": self._updated_at.get(graph_id), "cells": cells}
        migrated = migrate_board_v1_to_v2(source)
        self._append_version_record(graph_id, cells)
        _atomic_write_json(path, migrated)
        self._cache[graph_id] = migrated["cells"]
        self._schemas[graph_id] = "amo.board/v2"
        self._dirty.discard(graph_id)
        return {"graph_id": graph_id, "schema": "amo.board/v2", "version": version,
                "backup_path": backup_path, "needs_review": sorted(
                    c["id"] for c in migrated["cells"]
                    if c.get("shape") != "edge" and c.get("data", {}).get("needs_parent_review"))}

    def rollback_v1_migration(self, graph_id: str, *, expected_version: int) -> dict:
        """Restore the v1 backup only while the migrated board is unchanged."""
        if not self.exists(graph_id):
            raise KeyError("meeting not found")
        self.load(graph_id)
        if self._schemas.get(graph_id) != "amo.board/v2":
            raise RuntimeError("board is not an amo.board/v2 migration")
        current_version = self._versions.get(graph_id, 1)
        if current_version != expected_version:
            raise RuntimeError(f"version conflict: expected {expected_version}, current {current_version}")
        backup_path = self._migration_backup_path(graph_id)
        if not os.path.exists(backup_path):
            raise FileNotFoundError(backup_path)
        with open(backup_path, "rb") as f:
            original_bytes = f.read()
        original = json.loads(original_bytes)
        if original.get("schema") != "amo.board/v1":
            raise ValueError("migration backup is not amo.board/v1")

        safety_path = os.path.join(self.root, f"{graph_id}.board.v2-before-rollback-{secrets.token_hex(4)}")
        with open(self._path(graph_id), "rb") as f:
            safety_bytes = f.read()
        self._write_bytes_atomic(safety_path, safety_bytes, exclusive=True)
        self._append_version_record(graph_id, self.load(graph_id))
        self._write_bytes_atomic(self._path(graph_id), original_bytes)
        self._cache.pop(graph_id, None)
        self._versions.pop(graph_id, None)
        self._schemas.pop(graph_id, None)
        self._titles.pop(graph_id, None)
        self._updated_at.pop(graph_id, None)
        self._meeting_status.pop(graph_id, None)
        self._agendas.pop(graph_id, None)
        self._agenda_texts.pop(graph_id, None)
        self._dirty.discard(graph_id)
        self.load(graph_id)
        return {"graph_id": graph_id, "schema": "amo.board/v1",
                "version": self._versions.get(graph_id, 1), "safety_backup_path": safety_path}

    def create_meeting(self, graph_id: str, title: str, agenda: Optional[list[str]] = None,
                       agenda_text: str = "") -> dict:
        if not is_valid_meeting_id(graph_id):
            raise ValueError("invalid meeting_id")
        if self.exists(graph_id):
            raise FileExistsError(graph_id)
        clean_title = title.strip()
        clean_agenda = [str(item).strip() for item in (agenda or []) if str(item).strip()]
        root = make_node_cell("n_issue_root", NodeData(type="issue", label=clean_title,
                                                        metadata_refs=[]), x=300, y=40)
        cells = [root]
        for index, topic in enumerate(clean_agenda):
            topic_id = f"n_agenda_{index + 1}_{stable_hash(topic):x}"
            x = 300 + ((index % 3) - 1) * 270
            y = 150 + (index // 3) * 150
            cells.append(make_node_cell(topic_id, NodeData(type="issue", label=topic,
                                                           metadata_refs=[]), x=x, y=y))
            cells.append(make_edge_cell(f"e_agenda_{index + 1}", "n_issue_root", topic_id, "subordinate"))
        self._cache[graph_id] = cells
        self._versions[graph_id] = 1
        self._schemas[graph_id] = "amo.board/v1"
        self._titles[graph_id] = clean_title
        self._meeting_status[graph_id] = "draft"
        self._agendas[graph_id] = clean_agenda
        self._agenda_texts[graph_id] = agenda_text
        self._updated_at[graph_id] = self._now_iso()
        self._dirty.add(graph_id)
        self.flush(graph_id, force=True)
        return {"meeting_id": graph_id, "title": self._titles[graph_id],
                "version": self._versions[graph_id], "updated_at": self._updated_at[graph_id],
                "status": self._meeting_status[graph_id], "agenda": copy.deepcopy(self._agendas[graph_id]),
                "agenda_text": self._agenda_texts[graph_id]}

    def meeting_details(self, graph_id: str) -> Optional[dict]:
        if not self.exists(graph_id):
            return None
        self.load(graph_id)
        return {"meeting_id": graph_id, "title": self.title(graph_id),
                "status": self._meeting_status.get(graph_id, "draft"),
                "agenda": copy.deepcopy(self._agendas.get(graph_id, [])),
                "agenda_text": self._agenda_texts.get(graph_id, ""),
                "version": self.version(graph_id), "updated_at": self.updated_at(graph_id)}

    def set_meeting_status(self, graph_id: str, status: str) -> dict:
        if status not in {"live", "ended"}:
            raise ValueError("status must be live or ended")
        with self._lock:
            details = self.meeting_details(graph_id)
            if details is None:
                raise KeyError(graph_id)
            current = details["status"]
            allowed = {"draft": {"live", "ended"}, "live": {"ended"}, "ended": {"live"}}
            if status != current and status not in allowed.get(current, set()):
                raise ValueError(f"invalid meeting status transition: {current} -> {status}")
            if status == current:
                return details
            self._meeting_status[graph_id] = status
            self._updated_at[graph_id] = self._now_iso()
            self._dirty.add(graph_id)
            self.flush(graph_id, force=True)
            self._append_version_record(graph_id, self.load(graph_id), {
                "source": "lifecycle", "actor": "host", "time": self._updated_at[graph_id],
                "reason": f"会议状态：{current} → {status}",
                "fields": {"meeting_status": {"before": current, "after": status}},
            })
            return self.meeting_details(graph_id) or {}

    def rename_meeting(self, graph_id: str, title: str) -> Optional[dict]:
        if not self.exists(graph_id):
            return None
        clean_title = title.strip()
        cells = self.load(graph_id)
        self._append_version_record(graph_id, cells)
        self._titles[graph_id] = clean_title
        root = next((c for c in cells if c.get("id") == "n_issue_root"), None)
        if root is None:
            root = next((c for c in cells if c.get("shape") != "edge"
                         and (c.get("data") or {}).get("type") == "issue"), None)
        if root is not None:
            data = root.setdefault("data", {})
            data["label"] = clean_title
            data.setdefault("edit", {})["text_edited"] = True
        self.save(graph_id, cells)
        self.flush(graph_id, force=True)
        return {"meeting_id": graph_id, "title": clean_title,
                "version": self._versions.get(graph_id, 1), "updated_at": self._updated_at.get(graph_id)}

    def _merge_user_edits(self, cells: list, edits: Optional[dict]) -> list:
        """仅合并用户在画布上的位置与导线改动，不覆盖同时生成的 AI 节点内容。"""
        if not edits:
            return cells
        nodes = {c["id"]: c for c in cells if c.get("shape") != "edge"}
        removed = {edge_id for edge_id in (edits.get("removed_edge_ids") or [])
                   if isinstance(edge_id, str)}
        cells = [c for c in cells if c.get("shape") != "edge" or c.get("id") not in removed]
        positions = edits.get("node_positions") or {}
        if not isinstance(positions, dict):
            positions = {}
        for node_id, pos in positions.items():
            if node_id not in nodes or not isinstance(pos, dict):
                continue
            x, y = pos.get("x"), pos.get("y")
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (x, y)):
                continue
            nodes[node_id]["position"] = {"x": x, "y": y}
            nodes[node_id].setdefault("data", {}).setdefault("edit", {})["position_frozen"] = True
        present = {c.get("id") for c in cells}
        for edge in edits.get("added_edges") or []:
            if not isinstance(edge, dict):
                continue
            edge_id = edge.get("id")
            source, target = edge.get("source"), edge.get("target")
            relation = edge.get("relation", "support")
            if not isinstance(edge_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", edge_id):
                continue
            if (edge_id in present or not isinstance(source, str) or not isinstance(target, str)
                    or source not in nodes or target not in nodes or source == target
                    or relation not in {"support", "oppose", "subordinate", "duplicate", "replace", "derive"}):
                continue
            cells.append(make_edge_cell(edge_id, source, target, relation))
            present.add(edge_id)
        return cells

    def save_snapshot(self, graph_id: str, edits: Optional[dict] = None) -> Optional[dict]:
        if not self.exists(graph_id):
            return None
        cells = self._merge_user_edits(self.load(graph_id), edits)
        self.save(graph_id, cells)
        self.flush(graph_id, force=True)
        return self._append_version_record(graph_id, cells)

    def _append_version_record(self, graph_id: str, cells: list, change: Optional[dict] = None) -> dict:
        record = {"version": self._versions.get(graph_id, 1),
                  "schema": self._schemas.get(graph_id, "amo.board/v1"),
                  "created_at": self._updated_at.get(graph_id) or self._now_iso(),
                  "title": self._titles.get(graph_id) or graph_id,
                  "meeting_status": self._meeting_status.get(graph_id, "draft"),
                  "agenda": copy.deepcopy(self._agendas.get(graph_id, [])),
                  "cells": copy.deepcopy(cells)}
        if change is not None:
            record["change"] = copy.deepcopy(change)
        path = self._history_path(graph_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
        except (OSError, ValueError):
            records = []
        records.append(record)
        _atomic_write_json(path, records)
        return record

    def patch_node(self, graph_id: str, node_id: str, fields: dict, *,
                   expected_version: int, actor: str = "human", reason: str = "") -> list:
        """Optimistically patch editable node fields and preserve a field-level audit record."""
        allowed = {"label", "type", "parent_id", "importance", "status"}
        if not isinstance(fields, dict) or not fields or set(fields) - allowed:
            raise ValueError("invalid editable fields")
        with self._lock:
            current_version = self.version(graph_id)
            current = self.load(graph_id)
            if current_version != expected_version:
                raise VersionConflict(current_version, current)
            candidate = copy.deepcopy(current)
            index = {cell.get("id"): cell for cell in candidate}
            cell = index.get(node_id)
            if cell is None or cell.get("shape") == "edge":
                raise KeyError("node not found")
            data = cell.setdefault("data", {})
            before_after = {}
            for field, value in fields.items():
                old = copy.deepcopy(data.get(field))
                if field == "label":
                    value = value.strip() if isinstance(value, str) else ""
                    if not value or len(value) > 200:
                        raise ValueError("invalid label")
                elif field == "type":
                    if value not in {"point", "evidence", "issue", "conclusion", "action", "conflict"}:
                        raise ValueError("invalid node type")
                elif field == "importance":
                    if value not in {"high", "normal", "low"}:
                        raise ValueError("invalid importance")
                    importance = copy.deepcopy(data.get("importance") or {})
                    importance["level"] = value
                    importance["manual_override"] = True
                    data["importance"] = importance
                    old = old.get("level") if isinstance(old, dict) else old
                elif field == "status":
                    if value not in {"open", "confirmed", "resolved", "dismissed", "in_progress"}:
                        raise ValueError("invalid status")
                elif field == "parent_id":
                    if not _uses_parent_ids(index):
                        old = next((parent for parent, child in _structural_pairs(index)
                                    if child == node_id), None)
                    if value is not None:
                        validate_parent_assignment(index, node_id, value)
                    elif node_id == "n_issue_root":
                        raise ValueError("root parent cannot be changed")
                    if _uses_parent_ids(index):
                        data["parent_id"] = value
                        data.pop("needs_parent_review", None)
                    else:
                        old_parents = [edge_id for edge_id, edge in index.items()
                                       if edge.get("shape") == "edge"
                                       and (edge.get("data") or {}).get("relation") == "subordinate"
                                       and _edge_endpoint(edge.get("target")) == node_id]
                        for edge_id in old_parents:
                            index.pop(edge_id, None)
                        if value is not None:
                            edge_id = f"e_{value}__{node_id}"
                            if edge_id in index:
                                edge_id = f"e_tree_{value}__{node_id}"
                            if edge_id in index:
                                raise ValueError("structural edge id already exists")
                            index[edge_id] = make_edge_cell(edge_id, value, node_id, "subordinate")
                if field not in {"importance", "parent_id"}:
                    data[field] = value
                before_after[field] = {"before": old, "after": copy.deepcopy(value)}

            manual = data.setdefault("manual_override", {})
            for field in allowed:
                manual.setdefault(field, False)
            for field in fields:
                manual[field] = True
            edit = data.setdefault("edit", {})
            if "label" in fields: edit["text_edited"] = True
            if "type" in fields: edit["type_edited"] = True
            if "importance" in fields: edit["importance_override"] = True
            candidate = list(index.values())
            self._append_version_record(graph_id, current)
            self.save(graph_id, candidate)
            self.flush(graph_id, force=True)
            self._append_version_record(graph_id, candidate, {
                "source": "manual", "actor": str(actor)[:120], "time": self._now_iso(),
                "reason": str(reason)[:500], "fields": before_after,
            })
            return candidate

    def preview_node_merge(self, graph_id: str, duplicate_id: str, survivor_id: str) -> dict:
        with self._lock:
            cells = self.load(graph_id)
            index = {cell.get("id"): cell for cell in cells}
            duplicate, survivor = index.get(duplicate_id), index.get(survivor_id)
            if not duplicate or not survivor or duplicate.get("shape") == "edge" or survivor.get("shape") == "edge":
                raise KeyError("merge nodes not found")
            if duplicate_id == survivor_id:
                raise ValueError("cannot merge a node into itself")
            if _uses_parent_ids(index) and _is_descendant(index, survivor_id, duplicate_id):
                raise ValueError("merge would make survivor depend on duplicate")
            conflicts = []
            for edge in cells:
                if edge.get("shape") != "edge" or (edge.get("data") or {}).get("relation") != "oppose":
                    continue
                source, target = _edge_endpoint(edge.get("source")), _edge_endpoint(edge.get("target"))
                if {source, target} == {duplicate_id, survivor_id}:
                    conflicts.append(edge.get("id"))
            if conflicts:
                raise ValueError("opposing nodes cannot be merged")
            descendants = []
            frontier = {duplicate_id}
            seen_descendants = {duplicate_id}
            structural_pairs = _structural_pairs(index)
            direct_children = sorted(child for parent, child in structural_pairs if parent == duplicate_id)
            while frontier:
                children = {child for parent, child in structural_pairs
                            if parent in frontier and child not in seen_descendants}
                frontier = children
                seen_descendants.update(frontier)
                descendants.extend(sorted(frontier))
            refs = list(dict.fromkeys((duplicate.get("data") or {}).get("metadata_refs") or []))
            affected_edges = [edge.get("id") for edge in cells if edge.get("shape") == "edge"
                              and duplicate_id in {_edge_endpoint(edge.get("source")), _edge_endpoint(edge.get("target"))}]
            protected = [cell.get("id") for cell in (duplicate, survivor)
                         if is_locked(cell) or any(not field_writable(cell, field) for field in
                            ("text", "type", "parent_id", "importance", "status"))]
            return {
                "graph_id": graph_id, "version": self.version(graph_id),
                "duplicate": {"id": duplicate_id, "label": duplicate.get("data", {}).get("label")},
                "survivor": {"id": survivor_id, "label": survivor.get("data", {}).get("label")},
                "reparented_children": direct_children,
                "affected_descendants": descendants,
                "rewritten_relations": affected_edges,
                "transferred_metadata_refs": refs,
                "protected_node_ids": protected,
            }

    def merge_nodes(self, graph_id: str, duplicate_id: str, survivor_id: str, *,
                    expected_version: int, actor: str = "human", reason: str = "") -> tuple[list, dict]:
        with self._lock:
            current_version = self.version(graph_id)
            cells = self.load(graph_id)
            if current_version != expected_version:
                raise VersionConflict(current_version, cells)
            preview = self.preview_node_merge(graph_id, duplicate_id, survivor_id)
            if preview["protected_node_ids"]:
                raise ValueError("locked or manually edited nodes must be reviewed before merge")
            self._append_version_record(graph_id, cells)
            updated, receipt = self._commit_graph_update(graph_id, GraphUpdateOp(operations=[
                GraphOp(op="merge_as_duplicate", node=duplicate_id, parent=survivor_id),
            ], thought=str(reason)[:500]))
            if not receipt.get("ok") or duplicate_id in {cell.get("id") for cell in updated}:
                raise ValueError("merge was not applied")
            self._append_version_record(graph_id, updated, {
                "source": "manual", "actor": str(actor)[:120], "time": self._now_iso(),
                "operation": "merge_as_duplicate", "node_id": duplicate_id,
                "reason": str(reason)[:500], "survivor_id": survivor_id,
                "preview": preview,
            })
            return updated, receipt

    def create_share_snapshot(self, graph_id: str, edits: Optional[dict] = None) -> Optional[dict]:
        """保存当前版本，并生成可跨服务重启读取的只读分享快照。"""
        record = self.save_snapshot(graph_id, edits)
        if record is None:
            return None
        token = secrets.token_urlsafe(18)
        path = os.path.join(self.root, f"{token}.snapshot.json")
        _atomic_write_json(path, {"graph_id": graph_id, **record})
        return {"token": token, "graph_id": graph_id, "version": record["version"],
                "url": f"/view.html?token={token}"}

    def load_share_snapshot(self, token: str) -> Optional[dict]:
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token):
            return None
        path = os.path.join(self.root, f"{token}.snapshot.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def history(self, graph_id: str) -> list:
        path = self._history_path(graph_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
            return records if isinstance(records, list) else []
        except (OSError, ValueError):
            return []

    def rollback_cell(self, graph_id: str, cell_id: str, version: int, actor: str = "human",
                      expected_version: Optional[int] = None) -> list:
        """从已保存的完整看板版本恢复单个节点或边，其他当前内容保持不变。"""
        with self._lock:
            return self._rollback_cell(graph_id, cell_id, version, actor, expected_version)

    def _rollback_cell(self, graph_id: str, cell_id: str, version: int, actor: str,
                       expected_version: Optional[int]) -> list:
        current_version = self.version(graph_id)
        if expected_version is not None and current_version != expected_version:
            raise VersionConflict(current_version, self.load(graph_id))
        record = next((item for item in reversed(self.history(graph_id))
                       if item.get("version") == version), None)
        if record is None:
            raise KeyError(f"snapshot version {version} not found")
        old = next((c for c in record.get("cells", []) if c.get("id") == cell_id), None)
        if old is None:
            raise KeyError(f"cell {cell_id} not found in version {version}")
        cells = self.load(graph_id)
        current = next((c for c in cells if c.get("id") == cell_id), None)
        restored = copy.deepcopy(old)
        if current and restored.get("shape") != "edge":
            data = restored.setdefault("data", {})
            for meta_id in (current.get("data") or {}).get("metadata_refs") or []:
                self._append_meta(restored, [meta_id])
            for key in ("mention_count", "updated_at", "confidence"):
                if key in (current.get("data") or {}):
                    data[key] = copy.deepcopy(current["data"][key])
        self._append_version_record(graph_id, cells)
        cells = [c for c in cells if c.get("id") != cell_id]
        cells.append(restored)
        self.save(graph_id, cells)
        self.flush(graph_id, force=True)
        self._append_version_record(graph_id, cells, {
            "source": "manual", "actor": str(actor)[:120], "time": self._now_iso(),
            "operation": "rollback", "node_id": cell_id,
            "reason": f"restore snapshot v{version}",
        })
        return cells

    def undo_operation(self, graph_id: str, operation_version: int, *,
                       expected_version: int, actor: str = "human") -> list:
        """Undo one audited manual operation with a three-way, field-aware restore."""
        with self._lock:
            current_version = self.version(graph_id)
            current = self.load(graph_id)
            if current_version != expected_version:
                raise VersionConflict(current_version, current)
            records = self.history(graph_id)
            operation_record = next((record for record in reversed(records)
                if record.get("version") == operation_version
                and record.get("change", {}).get("source") == "manual"), None)
            if operation_record is None:
                raise KeyError(f"manual operation version {operation_version} not found")
            before_record = next((record for record in reversed(records)
                if record.get("version", 0) < operation_version), None)
            if before_record is None:
                raise KeyError(f"prior snapshot for version {operation_version} not found")

            def restore(before, after, now):
                if before == after:
                    return copy.deepcopy(now)
                if now == after:
                    return copy.deepcopy(before)
                if isinstance(before, dict) and isinstance(after, dict) and isinstance(now, dict):
                    merged = copy.deepcopy(now)
                    for key in set(before) | set(after):
                        if key not in before:
                            if key in merged and merged[key] == after.get(key):
                                merged.pop(key, None)
                        elif key not in after:
                            if merged.get(key) == before[key]:
                                merged.pop(key, None)
                        else:
                            merged[key] = restore(before[key], after[key], merged.get(key))
                    return merged
                return copy.deepcopy(now)

            before_cells = {cell.get("id"): cell for cell in before_record.get("cells", [])}
            after_cells = {cell.get("id"): cell for cell in operation_record.get("cells", [])}
            current_cells = {cell.get("id"): cell for cell in current}
            candidate = copy.deepcopy(current_cells)
            affected = []
            for cell_id in set(before_cells) | set(after_cells):
                before, after = before_cells.get(cell_id), after_cells.get(cell_id)
                if before == after:
                    continue
                affected.append(cell_id)
                now = current_cells.get(cell_id)
                if before is not None and after is None:
                    if now is None:
                        candidate[cell_id] = copy.deepcopy(before)
                elif before is None and after is not None:
                    if now == after:
                        candidate.pop(cell_id, None)
                elif now is not None:
                    restored = restore(before, after, now)
                    if restored is None:
                        candidate.pop(cell_id, None)
                    else:
                        candidate[cell_id] = restored

            restored_cells = list(candidate.values())
            self._append_version_record(graph_id, current)
            self.save(graph_id, restored_cells)
            self.flush(graph_id, force=True)
            self._append_version_record(graph_id, restored_cells, {
                "source": "manual", "actor": str(actor)[:120], "time": self._now_iso(),
                "operation": "undo_operation", "undo_of_version": operation_version,
                "affected_cell_ids": sorted(affected),
            })
            return restored_cells

    def apply_user_operation(self, graph_id: str, cell_id: str, op: str,
                             payload: dict, actor: str = "human",
                             expected_version: Optional[int] = None) -> list:
        with self._lock:
            return self._apply_user_operation(graph_id, cell_id, op, payload, actor, expected_version)

    def _apply_user_operation(self, graph_id: str, cell_id: str, op: str,
                              payload: dict, actor: str = "human",
                              expected_version: Optional[int] = None) -> list:
        if not self.exists(graph_id):
            raise KeyError("meeting not found")
        if expected_version is not None and self.version(graph_id) != expected_version:
            raise VersionConflict(self.version(graph_id), self.load(graph_id))
        cells = self.load(graph_id)
        before_cells = copy.deepcopy(cells)
        index = {c.get("id"): c for c in cells}
        cell = index.get(cell_id)
        if op in {"move", "edit_label", "edit_type", "lock", "set_importance", "remove"} and not cell:
            raise KeyError("node not found")
        if op == "move":
            if cell.get("shape") == "edge":
                # 连线手动整形/拖移：仅持久化顶点（端点仍锚定节点，AI 布局不受影响）
                verts = payload.get("vertices") or []
                if not all(isinstance(v.get("x"), (int, float)) and isinstance(v.get("y"), (int, float))
                           and math.isfinite(v["x"]) and math.isfinite(v["y"]) for v in verts):
                    raise ValueError("invalid vertices")
                cell["vertices"] = [{"x": float(v["x"]), "y": float(v["y"])} for v in verts]
            else:
                x, y = payload.get("x"), payload.get("y")
                if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (x, y)):
                    raise ValueError("invalid position")
                cell["position"] = {"x": x, "y": y}
                cell.setdefault("data", {}).setdefault("edit", {})["position_frozen"] = True
        elif op == "edit_label":
            label = str(payload.get("label", "")).strip()
            if not label or len(label) > 200:
                raise ValueError("invalid label")
            cell.setdefault("data", {})["label"] = label
            cell["data"].setdefault("edit", {})["text_edited"] = True
        elif op == "edit_type":
            kind = payload.get("type")
            if kind not in {"point", "evidence", "issue", "conclusion", "action", "conflict"}:
                raise ValueError("invalid node type")
            cell.setdefault("data", {})["type"] = kind
            cell["data"].setdefault("edit", {})["type_edited"] = True
        elif op == "lock":
            cell.setdefault("data", {})["lock"] = {"locked": bool(payload.get("locked", True)),
                "locked_by": actor, "locked_at": int(time.time() * 1000)}
        elif op == "set_importance":
            level = payload.get("level")
            if level not in {"high", "normal", "low"}:
                raise ValueError("invalid importance")
            data = cell.setdefault("data", {})
            data.setdefault("importance", {})["level"] = level
            data["importance"]["manual_override"] = True
            data.setdefault("edit", {})["importance_override"] = True
        elif op == "link":
            source, target = payload.get("source"), payload.get("target")
            if source not in index or target not in index or source == target:
                raise ValueError("invalid edge endpoints")
            if cell_id in index:
                raise ValueError("edge id already exists")
            cells.append(make_edge_cell(cell_id, source, target,
                                        payload.get("relation", "support")))
        elif op == "add":
            if cell_id in index or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", cell_id):
                raise ValueError("invalid or duplicate node id")
            data = NodeData(type=str(payload.get("type", "point")),
                            label=str(payload.get("label", "")),
                            metadata_refs=list(payload.get("meta_ids") or []))
            cells.append(make_node_cell(cell_id, data,
                float(payload.get("x", 0)), float(payload.get("y", 0))))
        elif op == "remove":
            removed_ids = {cell_id}
            if cell and cell.get("shape") != "edge":
                structure = _structural_pairs(index)
                children = {child for parent, child in structure if parent == cell_id}
                if children:
                    action = payload.get("children_action")
                    if action not in {"reparent", "delete"}:
                        raise ValueError("choose children_action: reparent or delete")
                    if action == "reparent":
                        parent_id = ((cell.get("data") or {}).get("parent_id") if _uses_parent_ids(index)
                                     else next((parent for parent, child in structure if child == cell_id), None))
                        if _uses_parent_ids(index):
                            for child_id in children:
                                next(c for c in cells if c.get("id") == child_id)["data"]["parent_id"] = parent_id
                        else:
                            for edge_id, edge in list(index.items()):
                                if (edge.get("shape") == "edge" and
                                        (edge.get("data") or {}).get("relation") == "subordinate" and
                                        _edge_endpoint(edge.get("source")) == cell_id and
                                        _edge_endpoint(edge.get("target")) in children):
                                    if parent_id:
                                        edge["source"] = {"cell": parent_id}
                                    else:
                                        index.pop(edge_id, None)
                    else:
                        removed_ids.update(children)
                        changed = True
                        while changed:
                            changed = False
                            for parent, child in structure:
                                if parent in removed_ids and child not in removed_ids:
                                    removed_ids.add(child); changed = True
            cells = [c for c in cells if c.get("id") not in removed_ids and
                     (c.get("shape") != "edge" or
                      (_edge_endpoint(c.get("source")) not in removed_ids and
                       _edge_endpoint(c.get("target")) not in removed_ids))]
        else:
            raise ValueError(f"unknown operation: {op}")
        self._append_version_record(graph_id, self.load(graph_id))
        self.save(graph_id, cells)
        self.flush(graph_id, force=True)
        if op != "move":
            before_cell = next((item for item in before_cells if item.get("id") == cell_id), None)
            after_cell = next((item for item in cells if item.get("id") == cell_id), None)
            before_data = before_cell.get("data", {}) if before_cell else {}
            after_data = after_cell.get("data", {}) if after_cell else {}
            fields_changed = {
                key: {"before": copy.deepcopy(before_data.get(key)),
                      "after": copy.deepcopy(after_data.get(key))}
                for key in set(before_data) | set(after_data)
                if before_data.get(key) != after_data.get(key)
            }
            self._append_version_record(graph_id, cells, {
                "source": "manual", "actor": str(actor)[:120], "time": self._now_iso(),
                "operation": op, "node_id": cell_id,
                "reason": str(payload.get("reason") or "")[:500],
                "fields": fields_changed,
            })
        return cells

    def _append_meta(self, cell: dict, meta_ids: list) -> None:
        existing = cell["data"].setdefault("metadata_refs", [])
        for m in meta_ids:
            if m not in existing:
                existing.append(m)

    def _merge(self, cell: dict, g: GraphOp) -> None:
        """把 g 的论据合并进已有 cell：metadata_refs 去重追加、mention_count++。"""
        self._append_meta(cell, g.meta_ids)
        data = cell["data"]
        old_count = max(1, data.get("mention_count", 1))
        if g.confidence is not None:
            prior = data.get("confidence")
            data["confidence"] = g.confidence if prior is None else (
                (prior * old_count + g.confidence) / (old_count + 1))
            if field_writable(cell, "status"):
                data["needs_confirmation"] = (
                    not data.get("resolved", False) and data["confidence"] < 0.65)
        if g.importance_rationale and field_writable(cell, "importance"):
            data.setdefault("importance", {})["rationale"] = g.importance_rationale
        data["mention_count"] = old_count + 1

    def _apply_one(self, index: dict, g: GraphOp) -> None:
        guarded = index.get(g.node) if g.node else (index.get(g.target) if g.op == "link" else None)
        if guarded and op_blocked_by_lock_or_edit(guarded, g, index):
            return
        if g.op == "merge_as_duplicate":
            duplicate = index.get(g.node)
            if duplicate and op_blocked_by_lock_or_edit(duplicate, g, index):
                return
        if g.op == "add_node":
            if g.node in index:
                self._merge(index[g.node], g)              # 已存在→合并去重
            else:
                confidence = 0.5 if g.confidence is None else g.confidence
                nd = NodeData(type=g.node_type or "point", label=g.label or "",
                              metadata_refs=list(g.meta_ids), confidence=confidence)
                if g.importance_rationale:
                    nd.importance["rationale"] = g.importance_rationale
                # 坐标由 apply_graph_update 末尾的 relayout 统一计算
                index[g.node] = make_node_cell(g.node, nd)
                if _uses_parent_ids(index):
                    root_id = "n_issue_root" if "n_issue_root" in index else next(
                        (c["id"] for c in index.values()
                         if c.get("shape") != "edge" and c.get("data", {}).get("type") == "issue"
                         and c["id"] != g.node), None)
                    parent_id = g.parent or root_id
                    if parent_id:
                        validate_parent_assignment(index, g.node, parent_id)
                    index[g.node]["data"]["parent_id"] = parent_id
                elif g.parent:
                    validate_parent_assignment(index, g.node, g.parent)
                    eid = g.edge or f"e_{g.parent}__{g.node}"
                    if eid in index:
                        raise ValueError("structural edge id already exists")
                    index[eid] = make_edge_cell(eid, g.parent, g.node, "subordinate")
        elif g.op == "mark_node":
            # 用户指令标记：gray（暂不考虑→灰化）/ strike（删除→画删除线，节点保留）；附简要理由
            if g.node not in index or index[g.node].get("shape") == "edge":
                return
            cell = index[g.node]
            if is_locked(cell):
                return                                     # 持久锁：跳过
            if g.mark not in ("gray", "strike"):
                return
            cell.setdefault("data", {})["cmd"] = {
                "mark": g.mark, "reason": (g.reason or "").strip()[:80], "by": "user"}
        elif g.op == "move_node":
            # 用户指令：把节点改挂到新父节点下（改写其父边 source，子边与论据不动）
            validate_parent_assignment(index, g.node, g.parent)
            node_cell, parent_cell = index[g.node], index[g.parent]
            if is_locked(node_cell) or is_locked(parent_cell):
                return
            if not field_writable(node_cell, "parent_id"):
                return
            if _uses_parent_ids(index):
                node_cell.setdefault("data", {})["parent_id"] = g.parent
                node_cell["data"].pop("needs_parent_review", None)
            else:
                parent_edges = [c for c in index.values()
                                if c.get("shape") == "edge"
                                and (c.get("data") or {}).get("relation") == "subordinate"
                                and _edge_endpoint(c.get("target")) == g.node]
                if parent_edges:                           # 只更改结构父边，语义边保留
                    parent_edges[0]["source"] = {"cell": g.parent}
                    for extra in parent_edges[1:]:
                        index.pop(extra.get("id"), None)
                else:                                      # 无结构父边（游离节点）→ 新建结构边
                    eid = g.edge or f"e_{g.parent}__{g.node}"
                    if eid not in index:
                        index[eid] = make_edge_cell(eid, g.parent, g.node, "subordinate")
            node_cell.setdefault("data", {})["cmd"] = {
                "mark": "move", "reason": (g.reason or "").strip()[:80], "by": "user"}
        elif g.op == "link":
            # 端点必须已存在，避免悬空边；同 id 边幂等（不覆盖已有属性）
            if g.source in index and g.target in index:
                if (g.relation or "support") == "subordinate":
                    validate_parent_assignment(index, g.target, g.source)
                    if not field_writable(index[g.target], "parent_id"):
                        return
                    if _uses_parent_ids(index):
                        index[g.target].setdefault("data", {})["parent_id"] = g.source
                        index[g.target]["data"].pop("needs_parent_review", None)
                        return
                    existing_parents = [c for c in index.values()
                                        if c.get("shape") == "edge"
                                        and (c.get("data") or {}).get("relation") == "subordinate"
                                        and _edge_endpoint(c.get("target")) == g.target]
                    if existing_parents:
                        if all(_edge_endpoint(c.get("source")) == g.source for c in existing_parents):
                            return
                        raise ValueError("node already has a structural parent; use move_node")
                eid = g.edge or f"e_{g.source}__{g.target}"
                if eid not in index:
                    index[eid] = make_edge_cell(eid, g.source, g.target, g.relation or "support")
        elif g.op == "merge_as_duplicate":
            if g.parent in index and g.node in index:
                target, duplicate = index[g.parent], index[g.node]
                if target.get("shape") == "edge" or duplicate.get("shape") == "edge":
                    raise ValueError("merge_as_duplicate requires two nodes")
                if _uses_parent_ids(index) and _is_descendant(index, g.parent, g.node):
                    raise ValueError("merge would make a surviving node depend on the removed node")
                target_data = target.setdefault("data", {})
                duplicate_data = duplicate.get("data") or {}
                # The surviving node keeps evidence already attached to either duplicate.
                refs = target_data.setdefault("metadata_refs", [])
                for meta_id in duplicate_data.get("metadata_refs") or []:
                    if meta_id not in refs:
                        refs.append(meta_id)
                self._merge(target, g)
                target_data["version"] = target_data.get("version", 1) + 1
                if (duplicate_data.get("lock") or {}).get("locked"):
                    target_data["lock"] = copy.deepcopy(duplicate_data["lock"])

                if _uses_parent_ids(index):
                    for cell in index.values():
                        if (cell.get("shape") != "edge"
                                and (cell.get("data") or {}).get("parent_id") == g.node):
                            cell["data"]["parent_id"] = g.parent
                    # The removed node's own parent relationship disappears with it.
                for edge_id, edge in list(index.items()):
                    if edge.get("shape") != "edge":
                        continue
                    structural = (edge.get("data") or {}).get("relation") == "subordinate"
                    source, dest = _edge_endpoint(edge.get("source")), _edge_endpoint(edge.get("target"))
                    if structural and not _uses_parent_ids(index):
                        # Keep the duplicate's children under the surviving node; discard
                        # its own parent link and any resulting self-parent edge.
                        if source == g.node and dest == g.parent:
                            index.pop(edge_id, None)
                        elif source == g.node:
                            edge["source"] = {"cell": g.parent}
                        elif dest == g.node:
                            index.pop(edge_id, None)
                        if source == g.parent and dest == g.parent:
                            index.pop(edge_id, None)
                        continue
                    for endpoint in ("source", "target"):
                        if _edge_endpoint(edge.get(endpoint)) == g.node:
                            edge[endpoint] = {"cell": g.parent}
                del index[g.node]
        elif g.op == "replace":
            if g.node not in index:
                return
            cell = index[g.node]
            if op_blocked_by_lock_or_edit(cell, g):
                return                                       # 持久锁：跳过
            if field_writable(cell, "text") and g.label:
                cell["data"]["label"] = g.label
            if field_writable(cell, "type") and g.node_type:
                cell["data"]["type"] = g.node_type
            self._append_meta(cell, g.meta_ids)
        elif g.op == "set_importance":
            if g.node in index and g.importance and field_writable(index[g.node], "importance"):
                index[g.node]["data"]["importance"]["level"] = g.importance
                if g.importance_rationale:
                    index[g.node]["data"]["importance"]["rationale"] = g.importance_rationale
        elif g.op == "relayout":
            # 用户指令：重新排版——解除全部节点的手动定位冻结，坐标交由末尾的 relayout 统一重算
            for cell in index.values():
                if cell.get("shape") == "edge":
                    continue
                if not is_locked(cell):
                    cell.setdefault("data", {}).setdefault("edit", {})["position_frozen"] = False

    def commit_graph_update(self, graph_id: str, op: GraphUpdateOp) -> tuple[list, dict]:
        with self._lock:
            return self._commit_graph_update(graph_id, op)

    def _commit_graph_update(self, graph_id: str, op: GraphUpdateOp) -> tuple[list, dict]:
        """先在副本上计算并校验，再一次性提交；失败时保留最后可用的看板。"""
        before = self.load(graph_id)
        index = {c["id"]: c for c in copy.deepcopy(before)}
        errors = []
        skipped = []
        executable = 0
        for g in op.operations:
            guarded = index.get(g.node) if g.node else (index.get(g.target) if g.op == "link" else None)
            blocked = bool(guarded and op_blocked_by_lock_or_edit(guarded, g, index))
            if g.op == "merge_as_duplicate":
                duplicate = index.get(g.node) if g.node else None
                target = index.get(g.parent) if g.parent else None
                blocked = blocked or bool(duplicate and op_blocked_by_lock_or_edit(duplicate, g, index))
                blocked = blocked or bool(target and op_blocked_by_lock_or_edit(target, g, index))
            if blocked:
                skipped.append(g.node or g.target or g.parent)
                continue
            executable += 1
            try:
                self._apply_one(index, g)
            except Exception as exc:
                errors.append(f"{g.op}: {safe_error_code(exc)}")
        if skipped and executable == 0 and not errors:
            return before, {"ok": True, "errors": [], "skipped": skipped,
                "change_set": {"added": [], "removed": [], "updated": []},
                "version": self.version(graph_id)}
        aligned = _align_evidence_parents(index)
        repaired = []
        triggers_structure_repair = any(
            g.op in _STRUCTURAL_OPS - {"link"}
            or (g.op == "link" and (g.relation or "support") == "subordinate")
            for g in op.operations
        )
        if config.CONFIG.auto_repair_orphans and triggers_structure_repair:
            try:                                    # 游离节点自愈（P2①）：挂回树后随本批一起落库
                repaired = _repair_orphans(index)
            except Exception as e:
                log.warning("[StoreA] orphan repair failed for %s (%s)", graph_id, safe_error_code(e))
        candidate = relayout(list(index.values()))
        nodes = {c.get("id") for c in candidate if c.get("shape") != "edge"}
        if len(nodes) > 500:
            errors.append("too many nodes")
        for cell in candidate:
            if cell.get("shape") == "edge":
                source = _edge_endpoint(cell.get("source"))
                target = _edge_endpoint(cell.get("target"))
                if source not in nodes or target not in nodes:
                    errors.append(f"dangling edge: {cell.get('id')}")
            elif cell.get("data", {}).get("type") not in {
                "issue", "point", "evidence", "conclusion", "action", "conflict"
            }:
                errors.append(f"invalid node type: {cell.get('id')}")
        before_by_id = {c["id"]: c for c in before}
        after_by_id = {c["id"]: c for c in candidate}
        change_set = {
            "added": [cid for cid in after_by_id if cid not in before_by_id],
            "removed": [cid for cid in before_by_id if cid not in after_by_id],
            "updated": [cid for cid in after_by_id if cid in before_by_id
                        and after_by_id[cid] != before_by_id[cid]],
        }
        receipt = {"ok": not errors, "errors": errors,
                   "change_set": change_set if not errors else {"added": [], "removed": [], "updated": []},
                   "version": self.version(graph_id)}
        if repaired:
            receipt["repaired_orphans"] = repaired
        if aligned:
            receipt["aligned_evidence"] = aligned
        if skipped:
            receipt["skipped"] = skipped
        if errors:
            log.warning("[StoreA] rejected update for %s (%d validation errors)", graph_id, len(errors))
            return before, receipt
        if candidate != before:
            self.save(graph_id, candidate)
            receipt["version"] = self.version(graph_id)
        return candidate, receipt

    def apply_graph_update(self, graph_id: str, op: GraphUpdateOp) -> list:
        cells, _ = self.commit_graph_update(graph_id, op)
        return cells

    def relayout_all(self) -> None:
        """对所有已存看板重算布局。启动时迁移历史数据（旧坐标由伪随机撒点产生）。"""
        for name in os.listdir(self.root):
            if not name.endswith(".board.json"):
                continue
            gid = name[: -len(".board.json")]
            try:
                cells = self.load(gid)
                if cells:
                    self.save(gid, relayout(cells))
            except Exception as e:                          # 单个文件失败不阻断启动
                log.warning("[StoreA] relayout %s failed (%s)", gid, safe_error_code(e))
        self.flush_all()


class StoreB:
    """原始语音/议程元数据，按 meta_id 索引（Design_StructureGraph_Storage §2）。

    每条记录形如 {"meta_id", "kind": "utt|agd|man", "text", "speaker_ref",
                 "start_offset_ms", "end_offset_ms", "source"}，供 metadata_refs 反查论据。

    P2③（分片扩展性）：按会议分片持久化——metadata_shards/<meeting>.json（快照）
    + <meeting>.jsonl（追加日志）。flush 只重写脏分片，不再全量重写单一快照，
    避免记录数增长后每秒兜底 flush 的 O(n) 写放大。根目录 metadata.json 保留为
    manifest（分片清单）；旧版全局快照/全局 jsonl 只读兼容，首次 flush 自动迁移入分片。
    """

    _MANIFEST_SCHEMA = "amo.metadata/v2"
    _GLOBAL_SHARD = "_global"

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.shard_dir = os.path.join(root, "metadata_shards")
        os.makedirs(self.shard_dir, exist_ok=True)
        self.path = os.path.join(root, "metadata.json")        # 旧版快照（只读兼容）/ 分片 manifest
        self.jsonl_path = os.path.join(root, "metadata.jsonl")  # 旧全局追加日志（只读兼容）
        self._data: dict = {}
        self._shard_of: dict = {}          # meta_id → 分片键（meeting_id 或 _global）
        self._dirty_shards: set = set()
        self._legacy_dirty = False         # 旧全局数据待迁移入分片
        self._last_flush = 0.0
        self._lock = threading.RLock()     # put/append/flush 串行，防 JSONL 截断与追加竞态
        self._load()

    # —— 分片路径 ——
    def _shard_snapshot_path(self, shard: str) -> str:
        return os.path.join(self.shard_dir, f"{shard}.json")

    def _shard_jsonl_path(self, shard: str) -> str:
        return os.path.join(self.shard_dir, f"{shard}.jsonl")

    @staticmethod
    def _shard_key(record: dict) -> str:
        mid = record.get("meeting_id")
        return mid if is_valid_meeting_id(mid) else StoreB._GLOBAL_SHARD

    def _load(self) -> None:
        """加载顺序（后读覆盖）：旧全局快照 → 旧全局 jsonl → 各分片快照 → 各分片 jsonl。"""
        legacy: dict = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                if not isinstance(obj, dict):
                    raise ValueError("metadata_snapshot_invalid")
                if isinstance(obj, dict) and obj.get("schema") != self._MANIFEST_SCHEMA:
                    legacy = obj                       # 旧版：meta_id → record 全量快照
            except Exception as e:
                log.warning("[StoreB] load legacy snapshot failed (%s)", safe_error_code(e))
                raise RuntimeError("metadata_storage_unreadable") from None
        if os.path.exists(self.jsonl_path):
            self._replay_jsonl(self.jsonl_path, legacy)
        if legacy:
            for mid, rec in legacy.items():
                if isinstance(rec, dict) and mid:
                    self._data[mid] = rec
                    self._shard_of[mid] = self._shard_key(rec)
            self._legacy_dirty = True                  # 首次 flush 迁移入分片
            self._dirty_shards.update(self._shard_of[m] for m in legacy
                                      if m in self._shard_of)
        if os.path.isdir(self.shard_dir):
            for name in sorted(os.listdir(self.shard_dir)):
                if name.endswith(".json"):
                    shard = name[: -len(".json")]
                    try:
                        with open(self._shard_snapshot_path(shard), "r", encoding="utf-8") as f:
                            recs = json.load(f)
                        if not isinstance(recs, dict):
                            raise ValueError("metadata_shard_invalid")
                        for mid, rec in recs.items():
                            if isinstance(rec, dict):
                                self._data[mid] = rec
                                self._shard_of[mid] = shard
                    except Exception as e:
                        log.warning("[StoreB] load shard %s failed (%s)", shard, safe_error_code(e))
                        raise RuntimeError("metadata_storage_unreadable") from None
                elif name.endswith(".jsonl"):
                    shard = name[: -len(".jsonl")]
                    staging: dict = {}
                    self._replay_jsonl(self._shard_jsonl_path(shard), staging)
                    for mid, rec in staging.items():
                        if rec is None:
                            self._data.pop(mid, None)
                            self._shard_of.pop(mid, None)
                        else:
                            self._data[mid] = rec
                            self._shard_of[mid] = shard

    @staticmethod
    def _replay_jsonl(path: str, into: dict) -> None:
        """回放完整日志行；仅忽略崩溃时未写完的最后一行。"""
        if not os.path.exists(path):
            return
        try:
            with open(path, "rb") as f:
                for raw in f:
                    if not raw.strip():
                        continue
                    if not raw.endswith(b"\n"):
                        break                      # append only ACKs after a newline and fsync
                    obj = json.loads(raw)
                    if not isinstance(obj, dict) or not isinstance(obj.get("meta_id"), str):
                        raise ValueError("metadata_log_record_invalid")
                    if obj.get("record") is not None and not isinstance(obj["record"], dict):
                        raise ValueError("metadata_log_record_invalid")
                    mid = obj.get("meta_id")
                    if mid:
                        into[mid] = obj.get("record")   # dict=upsert；None=delete
        except Exception as e:
            log.warning("[StoreB] replay jsonl failed (%s)", safe_error_code(e))
            raise RuntimeError("metadata_storage_unreadable") from None

    def _append(self, shard: str, meta_id: str, record) -> None:
        """逐条追加到对应分片日志：put/delete 均 O(1) 持久，不触发快照重写。"""
        path = self._shard_jsonl_path(shard)
        payload = (json.dumps({"meta_id": meta_id, "record": record}, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with open(path, "ab+") as f:
                # A crash can leave an unacknowledged partial final line.
                # Remove only that tail before appending the next event.
                f.seek(0, os.SEEK_END)
                end = f.tell()
                if end:
                    f.seek(end - 1)
                    if f.read(1) != b"\n":
                        offset = end
                        while offset:
                            size = min(offset, 4096)
                            offset -= size
                            f.seek(offset)
                            newline = f.read(size).rfind(b"\n")
                            if newline >= 0:
                                f.truncate(offset + newline + 1)
                                break
                        else:
                            f.truncate(0)
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            log.warning("[StoreB] append shard jsonl failed (%s)", safe_error_code(e))
            raise

    def put(self, meta_id: str, record: dict) -> None:
        with self._lock:
            shard = self._shard_of.get(meta_id) or self._shard_key(record)
            self._append(shard, meta_id, record)
            self._data[meta_id] = record
            self._shard_of[meta_id] = shard
            self._dirty_shards.add(shard)
            self.flush(force=False)            # 节流：合并分片快照写盘

    def delete(self, meta_id: str) -> bool:
        """删除记录（补偿批次完成后清 pending 标记用，P0①）。"""
        with self._lock:
            if meta_id not in self._data:
                return False
            shard = self._shard_of.get(meta_id) or self._GLOBAL_SHARD
            self._append(shard, meta_id, None)
            self._shard_of.pop(meta_id, None)
            del self._data[meta_id]
            self._dirty_shards.add(shard)
            self.flush(force=False)
            return True

    def get(self, meta_id: str) -> Optional[dict]:
        return self._data.get(meta_id)

    def get_many(self, ids: list) -> list:
        return [self._data[i] for i in ids if i in self._data]

    def list_utterances(self, meeting_id: str) -> list:
        """按会议列出全部逐句发言（kind=utt），按起始偏移稳定排序（无偏移时保持入库序）。"""
        items = [rec for rec in self._data.values()
                 if rec.get("kind") == "utt" and rec.get("meeting_id") == meeting_id]
        items.sort(key=lambda rec: rec.get("start_offset_ms") or 0)
        return items

    def list_by_kind(self, kind: str, meeting_id: Optional[str] = None) -> list:
        """按 kind 列出记录（可选按会议过滤）；失败批次补偿扫描用（P0①）。"""
        with self._lock:
            items = [dict(rec, meta_id=mid) for mid, rec in self._data.items()
                     if rec.get("kind") == kind]
        if meeting_id is not None:
            items = [i for i in items if i.get("meeting_id") == meeting_id]
        return items

    def flush(self, force: bool = True) -> None:
        """把脏分片写成紧凑快照 + 更新 manifest。force=False 时受 SAVE_THROTTLE_MS 约束。

        写分片快照后清空对应 JSONL：快照已含该分片全量，日志只保留增量。
        """
        with self._lock:
            if not self._dirty_shards:
                return
            now = time.monotonic()
            if not force and now - self._last_flush < SAVE_THROTTLE_MS / 1000.0:
                return
            for shard in sorted(self._dirty_shards):
                recs = {mid: self._data[mid] for mid, s in self._shard_of.items()
                        if s == shard and mid in self._data}
                _atomic_write_json(self._shard_snapshot_path(shard), recs)
                try:
                    open(self._shard_jsonl_path(shard), "w", encoding="utf-8").close()
                except OSError as e:
                    log.warning("[StoreB] truncate shard jsonl failed (%s)", safe_error_code(e))
            shards = sorted(set(self._shard_of.values()))
            _atomic_write_json(self.path, {"schema": self._MANIFEST_SCHEMA,
                                           "shards": shards, "count": len(self._data)})
            if self._legacy_dirty:
                try:                    # 迁移完成：清空旧全局日志，防下次启动重放覆盖分片
                    open(self.jsonl_path, "w", encoding="utf-8").close()
                except OSError as e:
                    log.warning("[StoreB] truncate legacy jsonl failed (%s)", safe_error_code(e))
                self._legacy_dirty = False
            self._dirty_shards.clear()
            self._last_flush = now
