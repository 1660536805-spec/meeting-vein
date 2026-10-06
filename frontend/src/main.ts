import { theme } from "antd";
import { getTheme } from "./theme";
import "antd/dist/reset.css";
import "@antv/x6/dist/index.css";
import {
  createGraph, renderBoard, applyBoardUpdate, collectLocalEdits, clearLocalEdits, focusNode,
} from "./board/render";
import { mountBoardToolbar } from "./board/toolbar";
import { mountDecisionPanel } from "./board/decision-panel";
import { CursorEventAdapter } from "./board/cursor";
import { MascotController, SUPPORTED_MASCOT_MOTIONS } from "./mascot/MascotController";
import { Live2DMascotView } from "./mascot/Live2DMascotView";
import { BoardWS } from "./api/ws";
import { mountAsrPanel } from "./asr/panel";
import { meetingsApi } from "./api/meetings";
import { rest, type CellDiff, type GraphChangeSet } from "./api/rest";
import { ChangeHighlighter } from "./board/changes";
import { downloadPNG, downloadSVG } from "./board/export";
import { buildAdjacency, buildHash, lens, parseHash, reach, route, type DeepLink } from "./board/nav";
import { renderIssueStructure } from "./issues-view";

// 普通入口每次开启独立的空白讨论；历史会议与深链带 meeting_id，继续加载指定会议。
const requestedMeetingId = new URLSearchParams(location.search).get("meeting_id")?.trim();
const MEETING_ID = requestedMeetingId || `mtg_${crypto.randomUUID().replace(/-/g, "")}`;
if (!requestedMeetingId && location.hash) {
  history.replaceState(history.state, "", `${location.pathname}${location.search}`);
}

/** 把 antd 官方设计 token 注入为 CSS 变量，覆盖 index.html 的兜底默认值
 * （满足「文本框使用 antd 库内颜色配置」：颜色全部取自 antd 而非硬编码）。 */
// defaultConfig.token 仅含 seed token；派生字段（colorPrimaryBg/Hover/Active 等）不在其中，
// 直接读会是 undefined → setProperty 写成非法 CSS 值 → 依赖它的皮肤元素回退成 SVG 默认黑色。
// 用 defaultAlgorithm 把 seed 展开为完整 token，所有颜色才真正取自 antd。
function applyAntTheme(): void {
  const dark = getTheme() === "dark";
  const tok = (dark ? theme.darkAlgorithm : theme.defaultAlgorithm)({
    ...theme.defaultConfig.token,
    colorPrimary: dark ? "#8cadf5" : "#2864bc",
    colorSuccess: "#6ccbb3",
    colorWarning: "#e3a962",
    colorError: "#e4869d",
  });
  const root = document.documentElement.style;
  root.setProperty("--ant-primary", tok.colorPrimary);
  root.setProperty("--ant-primary-hover", tok.colorPrimaryHover);
  root.setProperty("--ant-primary-active", tok.colorPrimaryActive);
  root.setProperty("--ant-primary-bg", tok.colorPrimaryBg);
  root.setProperty("--ant-primary-border", tok.colorPrimaryBorder);
  root.setProperty("--ant-border", tok.colorBorder);
  root.setProperty("--ant-border-secondary", tok.colorBorderSecondary);
  root.setProperty("--ant-bg-container", tok.colorBgContainer);
  root.setProperty("--ant-bg-layout", dark ? "#091321" : "#f5f9ff");
  root.setProperty("--ant-bg-elevated", tok.colorBgElevated);
  root.setProperty("--ant-success", tok.colorSuccess);
  root.setProperty("--ant-success-bg", tok.colorSuccessBg);
  root.setProperty("--ant-warning", tok.colorWarning);
  root.setProperty("--ant-warning-bg", tok.colorWarningBg);
  root.setProperty("--ant-error", tok.colorError);
  root.setProperty("--ant-error-bg", tok.colorErrorBg);
  // 预设紫色不在 AliasToken 类型里，运行时存在，加兜底常量（结论节点用）
  root.setProperty("--ant-purple", (tok as any).purple ?? "#722ed1");
  root.setProperty("--ant-purple-bg", (tok as any).purple1 ?? "#f9f0ff");
  root.setProperty("--ant-text", tok.colorText);
  root.setProperty("--ant-text-secondary", tok.colorTextSecondary);
  root.setProperty("--ant-radius", String(tok.borderRadius) + "px");
  root.setProperty("--ant-radius-sm", String(tok.borderRadiusSM) + "px");
  root.setProperty("--ant-control-height", String(tok.controlHeight) + "px");
}
applyAntTheme();
window.addEventListener("huimai-theme-change", applyAntTheme);

