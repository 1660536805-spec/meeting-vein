"""从 prompts.yaml 加载双 Agent 提示词与工具 schema，并序列化看板上下文。

- SYSTEM_PROMPT            ：绘图需求描述 + 绘图规范（Design_Agent_DataFlow §2.1 (a)）
- OUTPUT_SCHEMA            ：GraphUpdateOp 输出 schema（5 种 op 字段表 + 受控词表 + few-shot）
- serialize_for_llm(cells) ：关系图压成缩进大纲注入 LLM（Storage §6.2，防上下文膨胀）
- serialize_for_cursor(focus)：光标焦点注记注入提示词（Design_CursorCapture §7 / Research §6）
"""
from __future__ import annotations
import yaml
from pathlib import Path
from typing import List, Optional

from .models import NormCursorEvent

with Path(__file__).with_name("prompts.yaml").open("r", encoding="utf-8") as _file:
    YAML = yaml.safe_load(_file) or {}


def _dig(*path: str):
    node = YAML
    for part in path:
        if not isinstance(node, dict) or part not in node:
            raise RuntimeError(f"prompts.yaml 缺少关键节点 {'.'.join(path)}")
        node = node[part]
    return node

DEFAULT_MEETING_ID = str(_dig("defaults", "meeting_id"))
DEFAULT_MEETING_TITLE = str(_dig("defaults", "meeting_title"))
FILLER_WORDS = tuple(_dig("filter", "filler_words"))
FETCH_METADATA_TOOL = _dig("tools", "fetch_metadata")

SYSTEM_PROMPT = str(_dig("prompts", "syncer_system"))


# 分析 Agent 系统提示（MeetingSummary 输出）。
ANALYZE_SYSTEM_PROMPT = str(_dig("prompts", "analyzer_system"))



OUTPUT_SCHEMA = str(_dig("prompts", "syncer_output_schema"))

EXPERTS = _dig("experts")     # 会议专家技能包：id → {label, prompt}（prompts.yaml）


def expert_addendum(expert_id: Optional[str]) -> str:
    """按所选会议专家返回提示词补充段；未选/未知专家返回空串（回退全能模式）。"""
    if not expert_id:
        return ""
    entry = EXPERTS.get(str(expert_id))
    return str(entry.get("prompt") or "") if isinstance(entry, dict) else ""


MAX_LLM_NODES = 60          # 注入 LLM 的最大节点数（超出按重要度截断，防上下文膨胀）
MAX_LLM_EDGES = 80          # 注入 LLM 的最大边数（超出按端点是否入选截断）
MAX_LLM_REFS = 8            # 每节点最多列出的论据引用数
_IMPORTANCE_RANK = {"high": 2, "normal": 1, "low": 0}


