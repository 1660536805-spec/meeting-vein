import "@antv/x6/dist/index.css";
import type { Graph } from "@antv/x6";
import { createGraph, renderBoard } from "./board/render";
import { mountBoardToolbar } from "./board/toolbar";
import { meetingsApi, type MeetingSummary, type MeetingHistoryRecord, type UtteranceRecord } from "./api/meetings";

const list = document.getElementById("history-list")!;
const empty = document.getElementById("history-list-empty")!;
const placeholder = document.getElementById("history-placeholder")!;
const content = document.getElementById("history-content")!;
const title = document.getElementById("history-title")!;
const meta = document.getElementById("history-meta")!;
const open = document.getElementById("history-open") as HTMLAnchorElement;
const utteranceList = document.getElementById("history-utterance-list")!;
const records = document.getElementById("history-record-list")!;
const error = document.getElementById("history-error")!;
const renameForm = document.getElementById("rename-form") as HTMLFormElement;
const renameInput = document.getElementById("rename-input") as HTMLInputElement;
let meetings: MeetingSummary[] = [];
let activeId = new URLSearchParams(location.search).get("meeting_id") || "";
let graph: Graph | null = null;

function date(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN");
}

function showError(reason: unknown): void {
  const message = reason instanceof Error ? reason.message : "";
  error.textContent = message.includes("Not Found")
    ? "历史会议接口尚未生效，请重启后端服务后刷新页面。"
    : "历史会议加载失败，请检查后端服务并重试。";
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

function renderRecords(items: MeetingHistoryRecord[]): void {
  records.replaceChildren();
  if (items.length === 0) {
    const note = document.createElement("p");
    note.textContent = "尚无手动保存记录。可在看板右上角点击「保存会议」。";
    records.append(note);
    return;
  }
  for (const item of [...items].reverse()) {
    const card = document.createElement("div");
    card.className = "history-record";
    const name = document.createElement("strong");
    name.textContent = `第 ${item.version} 版 · ${item.title}`;
    const time = document.createElement("small");
    time.textContent = date(item.created_at);
    card.append(name, time);
    records.append(card);
  }
}

function renderUtterances(items: UtteranceRecord[]): void {
  utteranceList.replaceChildren();
  if (items.length === 0) {
    const note = document.createElement("p");
    note.className = "history-utterance-empty";
    note.textContent = "暂无逐句对话记录。较早创建的会议未存原始发言，此后在看板中的新讨论会逐句记录在此。";
    utteranceList.append(note);
    return;
  }
  items.forEach((item, index) => {
    const card = document.createElement("div");
    card.className = "history-utterance";
    const head = document.createElement("div");
    head.className = "history-utterance-head";
    const speaker = document.createElement("strong");
    speaker.textContent = item.speaker_ref || "未知发言人";
    const seq = document.createElement("span");
    seq.textContent = `#${index + 1}`;
    head.append(speaker, seq);
    const text = document.createElement("p");
    text.textContent = item.text;
    card.append(head, text);
    utteranceList.append(card);
  });
}

async function selectMeeting(id: string): Promise<void> {
  activeId = id;
  error.hidden = true;
  renderList();
  try {
    const [board, history, utter] = await Promise.all([
      meetingsApi.board(id), meetingsApi.history(id), meetingsApi.utterances(id)]);
    if (activeId !== id) return;
    placeholder.hidden = true;
    content.hidden = false;
    title.textContent = board.title || id;
    meta.textContent = `${date(meetings.find((item) => item.meeting_id === id)?.updated_at)} · 版本 ${board.version}`;
    open.href = `/workspace.html?meeting_id=${encodeURIComponent(id)}`;
    if (!graph) {
      graph = createGraph(document.getElementById("history-board")!, true);
      mountBoardToolbar(graph);                      // 只读缩放控制条：−/比例/＋/全图/1:1
    }
    renderBoard(graph, board.cells);
    renderUtterances(utter.utterances);
    renderRecords(history.records);
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

document.getElementById("history-refresh")!.addEventListener("click", () => void loadMeetings());
document.getElementById("history-rename")!.addEventListener("click", () => {
  renameInput.value = title.textContent || "";
  renameForm.hidden = false;
  renameInput.focus();
});
document.getElementById("rename-cancel")!.addEventListener("click", () => { renameForm.hidden = true; });
renameForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activeId || !renameInput.value.trim()) return;
  try {
    await meetingsApi.rename(activeId, renameInput.value.trim());
    renameForm.hidden = true;
    await loadMeetings();
  } catch (reason) { showError(reason); }
});

void loadMeetings();