const boardEl = document.getElementById("board")!;
const mascotModelHost = document.getElementById("mascot-model")!;
const mascotBubble = document.getElementById("mascot-bubble")!;

const graph = createGraph(boardEl);
const lensSelect = document.getElementById("lens-select") as HTMLSelectElement;
const changeSummary = document.getElementById("change-summary");
const shareStatus = document.getElementById("share-status");
const highlighter = new ChangeHighlighter(graph);
let currentLink: DeepLink = parseHash();
let lastCells: any[] = [];
const decisionPanel = mountDecisionPanel((id) => {
  focusNode(graph, id);
  selectCell(id);
  setLink({ focus: id, reach: id, route: undefined });
});
renderBoard(graph, []);

// ===== 看板内视图切换：关系图 / 议题结构（读取同一份 lastCells，与看板图同源联动）=====
const issueView = document.getElementById("issue-view");
const viewGraphBtn = document.getElementById("view-graph");
const viewIssuesBtn = document.getElementById("view-issues");
let issueViewActive = false;

function renderIssueView(): void {
  if (issueView) renderIssueStructure(issueView, lastCells);
}

function setIssueView(on: boolean): void {
  issueViewActive = on;
  issueView?.toggleAttribute("hidden", !on);
  viewGraphBtn?.setAttribute("aria-selected", String(!on));
  viewIssuesBtn?.setAttribute("aria-selected", String(on));
  if (on) renderIssueView();
}

viewIssuesBtn?.addEventListener("click", () => setIssueView(true));
viewGraphBtn?.addEventListener("click", () => setIssueView(false));

function setSummary(text: string): void {
  if (changeSummary) changeSummary.textContent = text;
}

function refreshView(center = false): void {
  const adjacency = buildAdjacency(lastCells);
  const visibleEdges = lens(adjacency, currentLink.lens);
  let activeNodes: Set<string> | null = null;
  let routeEdges: Set<string> | null = null;
  if (currentLink.route) {
    const path = route(adjacency, currentLink.route[0], currentLink.route[1]);
    if (path) {
      activeNodes = new Set(path.nodes);
      routeEdges = new Set(path.edges.map((edge) => edge.edgeId));
    }
  } else if (currentLink.reach) {
    activeNodes = new Set([currentLink.reach, ...reach(adjacency, currentLink.reach)]);
  }
  for (const node of graph.getNodes()) {
    const opacity = activeNodes && !activeNodes.has(node.id) ? 0.68 : 1;
    node.setAttrByPath("body/opacity", opacity);
    node.setAttrByPath("label/opacity", opacity);
  }
  for (const edge of graph.getEdges()) {
    edge.setVisible(visibleEdges.has(edge.id));
    const onRoute = routeEdges?.has(edge.id) ?? false;
    edge.setAttrByPath("line/opacity", activeNodes && !onRoute &&
      !(activeNodes.has(String(edge.getSourceCellId())) && activeNodes.has(String(edge.getTargetCellId()))) ? 0.6 : 1);
    edge.setAttrByPath("line/strokeWidth", onRoute ? 2.2 : 1.2);
  }
  lensSelect.value = currentLink.lens || "all";
  if (center && currentLink.focus) {
    const cell = graph.getCellById(currentLink.focus);
    if (cell) graph.centerCell(cell);
  }
}