def _estimate_tokens(text: str) -> int:
    """粗估 token 数：CJK 约 1 字/token，其余约 4 字符/token。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4


def _edge_endpoint(endpoint) -> str:
    """边端点兼容 {"cell": id} 与裸字符串两种写法。"""
    if isinstance(endpoint, dict):
        return str(endpoint.get("cell"))
    return str(endpoint)


def serialize_for_llm(cells: list) -> str:
    """Serialize a compact, ancestor-preserving tree so sync can reason about levels."""
    if not cells:
        return "(空图：无预载入节点)"

    nodes = [c for c in cells if c.get("shape") != "edge"]
    edges = [c for c in cells if c.get("shape") == "edge"]
    by_id = {str(c.get("id")): c for c in nodes if c.get("id") is not None}

    def rank(c: dict):
        d = c.get("data", {})
        lvl = (d.get("importance") or {}).get("level")
        return (_IMPORTANCE_RANK.get(lvl, 1), d.get("mention_count", 0), str(c.get("id")))

    parent_by_id = {}
    if any("parent_id" in (c.get("data") or {}) for c in nodes):
        for node in nodes:
            parent = (node.get("data") or {}).get("parent_id")
            if parent in by_id and parent != node.get("id"):
                parent_by_id[str(node["id"])] = str(parent)
    else:
        for edge in edges:
            if (edge.get("data") or {}).get("relation") != "subordinate":
                continue
            source, target = _edge_endpoint(edge.get("source")), _edge_endpoint(edge.get("target"))
            if source in by_id and target in by_id and target not in parent_by_id:
                parent_by_id[target] = source
        # Legacy evidence frequently had only a semantic point→evidence edge.
        evidence_parents = {}
        for edge in edges:
            relation = (edge.get("data") or {}).get("relation")
            source, target = _edge_endpoint(edge.get("source")), _edge_endpoint(edge.get("target"))
            if relation in {"support", "oppose"} and source in by_id and target in by_id \
                    and (by_id[source].get("data") or {}).get("type") == "point" \
                    and (by_id[target].get("data") or {}).get("type") == "evidence":
                evidence_parents.setdefault(target, set()).add(source)
        for target, parents in evidence_parents.items():
            if target not in parent_by_id and len(parents) == 1:
                parent_by_id[target] = next(iter(parents))

    # Select ranked nodes together with their ancestors, so a retained child is
    # never presented as if it were a root-level claim.
    kept = set()
    roots = [str(n["id"]) for n in nodes if str(n["id"]) not in parent_by_id]
    for root in roots:
        if len(kept) < MAX_LLM_NODES:
            kept.add(root)
    for candidate in sorted(nodes, key=rank, reverse=True):
        node_id = str(candidate["id"])
        chain, seen, cursor = [], set(), node_id
        while cursor in by_id and cursor not in seen:
            chain.append(cursor)
            seen.add(cursor)
            cursor = parent_by_id.get(cursor)
            if cursor is None:
                break
        additions = [item for item in reversed(chain) if item not in kept]
        if len(kept) + len(additions) <= MAX_LLM_NODES:
            kept.update(additions)

    children = {}
    for node_id, parent in parent_by_id.items():
        if node_id in kept and parent in kept:
            children.setdefault(parent, []).append(node_id)
    order = {str(node["id"]): i for i, node in enumerate(nodes)}
    for child_ids in children.values():
        child_ids.sort(key=lambda node_id: order.get(node_id, 0))

    def format_node(node_id: str, depth: int, path=frozenset()) -> str:
        if node_id in path:
            return f"{'  ' * depth}- [cycle] {node_id}"
        cell = by_id[node_id]
        d = cell.get("data", {})
        fields = [f"id={node_id}"]
        mentions = d.get("mention_count")
        if mentions and mentions > 1:
            fields.append(f"mentions={mentions}")
        refs_list = d.get("metadata_refs", []) or []
        refs = ",".join(str(ref) for ref in refs_list[:MAX_LLM_REFS])
        if len(refs_list) > MAX_LLM_REFS:
            refs += f",+{len(refs_list) - MAX_LLM_REFS}"
        if refs:
            fields.append(f"refs={refs}")
        line = f"{'  ' * depth}- [{d.get('type')}] {d.get('label')} ({', '.join(fields)})"
        descendants = [format_node(child, depth + 1, path | {node_id}) for child in children.get(node_id, [])]
        return "\n".join([line, *descendants])

    lines = [format_node(root, 0) for root in roots if root in kept]
    # Preserve any kept cycle member that cannot be reached from a root.
    reached = set()
    stack = [root for root in roots if root in kept]
    while stack:
        current = stack.pop()
        if current in reached:
            continue
        reached.add(current)
        stack.extend(children.get(current, []))
    for node in nodes:
        node_id = str(node.get("id"))
        if node_id in kept and node_id not in reached:
            lines.append(format_node(node_id, 0))

    body = "[父节点在前、缩进表示子节点的树形结构]\n" + "\n".join(lines)
    semantic = []
    for edge in edges:
        relation = (edge.get("data") or {}).get("relation", "?")
        if relation in {"subordinate", "child"}:
            continue
        source, target = _edge_endpoint(edge.get("source")), _edge_endpoint(edge.get("target"))
        if source in kept and target in kept:
            # Parent-child meaning is already visible in the outline.
            if parent_by_id.get(target) == source:
                continue
            semantic.append((source, relation, target))
    semantic.sort()
    if semantic:
        body += "\n[跨节点语义关系]\n" + "\n".join(
            f"- {source} --{relation}--> {target}" for source, relation, target in semantic[:MAX_LLM_EDGES])
        if len(semantic) > MAX_LLM_EDGES:
            body += f"\n(已省略 {len(semantic) - MAX_LLM_EDGES} 条关系)"
    dropped = max(0, len(nodes) - len(kept))
    if dropped:
        body += f"\n(为控制上下文，按重要度保留 {len(kept)}/{len(nodes)} 个节点，始终连同祖先节点展示)"
    return f"{body}\n(token≈{_estimate_tokens(body)})"


def serialize_for_cursor(focus: List[NormCursorEvent]) -> str:
    """光标焦点注记（Design_CursorCapture §7）。无焦点返回空串。"""
    if not focus:
        return ""
    parts = []
    for e in focus:
        tgt = e.target.node_id if e.target else None
        dur = (e.end_offset_ms - e.start_offset_ms) if (e.end_offset_ms and e.start_offset_ms) else 0
        parts.append(f"- 手势 {e.gesture_type} @ {tgt}（持续 {dur}ms）")
    return "[用户当前焦点 / 光标交互]\n" + "\n".join(parts) + \
        "\n建议：新要点优先挂接至焦点节点附近；请勿移动或改写用户正在操作的节点。"
