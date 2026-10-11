import "@antv/x6/dist/index.css";
import type { Graph } from "@antv/x6";
import { createGraph, renderBoard } from "./board/render";
import { mountBoardToolbar } from "./board/toolbar";
import { meetingsApi, type MeetingSummary, type MeetingHistoryRecord, type UtteranceRecord,
  type MeetingTimelineEntry } from "./api/meetings";

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
const linkForm = document.getElementById("history-link-form") as HTMLFormElement;
const sourceMeeting = document.getElementById("history-source-meeting") as HTMLSelectElement;
const sourceTopic = document.getElementById("history-source-topic") as HTMLSelectElement;
const targetTopic = document.getElementById("history-target-topic") as HTMLSelectElement;
const linkActor = document.getElementById("history-link-actor") as HTMLInputElement;
const linkReason = document.getElementById("history-link-reason") as HTMLInputElement;
const linkMessage = document.getElementById("history-link-message")!;
const timelineHost = document.getElementById("history-timeline")!;
let meetings: MeetingSummary[] = [];
let activeId = new URLSearchParams(location.search).get("meeting_id") || "";
let graph: Graph | null = null;

function setTopicOptions(select: HTMLSelectElement, cells: any[]): void {
  select.replaceChildren();
  for (const cell of cells) {
    if (cell.shape === "edge" || cell.data?.type !== "issue" || cell.id === "n_issue_root") continue;
    const option = document.createElement("option");
    option.value = cell.id;
    option.textContent = cell.data.label || cell.id;
    select.append(option);
  }
}

async function updateSourceTopics(): Promise<void> {
  sourceTopic.replaceChildren();
  if (!sourceMeeting.value) return;
  const requestedId = sourceMeeting.value;
  try {
    const board = await meetingsApi.board(requestedId);
    if (sourceMeeting.value === requestedId) setTopicOptions(sourceTopic, board.cells);
  } catch { linkMessage.textContent = "旧会议议题加载失败，请刷新后重试。"; }
}

function renderTimeline(entries: MeetingTimelineEntry[]): void {
  timelineHost.replaceChildren();
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.textContent = "尚未关联旧会议议题。由主持人确认后才会显示跨会复盘。";
    timelineHost.append(empty);
  }
  for (const entry of entries) {
    const card = document.createElement("section");
    card.className = "history-timeline-card";
    const heading = document.createElement("strong");
    heading.textContent = `${entry.source_meeting.title} · ${entry.old_topic.topic.label} → ${entry.new_topic.topic.label}`;
    const reason = document.createElement("p");
    reason.textContent = `${entry.link.actor}：${entry.link.reason}`;
    card.append(heading, reason);
    const groups: Array<[string, string[]]> = [
      ["上次人工决议", entry.old_topic.confirmed_decisions.map((item) => item.label)],
      ["本次新增证据", entry.new_topic.evidence.flatMap((item) =>
        item.source_utterances.map((source) => source.text || `${item.label}（原话待核对）`))],
      ["本次人工决议", entry.new_topic.confirmed_decisions.map((item) => item.label)],
      ["上次未完成行动", entry.unfinished_actions.map((item) =>
        `${item.label} · ${item.owner || "待分配"} · ${item.status}`)],
    ];
    for (const [label, values] of groups) {
      const section = document.createElement("p");
      section.textContent = `${label}：${values.length ? values.join("；") : "暂无"}`;
      card.append(section);
    }
    timelineHost.append(card);
  }
}

async function updateTimeline(id: string): Promise<void> {
  try {
    const result = await meetingsApi.timeline(id);
    if (activeId === id) renderTimeline(result.entries);
  } catch { if (activeId === id) timelineHost.textContent = "跨会时间线加载失败，请刷新后重试。"; }
}

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
    const status = { draft: "草稿", live: "进行中", ended: "已结束" }[item.meeting_status || "draft"];
    const topics = item.agenda?.length ? ` · ${item.agenda.join("、")}` : " · 未设置议题";
    time.textContent = `${date(item.created_at)} · ${status}${topics}`;
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
    const summary = meetings.find((item) => item.meeting_id === id);
    const state = { draft: "草稿", live: "进行中", ended: "已结束" }[summary?.status || "draft"];
    const agenda = summary?.agenda?.length ? ` · 议题：${summary.agenda.join("、")}` : " · 未设置议题";
    meta.textContent = `${date(summary?.updated_at)} · ${state} · 版本 ${board.version}${agenda}`;
    open.href = `/workspace.html?meeting_id=${encodeURIComponent(id)}`;
    if (!graph) {
      graph = createGraph(document.getElementById("history-board")!, true);
      mountBoardToolbar(graph);                      // 只读缩放控制条：−/比例/＋/全图/1:1
    }
    renderBoard(graph, board.cells);
    renderUtterances(utter.utterances);
    renderRecords(history.records);
    setTopicOptions(targetTopic, board.cells);
    sourceMeeting.replaceChildren();
    for (const item of meetings.filter((meeting) => meeting.meeting_id !== id)) {
      const option = document.createElement("option");
      option.value = item.meeting_id;
      option.textContent = item.title;
      sourceMeeting.append(option);
    }
    linkForm.hidden = sourceMeeting.options.length === 0 || targetTopic.options.length === 0;
    await Promise.all([updateSourceTopics(), updateTimeline(id)]);
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
sourceMeeting.addEventListener("change", () => void updateSourceTopics());
linkForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activeId || !sourceMeeting.value || !sourceTopic.value || !targetTopic.value) return;
  const targetId = activeId;
  const button = linkForm.querySelector<HTMLButtonElement>("button[type=submit]")!;
  button.disabled = true;
  linkMessage.textContent = "正在保存关联…";
  try {
    await meetingsApi.linkMeeting(targetId, { source_meeting_id: sourceMeeting.value,
      source_topic_node_id: sourceTopic.value, target_topic_node_id: targetTopic.value,
      event_id: crypto.randomUUID(), actor: linkActor.value.trim(), reason: linkReason.value.trim() });
    linkMessage.textContent = "跨会关联已保存";
    await updateTimeline(targetId);
  } catch { linkMessage.textContent = "关联失败；请核对议题或刷新后重试。"; }
  finally { button.disabled = false; }
});
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
