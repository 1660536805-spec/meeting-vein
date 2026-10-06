/** 议题结构页（issues.html）：会议历史 → 议题结构视图。
 * 层级渲染复用 src/issues-view.ts 的 renderIssueStructure（与看板内视图同源），
 * 交互范式（列表 / 深链 / 刷新 / 错误提示）与 history.ts 保持一致。 */
import { meetingsApi, type MeetingSummary } from "./api/meetings";
import { renderIssueStructure } from "./issues-view";

const list = document.getElementById("history-list")!;
const empty = document.getElementById("history-list-empty")!;
const placeholder = document.getElementById("history-placeholder")!;
const content = document.getElementById("history-content")!;
const titleEl = document.getElementById("history-title")!;
const metaEl = document.getElementById("history-meta")!;
const openEl = document.getElementById("history-open") as HTMLAnchorElement;
const body = document.getElementById("issues-body")!;
const error = document.getElementById("history-error")!;

let meetings: MeetingSummary[] = [];
let activeId = new URLSearchParams(location.search).get("meeting_id") || "";

function date(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN");
}

function showError(reason: unknown): void {
  const message = reason instanceof Error ? reason.message : "";
  error.textContent = message.includes("Not Found")
    ? "历史会议接口尚未生效，请重启后端服务后刷新页面。"
    : "议题结构加载失败，请检查后端服务并重试。";
  error.hidden = false;
}

function renderList(): void {
  list.replaceChildren();
  empty.hidden = meetings.length > 0;
  for (const meeting of meetings) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item";
    button.setAttribute("role", "listitem");
    button.setAttribute("aria-current", String(meeting.meeting_id === activeId));
    const name = document.createElement("strong");
    name.textContent = meeting.title;
    const details = document.createElement("small");
    details.textContent = `${date(meeting.updated_at)} · ${meeting.stats.nodes} 个节点`;
    button.append(name, details);
    button.addEventListener("click", () => void selectMeeting(meeting.meeting_id));
    list.append(button);
  }
}

async function selectMeeting(id: string): Promise<void> {
  activeId = id;
  error.hidden = true;
  renderList();
  try {
    const board = await meetingsApi.board(id);
    if (activeId !== id) return;
    placeholder.hidden = true;
    content.hidden = false;
    titleEl.textContent = board.title || id;
    metaEl.textContent = `${date(meetings.find((item) => item.meeting_id === id)?.updated_at)} · 版本 ${board.version}`;
    openEl.href = `/workspace.html?meeting_id=${encodeURIComponent(id)}`;
    renderIssueStructure(body, board.cells);
  } catch (reason) {
    showError(reason);
  }
}

async function loadMeetings(): Promise<void> {
  error.hidden = true;
  try {
    meetings = (await meetingsApi.list()).meetings;
    if (activeId && !meetings.some((item) => item.meeting_id === activeId)) activeId = "";
    renderList();
    if (activeId) await selectMeeting(activeId);
    else if (meetings.length) await selectMeeting(meetings[0].meeting_id);
    else { placeholder.hidden = false; content.hidden = true; }
  } catch (reason) {
    showError(reason);
  }
}

document.getElementById("issues-refresh")!.addEventListener("click", () => void loadMeetings());
void loadMeetings();
