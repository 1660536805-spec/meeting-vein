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
    GraphUpdateOp, GraphOp, NodeData, make_node_cell, make_edge_cell, node_data_to_dict,
)
from . import config

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


def _is_descendant(index: dict, candidate: str, ancestor: str) -> bool:
    """candidate 是否为 ancestor 的子孙（按当前 index 的边推导），供 move_node 环检测。"""
    children: dict = {}
    for c in index.values():
        if c.get("shape") != "edge":
            continue
        s, t = _edge_endpoint(c.get("source")), _edge_endpoint(c.get("target"))
        if s and t:
            children.setdefault(s, []).append(t)
    seen, stack = set(), [ancestor]
    while stack:
        for ch in children.get(stack.pop(), []):
            if ch == candidate:
                return True
            if ch not in seen:
                seen.add(ch)
                stack.append(ch)
    return False


# 触发游离修复的结构性算子：内容型 sync 才修；纯光标/无操作批次不动图（保护 DP-11 语义）
_STRUCTURAL_OPS = {"add_node", "link", "move_node", "merge_as_duplicate"}


def _repair_orphans(index: dict, max_passes: int = 4) -> list:
    """把游离节点自动挂回议题树（P2①，开关 config.auto_repair_orphans）。

    游离定义与 relayout 一致：不与根（n_issue_root → 首个 issue → 首节点）无向连通。
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
        node_ids = {c["id"] for c in nodes}
        adj: dict = {nid: [] for nid in node_ids}        # 无向连通性（与 relayout 同判据）
        for c in index.values():
            if c.get("shape") != "edge":
                continue
            s, t = _edge_endpoint(c.get("source")), _edge_endpoint(c.get("target"))
            if s in adj and t in adj:
                adj[s].append(t)
                adj[t].append(s)
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
        orphans = sorted((c for c in nodes if c["id"] not in seen),
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
            eid = f"e_{parent['id']}__{orphan['id']}"
            if eid not in index:
                index[eid] = make_edge_cell(eid, parent["id"], orphan["id"], "subordinate")
            repaired.append({"node": orphan["id"], "parent": parent["id"], "edge": eid})
            fixed += 1
            adj[parent["id"]].append(orphan["id"])       # 就地刷新连通性，同趟后续判断更准
            adj[orphan["id"]].append(parent["id"])
            if parent["id"] in seen:
                seen.add(orphan["id"])
        if fixed == 0:
            break
    return repaired


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
    edit = cell.get("data", {}).get("edit", {})
    if field == "text":
        return not edit.get("text_edited")
    if field == "type":
        return not edit.get("type_edited")
    if field == "importance":
        return not edit.get("importance_override")
    return True


def op_blocked_by_lock_or_edit(cell: dict, op: GraphOp) -> bool:
    """该 op 是否应被整体跳过（GraphTools 工具层守卫，回 skipped）。"""
    if is_locked(cell):
        return True
    if op.op == "replace":
        return not (field_writable(cell, "text") and field_writable(cell, "type"))
    if op.op == "set_importance":
        return not field_writable(cell, "importance")
    return False


def relayout(cells: list) -> list:
    """重算节点坐标：以议题为根、按边 BFS 分层，同层水平居中。

    只改 position，不动 id / 数量 / cells 顺序；position_frozen 的节点保持原位。
    孤立节点（不与根连通）依次各自成层，避免与主树重叠。
    """
    nodes = [c for c in cells if c.get("shape") != "edge"]
    if not nodes:
        return cells
    edges = [c for c in cells if c.get("shape") == "edge"]

    by_id = {c["id"]: c for c in nodes}
    adj: dict = {cid: [] for cid in by_id}
    for e in edges:
        s, t = _edge_endpoint(e.get("source")), _edge_endpoint(e.get("target"))
        if s in adj and t in adj:
            adj[s].append(t)                 # 无向：方向不敏感，仅用于分层
            adj[t].append(s)

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
                   if not by_id[cid].get("data", {}).get("edit", {}).get("position_frozen")]
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
            if cell.get("data", {}).get("edit", {}).get("position_frozen"):
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

    def _history_path(self, graph_id: str) -> str:
        return os.path.join(self.root, f"{graph_id}.history.json")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _path(self, graph_id: str) -> str:
        if not is_valid_meeting_id(graph_id):
            raise ValueError("invalid meeting_id")
        return os.path.join(self.root, f"{graph_id}.board.json")

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
            except Exception as e:                    # 单文件损坏不拖垮调用方
                log.warning("[StoreA] load %s failed: %r", graph_id, e)
                cells = []
        self._cache[graph_id] = cells
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
        _atomic_write_json(p, {"schema": "amo.board/v1", "graph_id": graph_id,
                               "version": self._versions.get(graph_id, 1),
                               "title": self._titles.get(graph_id) or None,
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
                log.warning("[StoreA] flush %s failed: %r", gid, e)

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
                "title": title,
                "version": self._versions.get(graph_id, 0),
                "updated_at": self._updated_at.get(graph_id),
                "stats": {"nodes": len(nodes), "edges": len(edges), "types": types},
            })
        meetings.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return meetings

    def title(self, graph_id: str) -> Optional[str]:
        self.load(graph_id)
        return self._titles.get(graph_id) or None

    def create_meeting(self, graph_id: str, title: str) -> dict:
        if not is_valid_meeting_id(graph_id):
            raise ValueError("invalid meeting_id")
        if self.exists(graph_id):
            raise FileExistsError(graph_id)
        self._cache[graph_id] = []
        self._versions[graph_id] = 1
        self._titles[graph_id] = title.strip()
        self._updated_at[graph_id] = self._now_iso()
        self._dirty.add(graph_id)
        self.flush(graph_id, force=True)
        return {"meeting_id": graph_id, "title": self._titles[graph_id],
                "version": self._versions[graph_id], "updated_at": self._updated_at[graph_id]}

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

    def _append_version_record(self, graph_id: str, cells: list) -> dict:
        record = {"version": self._versions.get(graph_id, 1),
                  "created_at": self._updated_at.get(graph_id) or self._now_iso(),
                  "title": self._titles.get(graph_id) or graph_id,
                  "cells": copy.deepcopy(cells)}
        path = self._history_path(graph_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
        except (OSError, ValueError):
            records = []
        records.append(record)
        _atomic_write_json(path, records)
        return record

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

    def rollback_cell(self, graph_id: str, cell_id: str, version: int) -> list:
        """从已保存的完整看板版本恢复单个节点或边，其他当前内容保持不变。"""
        record = next((item for item in reversed(self.history(graph_id))
                       if item.get("version") == version), None)
        if record is None:
            raise KeyError(f"snapshot version {version} not found")
        old = next((c for c in record.get("cells", []) if c.get("id") == cell_id), None)
        if old is None:
            raise KeyError(f"cell {cell_id} not found in version {version}")
        cells = self.load(graph_id)
        self._append_version_record(graph_id, cells)
        cells = [c for c in cells if c.get("id") != cell_id]
        cells.append(copy.deepcopy(old))
        self.save(graph_id, cells)
        self.flush(graph_id, force=True)
        return cells

    def apply_user_operation(self, graph_id: str, cell_id: str, op: str,
                             payload: dict, actor: str = "human") -> list:
        if not self.exists(graph_id):
            raise KeyError("meeting not found")
        cells = self.load(graph_id)
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
            cells = [c for c in cells if c.get("id") != cell_id and
                     (c.get("shape") != "edge" or
                      (_edge_endpoint(c.get("source")) != cell_id and
                       _edge_endpoint(c.get("target")) != cell_id))]
        else:
            raise ValueError(f"unknown operation: {op}")
        self._append_version_record(graph_id, self.load(graph_id))
        self.save(graph_id, cells)
        self.flush(graph_id, force=True)
        return cells

    def _append_meta(self, cell: dict, meta_ids: list) -> None:
        existing = cell["data"].setdefault("metadata_refs", [])
        for m in meta_ids:
            if m not in existing:
                existing.append(m)

    def _merge(self, cell: dict, g: GraphOp) -> None:
        """把 g 的论据合并进已有 cell：metadata_refs 去重追加、mention_count++。"""
        self._append_meta(cell, g.meta_ids)
        cell["data"]["mention_count"] = cell["data"].get("mention_count", 0) + 1

    def _apply_one(self, index: dict, g: GraphOp) -> None:
        if g.op == "add_node":
            if g.node in index:
                self._merge(index[g.node], g)              # 已存在→合并去重
            else:
                nd = NodeData(type=g.node_type or "point", label=g.label or "",
                              metadata_refs=list(g.meta_ids))
                # 坐标由 apply_graph_update 末尾的 relayout 统一计算
                index[g.node] = make_node_cell(g.node, nd)
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
            if g.node not in index or g.parent not in index:
                return
            if index[g.node].get("shape") == "edge" or index[g.parent].get("shape") == "edge":
                return
            node_cell, parent_cell = index[g.node], index[g.parent]
            if is_locked(node_cell) or is_locked(parent_cell):
                return
            if g.node == "n_issue_root" or g.node == g.parent:
                return                                     # 根不可移动；自挂无意义
            if _is_descendant(index, g.parent, g.node):
                return                                     # 环检测：新父不能是自己的子孙
            parent_edges = [c for c in index.values()
                            if c.get("shape") == "edge"
                            and _edge_endpoint(c.get("target")) == g.node]
            if parent_edges:                               # 改写现有父边（多父取首条，其余删除保持树形）
                parent_edges[0]["source"] = {"cell": g.parent}
                parent_edges[0]["data"]["relation"] = g.relation or "subordinate"
                for extra in parent_edges[1:]:
                    index.pop(extra.get("id"), None)
            else:                                          # 无父边（游离节点）→ 新建挂接边
                eid = g.edge or f"e_{g.parent}__{g.node}"
                if eid not in index:
                    index[eid] = make_edge_cell(eid, g.parent, g.node, g.relation or "subordinate")
            node_cell.setdefault("data", {})["cmd"] = {
                "mark": "move", "reason": (g.reason or "").strip()[:80], "by": "user"}
        elif g.op == "link":
            # 端点必须已存在，避免悬空边；同 id 边幂等（不覆盖已有属性）
            if g.source in index and g.target in index:
                eid = g.edge or f"e_{g.source}__{g.target}"
                if eid not in index:
                    index[eid] = make_edge_cell(eid, g.source, g.target, g.relation or "support")
        elif g.op == "merge_as_duplicate":
            if g.parent in index and g.node in index:
                self._merge(index[g.parent], g)
                index[g.parent]["data"]["version"] = index[g.parent]["data"].get("version", 1) + 1
                for edge in index.values():
                    if edge.get("shape") != "edge":
                        continue
                    for endpoint in ("source", "target"):
                        if _edge_endpoint(edge.get(endpoint)) == g.node:
                            edge[endpoint] = {"cell": g.parent}
                del index[g.node]
        elif g.op == "replace":
            if g.node not in index:
                return
            cell = index[g.node]
            if is_locked(cell):
                return                                       # 持久锁：跳过
            if field_writable(cell, "text") and g.label:
                cell["data"]["label"] = g.label
            if field_writable(cell, "type") and g.node_type:
                cell["data"]["type"] = g.node_type
            self._append_meta(cell, g.meta_ids)
        elif g.op == "set_importance":
            if g.node in index and g.importance and field_writable(index[g.node], "importance"):
                index[g.node]["data"]["importance"]["level"] = g.importance
        elif g.op == "relayout":
            # 用户指令：重新排版——解除全部节点的手动定位冻结，坐标交由末尾的 relayout 统一重算
            for cell in index.values():
                if cell.get("shape") == "edge":
                    continue
                cell.setdefault("data", {}).setdefault("edit", {})["position_frozen"] = False

    def commit_graph_update(self, graph_id: str, op: GraphUpdateOp) -> tuple[list, dict]:
        """先在副本上计算并校验，再一次性提交；失败时保留最后可用的看板。"""
        before = self.load(graph_id)
        index = {c["id"]: c for c in copy.deepcopy(before)}
        errors = []
        for g in op.operations:
            try:
                self._apply_one(index, g)
            except Exception as exc:
                errors.append(f"{g.op}: {exc}")
        repaired = []
        if config.CONFIG.auto_repair_orphans and any(
                g.op in _STRUCTURAL_OPS for g in op.operations):
            try:                                    # 游离节点自愈（P2①）：挂回树后随本批一起落库
                repaired = _repair_orphans(index)
            except Exception as e:
                log.warning("[StoreA] orphan repair failed for %s: %r", graph_id, e)
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
        if errors:
            log.warning("[StoreA] rejected update for %s: %s", graph_id, errors)
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
                log.warning("[StoreA] relayout %s failed: %r", gid, e)
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
        return mid if isinstance(mid, str) and mid else StoreB._GLOBAL_SHARD

    def _load(self) -> None:
        """加载顺序（后读覆盖）：旧全局快照 → 旧全局 jsonl → 各分片快照 → 各分片 jsonl。"""
        legacy: dict = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict) and obj.get("schema") != self._MANIFEST_SCHEMA:
                    legacy = obj                       # 旧版：meta_id → record 全量快照
            except Exception as e:
                log.warning("[StoreB] load legacy snapshot failed: %r", e)
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
                        for mid, rec in recs.items():
                            if isinstance(rec, dict):
                                self._data[mid] = rec
                                self._shard_of[mid] = shard
                    except Exception as e:
                        log.warning("[StoreB] load shard %s failed: %r", shard, e)
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
        """回放追加日志到 dict；record=null 表示删除（后写覆盖/删除）。"""
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue                   # 尾部半截行（崩溃残留）忽略
                    mid = obj.get("meta_id")
                    if mid:
                        into[mid] = obj.get("record")   # dict=upsert；None=delete
        except Exception as e:
            log.warning("[StoreB] replay jsonl %s failed: %r", path, e)

    def _append(self, shard: str, meta_id: str, record) -> None:
        """逐条追加到对应分片日志：put/delete 均 O(1) 持久，不触发快照重写。"""
        try:
            with open(self._shard_jsonl_path(shard), "a", encoding="utf-8") as f:
                f.write(json.dumps({"meta_id": meta_id, "record": record}, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            log.warning("[StoreB] append shard jsonl failed: %r", e)

    def put(self, meta_id: str, record: dict) -> None:
        with self._lock:
            shard = self._shard_of.get(meta_id) or self._shard_key(record)
            self._data[meta_id] = record
            self._shard_of[meta_id] = shard
            self._dirty_shards.add(shard)
            self._append(shard, meta_id, record)
            self.flush(force=False)            # 节流：合并分片快照写盘

    def delete(self, meta_id: str) -> bool:
        """删除记录（补偿批次完成后清 pending 标记用，P0①）。"""
        with self._lock:
            if meta_id not in self._data:
                return False
            shard = self._shard_of.pop(meta_id, None) or self._GLOBAL_SHARD
            del self._data[meta_id]
            self._dirty_shards.add(shard)
            self._append(shard, meta_id, None)
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
                    log.warning("[StoreB] truncate shard jsonl failed: %r", e)
            shards = sorted(set(self._shard_of.values()))
            _atomic_write_json(self.path, {"schema": self._MANIFEST_SCHEMA,
                                           "shards": shards, "count": len(self._data)})
            if self._legacy_dirty:
                try:                    # 迁移完成：清空旧全局日志，防下次启动重放覆盖分片
                    open(self.jsonl_path, "w", encoding="utf-8").close()
                except OSError as e:
                    log.warning("[StoreB] truncate legacy jsonl failed: %r", e)
                self._legacy_dirty = False
            self._dirty_shards.clear()
            self._last_flush = now