function setLink(patch: Partial<DeepLink>): void {
  currentLink = { ...currentLink, ...patch };
  history.replaceState(null, "", location.pathname + location.search + buildHash(currentLink));
  refreshView();
}

// ===== 手动编辑：选中 / 移动 / 拖弯 / 删除（经 POST /api/node/{id}/op 落库并广播）=====
let selectedId = "";

/** 单个 cell 的选中态描边：加粗（颜色仍由派系/关系着色决定）。 */
function markSelected(id: string, selected: boolean): void {
  const cell = id ? graph.getCellById(id) : null;
  if (!cell) return;
  if (cell.isEdge()) cell.setAttrByPath("line/strokeWidth", selected ? 2.2 : 1.2);
  else {
    const d = cell.getData();
    const high = d?.importance?.level === "high";
    cell.setAttrByPath("body/strokeWidth", selected ? 2 : high ? 1.4 : 1);
  }
}

function selectCell(id: string): void {
  if (id === selectedId) return;
  markSelected(selectedId, false);                  // 恢复旧选中
  selectedId = id;
  markSelected(id, true);
}

graph.on("cell:click", ({ cell }: any) => selectCell(cell.id));

// 节点拖拽落位：持久化坐标并冻结自动布局（position_frozen，广播回放后位置不丢）
graph.on("node:moved", ({ node }: any) => {
  const p = node.getPosition();
  rest.emitUserOp({ node_id: node.id, graph_id: MEETING_ID, op: "move",
                    payload: { x: p.x, y: p.y } })
    .catch(() => setSummary("位置保存失败"));
});

// 连线拖弯/整线拖移：segments 手柄与 edgeMovable 拖拽均以 {ui:true} 写 vertices；
// 防抖 300ms 落库。连线顶点不置 position_frozen（AI 自动布局不受影响）
let edgeVertsTimer: number | undefined;
graph.on("edge:change:vertices", ({ edge, options }: any) => {
  if (!options?.ui) return;
  window.clearTimeout(edgeVertsTimer);
  edgeVertsTimer = window.setTimeout(() => {
    rest.emitUserOp({ node_id: edge.id, graph_id: MEETING_ID, op: "move",
                      payload: { vertices: edge.getVertices() } })
      .catch(() => setSummary("连线位置保存失败"));
  }, 300);
});

// 悬停连线出现拖弯手柄（segments 工具：拖段中点即新增/移动顶点）
graph.on("edge:mouseenter", ({ edge }: any) => edge.addTools("segments"));
graph.on("edge:mouseleave", ({ edge }: any) => edge.removeTool("segments"));

// 删除选中节点/连线（节点删除时后端联动清理相连边，§1.5）
async function deleteSelected(): Promise<void> {
  if (!selectedId) { setSummary("请先点击选中要删除的节点或连线"); return; }
  const cell = graph.getCellById(selectedId);
  if (!cell) return;
  const kind = cell.isEdge() ? "连线" : "节点";
  const label = (cell.attr("label/text") as string) || selectedId;
  if (!window.confirm(`确认删除${kind}「${label}」？`)) return;
  try {
    const r = await rest.emitUserOp({ node_id: selectedId, graph_id: MEETING_ID,
                                      op: "remove", payload: {} });
    setSummary(r?.ok === false ? (r.error || "删除失败") : `已删除${kind}「${label}」`);
    selectCell("");
  } catch {
    setSummary("删除请求失败");
  }
}

// Delete / Backspace 快捷删除（输入控件内不拦截）
window.addEventListener("keydown", (e) => {
  if (e.key !== "Delete" && e.key !== "Backspace") return;
  const t = e.target as HTMLElement | null;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
    || t.tagName === "SELECT" || t.isContentEditable)) return;
  if (!selectedId) return;
  e.preventDefault();
  void deleteSelected();
});
document.getElementById("delete-cell")?.addEventListener("click", () => void deleteSelected());

