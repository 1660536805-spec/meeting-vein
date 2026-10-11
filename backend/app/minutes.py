"""Meeting agenda parsing and source-aware minutes exports."""
from __future__ import annotations

import html
import re
from typing import Iterable


_AGENDA_MARKER = re.compile(r"^\s*(?:(?:[-*+])\s+|(?:\d{1,3}[.、)]|[一二三四五六七八九十]+[、.])\s*)")


def parse_agenda(text: str) -> list[str]:
    """Parse plain text/Markdown into ordered top-level topics."""
    topics: list[str] = []
    seen: set[str] = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```") or line.startswith(">"):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = _AGENDA_MARKER.sub("", line).strip()
        line = re.sub(r"\s+#+\s*$", "", line).strip()
        line = re.sub(r"\*\*(.*?)\*\*|__(.*?)__", lambda m: m.group(1) or m.group(2), line)
        line = re.sub(r"`([^`]*)`", r"\1", line)
        if line.casefold() in {"议程", "会议议程", "agenda", "meeting agenda"}:
            continue
        if line and line not in seen:
            seen.add(line)
            topics.append(line)
    return topics


def _node_label(cell: dict) -> str:
    return str((cell.get("data") or {}).get("label") or cell.get("id") or "未命名")


def _minutes_markdown(title: str, agenda: list[str], status: str,
                      cells: Iterable[dict], utterances: Iterable[dict],
                      history: Iterable[dict] = (), confirmed_tasks: Iterable[dict] = ()) -> str:
    nodes = [c for c in cells if isinstance(c, dict) and c.get("shape") != "edge"]
    edges = [c for c in cells if isinstance(c, dict) and c.get("shape") == "edge"]
    by_id = {c.get("id"): c for c in nodes}
    utterance_by_id = {u.get("meta_id"): u for u in utterances if isinstance(u, dict)}
    planned_by_node: dict[str, list[dict]] = {}
    for task in confirmed_tasks:
        if task.get("source_node_id"):
            planned_by_node.setdefault(task["source_node_id"], []).append(task)
    structural_parent = {}
    for edge in edges:
        if (edge.get("data") or {}).get("relation") != "subordinate":
            continue
        source = edge.get("source", {})
        target = edge.get("target", {})
        source_id = source.get("cell") if isinstance(source, dict) else source
        target_id = target.get("cell") if isinstance(target, dict) else target
        if target_id in by_id and source_id in by_id:
            structural_parent[target_id] = source_id
    agenda_topics = list(agenda or [])
    issue_nodes = [c for c in nodes if (c.get("data") or {}).get("type") == "issue"]
    if not agenda_topics:
        agenda_topics = [_node_label(c) for c in issue_nodes if c.get("id") != "n_issue_root"]
    if not agenda_topics:
        agenda_topics = ["未设置议题"]

    def topic_for(node: dict) -> str:
        data = node.get("data") or {}
        parent = by_id.get(data.get("parent_id") or structural_parent.get(node.get("id")))
        while parent:
            pd = parent.get("data") or {}
            if pd.get("type") == "issue":
                label = _node_label(parent)
                if label in agenda_topics:
                    return label
                # Legacy v1 boards can have an issue node without parent_id.
                return label
            parent = by_id.get(pd.get("parent_id") or structural_parent.get(parent.get("id")))
        return agenda_topics[0]

    lines = [f"# {title}", "", f"会议状态：{'已结束' if status == 'ended' else '进行中' if status == 'live' else '草稿'}", "", "## 议程"]
    lines.extend(f"- {topic}" for topic in agenda_topics)
    manual_changes = [record for record in history if isinstance(record, dict)
                      and (record.get("change") or {}).get("source") == "manual"]
    lines.extend(["", "## 人工改动说明"])
    if not manual_changes:
        lines.append("- 暂无带审计记录的人工改动")
    for record in manual_changes:
        change = record.get("change") or {}
        fields = "、".join(change.get("fields", {}).keys()) or "人工操作"
        lines.append(f"- v{record.get('version', '?')} · {change.get('actor') or '人工'} · {change.get('reason') or '未填写原因'} · 修改：{fields}")
    for topic in agenda_topics:
        lines.extend(["", f"## {topic}", "", "### 已确认结论"])
        topic_nodes = [n for n in nodes if topic_for(n) == topic]
        confirmed = [n for n in topic_nodes if (n.get("data") or {}).get("type") == "conclusion"
                     and (n.get("data") or {}).get("status") == "confirmed"]
        if not confirmed:
            lines.append("- 暂无已确认结论")
        for node in confirmed:
            data = node.get("data") or {}
            refs = data.get("metadata_refs") or []
            source = "人工创建（未关联原话）" if not refs else "；".join(
                f"{(utterance_by_id.get(ref) or {}).get('speaker_ref') or '未知发言人'}："
                f"{(utterance_by_id.get(ref) or {}).get('text') or '原话记录不可用'}"
                for ref in refs)
            lines.append(f"- **{_node_label(node)}**（依据：{source}）")
            if data.get("manual_override"):
                lines.append(f"  - 人工改动：{', '.join(sorted(data['manual_override'])) if isinstance(data['manual_override'], dict) else '已人工校正'}")

        lines.extend(["", "### 未决与分歧"])
        unresolved = [n for n in topic_nodes if (n.get("data") or {}).get("status") == "needs_confirmation"
                      or (n.get("data") or {}).get("type") in {"conflict", "question"}]
        unresolved_edge_ids = set()
        for edge in edges:
            data = edge.get("data") or {}
            if data.get("relation") != "oppose" or data.get("status") in {"resolved", "closed"}:
                continue
            source = edge.get("source", {})
            target = edge.get("target", {})
            source_id = source.get("cell") if isinstance(source, dict) else source
            target_id = target.get("cell") if isinstance(target, dict) else target
            unresolved_edge_ids.update((source_id, target_id))
        unresolved.extend(by_id[i] for i in unresolved_edge_ids if i in by_id)
        unique = {n.get("id"): n for n in unresolved if n.get("id")}
        if not unique:
            lines.append("- 暂无未决项")
        for node in unique.values():
            lines.append(f"- {_node_label(node)}（未确认）")

        lines.extend(["", "### 行动项"])
        todos = [n for n in topic_nodes if (n.get("data") or {}).get("type") in {"todo", "action"}]
        if not todos:
            lines.append("- 暂无行动项")
        for node in todos:
            data = node.get("data") or {}
            plans = planned_by_node.get(node.get("id"), [])
            planned = plans[0] if len(plans) == 1 else {}
            owner = planned.get("owner") or data.get("owner") or data.get("assignee")
            due = planned.get("due_date") or data.get("deadline") or data.get("due_date")
            status_text = ({"todo": "未开始", "in_progress": "进行中", "done": "已完成"}.get(planned.get("status"))
                           or data.get("status") or "待确认")
            gaps = []
            if not owner:
                gaps.append("待补充负责人")
            if not due:
                gaps.append("待补充期限")
            lines.append(f"- {_node_label(node)}（负责人：{owner or '待补充'}；期限：{due or '待补充'}；状态：{status_text}；{'、'.join(gaps) if gaps else '字段完整'}）")
            refs = data.get("metadata_refs") or []
            if refs:
                for ref in refs:
                    utterance = utterance_by_id.get(ref)
                    if utterance:
                        lines.append(f"  - 原话：{utterance.get('speaker_ref') or '未知发言人'}：{utterance.get('text') or ''}")

    return "\n".join(lines).rstrip() + "\n"


def _markdown_to_safe_html(markdown: str) -> str:
    rendered: list[str] = []
    in_list = False
    for line in markdown.splitlines():
        if line.startswith("# ") or line.startswith("## ") or line.startswith("### "):
            if in_list:
                rendered.append("</ul>")
                in_list = False
            level = len(line) - len(line.lstrip("#"))
            rendered.append(f"<h{level}>{html.escape(line[level + 1:])}</h{level}>")
        elif line.startswith("- ") or line.startswith("  - "):
            if not in_list:
                rendered.append("<ul>")
                in_list = True
            text = html.escape(line.strip()[2:])
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
            rendered.append(f"<li>{text}</li>")
        elif line.strip():
            if in_list:
                rendered.append("</ul>")
                in_list = False
            rendered.append(f"<p>{html.escape(line)}</p>")
        elif in_list:
            rendered.append("</ul>")
            in_list = False
    if in_list:
        rendered.append("</ul>")
    return "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>会议纪要</title><body><main>" + "".join(rendered) + "</main></body></html>"


def render_minutes(title: str, agenda: list[str], status: str, cells: Iterable[dict],
                   utterances: Iterable[dict], *, format: str = "markdown",
                   history: Iterable[dict] = (), task_plan_markdown: str = "",
                   confirmed_tasks: Iterable[dict] = ()) -> str:
    markdown = _minutes_markdown(title, agenda, status, cells, utterances, history, confirmed_tasks)
    if task_plan_markdown:
        markdown += "\n" + task_plan_markdown
    if format == "markdown":
        return markdown
    if format == "html":
        return _markdown_to_safe_html(markdown)
    raise ValueError("format must be markdown or html")


def review_before_close(cells: Iterable[dict]) -> dict:
    """Return the unresolved decisions, disagreements and incomplete actions to review."""
    nodes = [c for c in cells if isinstance(c, dict) and c.get("shape") != "edge"]
    edges = [c for c in cells if isinstance(c, dict) and c.get("shape") == "edge"]
    by_id = {c.get("id"): c for c in nodes}
    unconfirmed = [{"id": c.get("id"), "label": _node_label(c)} for c in nodes
                   if (c.get("data") or {}).get("type") == "conclusion"
                   and (c.get("data") or {}).get("status") != "confirmed"]
    disputes = []
    for edge in edges:
        data = edge.get("data") or {}
        if data.get("relation") != "oppose" or data.get("status") in {"resolved", "closed"}:
            continue
        source = edge.get("source", {})
        target = edge.get("target", {})
        source_id = source.get("cell") if isinstance(source, dict) else source
        target_id = target.get("cell") if isinstance(target, dict) else target
        if source_id in by_id and target_id in by_id:
            disputes.append({"from": _node_label(by_id[source_id]), "to": _node_label(by_id[target_id])})
    incomplete_actions = []
    for cell in nodes:
        data = cell.get("data") or {}
        if data.get("type") not in {"todo", "action"}:
            continue
        missing = []
        if not (data.get("owner") or data.get("assignee")):
            missing.append("负责人")
        if not (data.get("deadline") or data.get("due_date")):
            missing.append("期限")
        if missing:
            incomplete_actions.append({"id": cell.get("id"), "label": _node_label(cell), "missing": missing})
    for cell in nodes:
        data = cell.get("data") or {}
        if data.get("type") == "conflict" and data.get("status") not in {"resolved", "closed"}:
            disputes.append({"label": _node_label(cell)})
    return {"unconfirmed": unconfirmed, "disputes": disputes,
            "incomplete_actions": incomplete_actions}
