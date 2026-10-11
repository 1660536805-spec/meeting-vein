"""iCalendar export for open, dated actions."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Mapping


STATUS_LABELS = {
    "open": "待开始",
    "in_progress": "进行中",
    "delayed": "已延期",
}


def _escape_text(value: object) -> str:
    text = str(value or "")
    return (text.replace("\\", "\\\\").replace("\r\n", "\\n")
            .replace("\r", "\\n").replace("\n", "\\n")
            .replace(";", "\\;").replace(",", "\\,"))


def _fold_line(line: str, limit: int = 75) -> str:
    """Fold at UTF-8 codepoint boundaries, counting octets per RFC 5545."""
    result: list[str] = []
    current = ""
    size = 0
    for char in line:
        width = len(char.encode("utf-8"))
        if current and size + width > limit:
            result.append(current)
            current = " "
            size = 1
        current += char
        size += width
    result.append(current)
    return "\r\n".join(result)


def render_action_calendar(meeting_title: str, actions: Iterable[dict],
                           labels: Mapping[str, str], *,
                           now: datetime | None = None,
                           repeat_overdue: bool = False) -> str:
    """Render due actions as stable, locally subscribable calendar events.

    Events use floating local time at 09:00 on the due date. The VALARMs fire
    one day before and at the event start; clients can show both reminders.
    Overdue items in a subscription repeat daily, starting at the next usable
    local 09:00, until the server feed removes them. A downloaded snapshot moves
    an overdue reminder to the same next 09:00 and never creates an indefinite
    recurrence. Completed, cancelled, undated, and malformed records are omitted.
    """
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    stamp = timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    local_now = timestamp.astimezone()
    today = local_now.date()
    past_due_time = (local_now.hour, local_now.minute, local_now.second) >= (9, 0, 0)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Huimai//Meeting Actions//ZH",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape_text((meeting_title or '会议') + ' · 行动提醒')}",
    ]
    active = []
    for action in actions or []:
        if action.get("status") not in STATUS_LABELS or not action.get("due_date"):
            continue
        try:
            due = date.fromisoformat(action["due_date"])
        except (TypeError, ValueError):
            continue
        active.append((due, action))
    for due, action in sorted(active, key=lambda item: (item[0], str(item[1].get("id", "")))):
        action_id = str(action.get("id") or "action")
        label = str(action.get("calendar_label") or
                    labels.get(str(action.get("action_node_id") or ""), "待办事项"))
        action_meeting_title = str(action.get("calendar_meeting_title") or meeting_title or "会议")
        owner = action.get("owner") or "待分配"
        status = STATUS_LABELS[action["status"]]
        overdue = due < today or (due == today and past_due_time)
        reminder_date = due
        if overdue:
            # Start overdue feeds at the next usable occurrence. Starting the
            # unbounded daily rule on the original due date creates a long
            # history of stale events and can make clients import old alarms.
            reminder_date = today + timedelta(days=1) if past_due_time else today
        due_stamp = reminder_date.strftime("%Y%m%d")
        scoped_uid = str(action.get("calendar_uid") or action_id)
        if action.get("calendar_meeting_title"):
            summary = f"[会脉 · {action_meeting_title}{' · 逾期' if overdue else ''}] {label}"
        else:
            summary = f"[{'逾期待办' if overdue else '会脉行动'}] {label}"
        description = f"会议：{action_meeting_title}\n负责人：{owner}\n状态：{status}\n行动编号：{action_id}"
        if overdue:
            description += f"\n原截止日期：{due.isoformat()}"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{_escape_text(scoped_uid)}@huimai.local",
            f"DTSTAMP:{stamp}",
            "ORGANIZER;CN=会脉:mailto:noreply@huimai.invalid",
            f"SEQUENCE:{int(action.get('version') or 1)}",
            f"DTSTART:{due_stamp}T090000",
            f"DURATION:PT30M",
            *( ["RRULE:FREQ=DAILY"] if overdue and repeat_overdue else [] ),
            f"SUMMARY:{_escape_text(summary)}",
            f"DESCRIPTION:{_escape_text(description)}",
            "STATUS:CONFIRMED",
            "TRANSP:TRANSPARENT",
            *([] if overdue else [
                "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-P1D",
                f"DESCRIPTION:{_escape_text('明日到期：' + str(label))}", "END:VALARM",
            ]),
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "TRIGGER:PT0M",
            f"DESCRIPTION:{_escape_text(('逾期待办：' if overdue else '今日到期：') + str(label))}",
            "END:VALARM",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold_line(line) for line in lines) + "\r\n"