// ===== 视图联动：语义透镜 / 聚焦 / 深链 =====
lensSelect?.addEventListener("change", () =>
  setLink({ lens: lensSelect.value === "all" ? undefined : lensSelect.value }));
graph.on("node:click", ({ node }) => setLink({ focus: node.id, reach: node.id, route: undefined }));
graph.on("blank:click", () => {
  setLink({ focus: undefined, reach: undefined, route: undefined });
  selectCell("");                                   // 点空白同时取消选中
});
window.addEventListener("hashchange", () => { currentLink = parseHash(); refreshView(true); });
// 重新排版：先清空前端本地定位叠加层（否则本地拖拽坐标会在回放时覆盖后端重排结果），
// 再请求后端把全部节点解除手动定位冻结并按层级重算坐标，结果经 WS board.update 回放。
async function relayoutBoard(): Promise<void> {
  clearLocalEdits();
  try {
    const r = await rest.update(MEETING_ID, [{ op: "relayout" }],
      { thought: "用户触发重排：解除手动定位，按层级重新排版" });
    setSummary(r?.ok === false ? (r.repair_receipt?.errors?.[0] || "重排失败") : "已重新排版");
  } catch {
    setSummary("重排请求失败");
  }
}
const disposeToolbar = mountBoardToolbar(graph, () => void relayoutBoard());   // 底部液态玻璃控制条：缩放/重排
const mascotView = new Live2DMascotView();
const mascot = new MascotController(mascotModelHost, mascotBubble, (name) => mascotView.setMotion(name), SUPPORTED_MASCOT_MOTIONS);
void mascotView.mount(mascotModelHost);
window.addEventListener("pagehide", () => { disposeToolbar(); mascotView.dispose(); }, { once: true });

/** 后端 change_set（cell id 数组）→ 前端 GraphChangeSet（CellDiff 数组），兼容两代词表。 */
function diffOf(ids: unknown): CellDiff[] {
  if (!Array.isArray(ids)) return [];
  const out: CellDiff[] = [];
  for (const id of ids) {
    const cellId = typeof id === "string" ? id : id?.cell_id;
    if (!cellId) continue;
    const cell = graph.getCellById(cellId);
    out.push({ cell_id: cellId, shape: cell && cell.isEdge() ? "edge" : "node" });
  }
  return out;
}

function normalizeChangeSet(cs: any): GraphChangeSet | null {
  if (!cs || typeof cs !== "object") return null;
  return {
    batch_id: cs.batch_id ?? "",
    meeting_summary_ref: cs.meeting_summary_ref ?? "",
    added: diffOf(cs.added),
    modified: diffOf(cs.updated ?? cs.modified),
    removed: diffOf(cs.removed),
    skipped: diffOf(cs.skipped),
    generated_at: cs.generated_at ?? Date.now(),
  };
}

const ws = new BoardWS(`ws://${location.host}/ws`, {
  onBoard: (cells, _graphId, info) => {
    lastCells = cells;
    if (issueViewActive) renderIssueView();
    highlighter.clear();          // 先清旧高亮（引用的是旧 cell），再重渲染
    applyBoardUpdate(graph, cells);
    void decisionPanel.update(cells);
    refreshView();
    markSelected(selectedId, true);   // 重渲染后补回选中态描边
    if (info?.type === "board.rollback") {
      setSummary(`已回滚「${info.cell_id ?? ""}」→ 版本 ${info.version}`);
      return;
    }
    const cs = normalizeChangeSet(info?.change_set);
    if (!cs) { setSummary("看板已更新"); return; }
    const s = highlighter.apply(cs);
    setSummary(`AI 本批：新增 ${s.added} / 修改 ${s.modified} / 跳过 ${s.skipped}`);
    // 焦点契合最近发言：视口居中到本批新增节点（无新增则取修改节点）
    const pick = (list: CellDiff[]) => list.find((d) => d.shape !== "edge")?.cell_id ?? "";
    const target = pick(cs.added) || pick(cs.modified);
    if (target) focusNode(graph, target);
  },
  onMascot: (state) => {
    mascot.setState(state);
  },
}, MEETING_ID);
ws.connect();
mountAsrPanel(document.getElementById("asr-panel")!, MEETING_ID);

// 光标采集 → 总线（节流/hover 停留阈值/拖拽阈值由后端 /api/cursor/config 下发，避免前后端常量漂移）
const cursorAdapter = new CursorEventAdapter(graph, (raw) => ws.sendCursor(raw));
fetch("/api/cursor/config")
  .then((r) => r.json())
  .then((cfg) => cursorAdapter.configure(cfg))
  .catch((e) => console.warn("[cursor] 采集配置下发失败，沿用默认值", e));
window.addEventListener("pagehide", () => cursorAdapter.dispose(), { once: true });

// 首屏拉取看板
fetch(`/api/board?meeting_id=${MEETING_ID}`)
  .then((r) => r.json())
  .then((d) => { lastCells = d.cells; renderBoard(graph, d.cells); void decisionPanel.update(d.cells); refreshView(true); if (issueViewActive) renderIssueView(); })
  .catch((e) => console.warn("[board] 首屏加载失败，等待 WS 推送", e));

// ===== 会议专家：切换要点解析/节点绘制的 skill（后端 prompts.yaml experts，随每次发言推送生效）=====
const expertSelect = document.getElementById("expert-select") as HTMLSelectElement;
const EXPERT_STORAGE_KEY = "amo.expert";
expertSelect.value = localStorage.getItem(EXPERT_STORAGE_KEY) || "general";
if (!expertSelect.value) expertSelect.value = "general";   // 存储值不在选项中时回退全能
expertSelect.addEventListener("change", () => {
  localStorage.setItem(EXPERT_STORAGE_KEY, expertSelect.value);
});

// ===== 底部发言文本框：输入发言 → POST /api/cli/push 实时驱动看板 =====
const input = document.getElementById("composer-input") as HTMLInputElement;
const sendBtn = document.getElementById("composer-send") as HTMLButtonElement;
const composerStatus = document.getElementById("composer-status")!;

function setStatus(text: string, kind: "ok" | "error" | "" = ""): void {
  composerStatus.textContent = text;
  if (kind) composerStatus.dataset.kind = kind;
  else delete composerStatus.dataset.kind;
}

function sendText(): void {
  const text = input.value.trim();
  if (!text) return;
  sendBtn.disabled = true;
  setStatus("发送中…");
  fetch("/api/cli/push", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      meeting_id: MEETING_ID,
      text,
      speaker_ref: "web:user",
      display_name: "网页发言人",
      expert: expertSelect.value || "general",
    }),
  })
    .then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    })
    .then(() => { input.value = ""; setStatus("已发送 ✓", "ok"); })
    .catch((e) => {
      setStatus("发送失败，请重试", "error");
      console.warn("[composer] 发送发言失败", e);
    })
    .finally(() => { sendBtn.disabled = false; input.focus(); });
}
sendBtn.addEventListener("click", sendText);
input.addEventListener("keydown", (e) => { if (e.key === "Enter") sendText(); });
input.addEventListener("input", () => setStatus(""));   // 重新输入即清除上次提示

// ===== 会议管理：创建会议后带 meeting_id 重新挂载实时通道；显式保存生成历史版本 =====
const meetingMessage = document.getElementById("meeting-message");
const createMeetingButton = document.getElementById("new-meeting") as HTMLButtonElement | null;
const saveMeetingButton = document.getElementById("save-meeting") as HTMLButtonElement | null;
const meetingModal = document.getElementById("meeting-modal");
const meetingForm = document.getElementById("meeting-form") as HTMLFormElement | null;
const meetingName = document.getElementById("meeting-name") as HTMLInputElement | null;
const meetingError = document.getElementById("meeting-form-error");
const meetingCancel = document.getElementById("meeting-cancel");

createMeetingButton?.addEventListener("click", () => {
  if (!meetingModal || !meetingName || !meetingError) return;
  meetingError.textContent = "";
  meetingName.value = "";
  meetingModal.hidden = false;
  meetingName.focus();
});

const closeMeetingModal = () => { if (meetingModal) meetingModal.hidden = true; };
meetingCancel?.addEventListener("click", closeMeetingModal);
meetingModal?.addEventListener("click", (event) => {
  if (event.target === meetingModal) closeMeetingModal();
});
meetingName?.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMeetingModal();
});

meetingForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = meetingName?.value.trim() ?? "";
  if (!title) { if (meetingError) meetingError.textContent = "请填写会议名称"; return; }
  const submit = document.getElementById("meeting-create") as HTMLButtonElement;
  submit.disabled = true;
  if (meetingError) meetingError.textContent = "";
  try {
    const result = await meetingsApi.create(title);
    closeMeetingModal();
    location.assign(`/workspace.html?meeting_id=${encodeURIComponent(result.meeting_id)}`);
  } catch (error) {
    if (meetingError) meetingError.textContent = error instanceof Error ? error.message : "创建会议失败";
  } finally {
    submit.disabled = false;
  }
});

saveMeetingButton?.addEventListener("click", async () => {
  saveMeetingButton.disabled = true;
  if (meetingMessage) meetingMessage.textContent = "正在保存…";
  try {
    const result = await meetingsApi.save(MEETING_ID, collectLocalEdits());
    await refreshSavedBoard().catch((error) => console.warn("保存后刷新看板失败", error));
    if (meetingMessage) meetingMessage.textContent = `已保存 v${result.version}`;
  } catch {
    if (meetingMessage) meetingMessage.textContent = "保存失败，请重试";
  } finally {
    saveMeetingButton.disabled = false;
    window.setTimeout(() => { if (meetingMessage) meetingMessage.textContent = ""; }, 3500);
  }
});

async function refreshSavedBoard(): Promise<void> {
  clearLocalEdits();
  const saved = await meetingsApi.board(MEETING_ID);
  lastCells = saved.cells;
  if (issueViewActive) renderIssueView();
  applyBoardUpdate(graph, saved.cells);
  void decisionPanel.update(saved.cells);
  refreshView();
}

document.getElementById("export-svg")?.addEventListener("click", () => {
  try { downloadSVG(graph); } catch { if (shareStatus) shareStatus.textContent = "SVG 导出失败"; }
});
document.getElementById("export-png")?.addEventListener("click", async () => {
  try { await downloadPNG(graph); } catch { if (shareStatus) shareStatus.textContent = "PNG 导出失败"; }
});
document.getElementById("share-snapshot")?.addEventListener("click", async () => {
  try {
    const snapshot = await meetingsApi.shareSnapshot(MEETING_ID, collectLocalEdits());
    await refreshSavedBoard().catch((error) => console.warn("快照生成后刷新看板失败", error));
    const url = new URL(snapshot.url, location.origin).href;
    if (shareStatus) shareStatus.textContent = `v${snapshot.version} 只读链接已生成`;
    await navigator.clipboard.writeText(url).catch(() => {
      if (shareStatus) shareStatus.textContent = url;
    });
  } catch { if (shareStatus) shareStatus.textContent = "快照生成失败"; }
});
document.getElementById("copy-link")?.addEventListener("click", async () => {
  const url = new URL(`/workspace.html?meeting_id=${encodeURIComponent(MEETING_ID)}${buildHash(currentLink)}`, location.origin).href;
  try { await navigator.clipboard.writeText(url); if (shareStatus) shareStatus.textContent = "已复制深链到剪贴板"; }
  catch { if (shareStatus) shareStatus.textContent = url; }
});
