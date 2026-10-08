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
import { parseAgenda } from "./agenda";
import { rest, ApiError, type CellDiff, type GraphChangeSet } from "./api/rest";
import { ChangeHighlighter } from "./board/changes";
import { downloadPNG, downloadSVG } from "./board/export";
import { buildAdjacency, buildHash, lens, parseHash, reach, route, type DeepLink } from "./board/nav";
import { renderIssueStructure } from "./issues-view";
import { projectBoard, projectLocalNeighborhood } from "./board/projection";
import { mountFullscreenToggle } from "./board/fullscreen";
import { cycleNodeId } from "./board/keyboard-nav";
import { mountTaskPlan } from "./task-plan";

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
const disposeFullscreen = mountFullscreenToggle(
  document.getElementById("board-fullscreen") as HTMLButtonElement,
  document.getElementById("board-shell")!,
);
const lensSelect = document.getElementById("lens-select") as HTMLSelectElement;
const changeSummary = document.getElementById("change-summary");
const shareStatus = document.getElementById("share-status");
const highlighter = new ChangeHighlighter(graph);
let currentLink: DeepLink = parseHash();
let lastCells: any[] = [];
let boardVersion = 0;
let undoableOperationVersion: number | null = null;
let selectedId = "";
let graphScope: "local" | "full" = "local";
const focusBaseStyles = new Map<string, { fill: string; stroke: string; label: string; typeLabel: string }>();
function scopedCells(cells: any[]): any[] {
  if (graphScope === "full") return cells;
  const projection = projectBoard(cells);
  const focusId = selectedId || currentLink.reach || currentLink.focus || projection.focus?.id || projection.roots[0];
  return focusId ? projectLocalNeighborhood(cells, focusId, 2) : cells;
}
function renderScopedBoard(fit = false): void {
  focusBaseStyles.clear();
  applyBoardUpdate(graph, scopedCells(lastCells), fit);
  refreshView();
  markSelected(selectedId, true);
  const focusId = selectedId || currentLink.reach || currentLink.focus || projectBoard(lastCells).focus?.id;
  if (focusId) focusNode(graph, focusId);
}
const decisionPanel = mountDecisionPanel((id) => {
  selectCell(id);
  setLink({ focus: id, reach: id, route: undefined });
  renderScopedBoard();
});
renderBoard(graph, []);

// ===== 看板内视图切换：关系图 / 议题结构（读取同一份 lastCells，与看板图同源联动）=====
const issueView = document.getElementById("issue-view");
const viewGraphBtn = document.getElementById("view-graph");
const viewIssuesBtn = document.getElementById("view-issues");
let issueViewActive = false;
const taskView = document.getElementById("task-plan-view");
const viewTasksBtn = document.getElementById("view-tasks");
const taskPlan = taskView ? mountTaskPlan(taskView, MEETING_ID) : null;
window.addEventListener("pagehide", () => taskPlan?.dispose(), { once: true });

function renderIssueView(): void {
  if (issueView) renderIssueStructure(issueView, lastCells, { onViewInGraph: (id) => {
    selectCell(id);
    setLink({ focus: id, reach: id, route: undefined });
    setIssueView(false);
    setGraphScope("local");
  } });
}

function setIssueView(on: boolean): void {
  taskView?.setAttribute("hidden", "");
  document.body.classList.remove("task-plan-mode");
  boardEl.removeAttribute("aria-hidden");
  viewTasksBtn?.setAttribute("aria-selected", "false");
  issueViewActive = on;
  issueView?.toggleAttribute("hidden", !on);
  viewGraphBtn?.setAttribute("aria-selected", String(!on));
  viewIssuesBtn?.setAttribute("aria-selected", String(on));
  if (on) renderIssueView();
}

const scopeLocalBtn = document.getElementById("scope-local");
const scopeFullBtn = document.getElementById("scope-full");
function setGraphScope(scope: "local" | "full"): void {
  graphScope = scope;
  scopeLocalBtn?.setAttribute("aria-pressed", String(scope === "local"));
  scopeFullBtn?.setAttribute("aria-pressed", String(scope === "full"));
  renderScopedBoard(true);
}
scopeLocalBtn?.addEventListener("click", () => setGraphScope("local"));
scopeFullBtn?.addEventListener("click", () => setGraphScope("full"));

viewIssuesBtn?.addEventListener("click", () => setIssueView(true));
viewGraphBtn?.addEventListener("click", () => setIssueView(false));
viewTasksBtn?.addEventListener("click", () => {
  setIssueView(false);
  taskView?.removeAttribute("hidden");
  document.body.classList.add("task-plan-mode");
  boardEl.setAttribute("aria-hidden", "true");
  viewGraphBtn?.setAttribute("aria-selected", "false");
  viewTasksBtn.setAttribute("aria-selected", "true");
  taskPlan?.activate();
});

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
  const focusId = currentLink.reach || selectedId;
  if (activeNodes && focusId) {
    const projection = projectBoard(lastCells);
    const parent = projection.parentById.get(focusId);
    if (parent) activeNodes.add(parent);
    for (const child of projection.children.get(focusId) ?? []) activeNodes.add(child);
  }
  for (const node of graph.getNodes()) {
    if (!focusBaseStyles.has(node.id)) focusBaseStyles.set(node.id, {
      fill: String(node.attr("body/fill") ?? "#24364c"),
      stroke: String(node.attr("body/stroke") ?? "#4c6380"),
      label: String(node.attr("label/fill") ?? "#f2efe4"),
      typeLabel: String(node.attr("typeLabel/fill") ?? "#f2efe4"),
    });
    const base = focusBaseStyles.get(node.id)!;
    const dim = activeNodes !== null && !activeNodes.has(node.id);
    node.setAttrByPath("body/fill", dim ? (document.documentElement.dataset.theme === "light" ? "#e3e8ee" : "#263445") : base.fill);
    node.setAttrByPath("body/stroke", dim ? (document.documentElement.dataset.theme === "light" ? "#b6c0cc" : "#526174") : base.stroke);
    node.setAttrByPath("label/fill", dim ? (document.documentElement.dataset.theme === "light" ? "#596579" : "#a8b4c3") : base.label);
    node.setAttrByPath("typeLabel/fill", dim ? (document.documentElement.dataset.theme === "light" ? "#596579" : "#a8b4c3") : base.typeLabel);
    node.setAttrByPath("body/opacity", 1);
    node.setAttrByPath("label/opacity", 1);
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
window.addEventListener("huimai-theme-change", () => {
  focusBaseStyles.clear();
  refreshView();
});

// ===== 手动编辑：选中 / 移动 / 拖弯 / 删除（经 POST /api/node/{id}/op 落库并广播）=====
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
  const editButton = document.getElementById("edit-cell") as HTMLButtonElement | null;
  if (editButton) editButton.disabled = !id || graph.getCellById(id)?.isEdge();
}

const editor = document.getElementById("node-editor") as HTMLElement | null;
const editorForm = document.getElementById("node-editor-form") as HTMLFormElement | null;
const editorConflict = document.getElementById("node-editor-conflict") as HTMLElement | null;
let editorInitial: Record<string, any> = {};
document.getElementById("edit-cell")?.addEventListener("click", async () => {
  const cell = lastCells.find((item) => item.id === selectedId && item.shape !== "edge");
  if (!cell || !editorForm || !editor) return;
  const data = cell.data ?? {};
  const structuralParent = lastCells.find((item) => item.shape === "edge"
    && item.data?.relation === "subordinate" && (item.target?.cell ?? item.target) === selectedId);
  const currentParent = data.parent_id ?? (structuralParent?.source?.cell ?? structuralParent?.source) ?? null;
  editorInitial = {
    label: data.label ?? "", type: data.type ?? "point", parent_id: currentParent,
    importance: data.importance?.level ?? "normal", status: data.status ?? "open",
  };
  (editorForm.elements.namedItem("label") as HTMLInputElement).value = data.label ?? "";
  (editorForm.elements.namedItem("type") as HTMLSelectElement).value = data.type ?? "point";
  const parentSelect = editorForm.elements.namedItem("parent_id") as HTMLSelectElement;
  parentSelect.replaceChildren(new Option("无所属议题", ""));
  for (const parent of lastCells.filter((item) => item.shape !== "edge" && item.id !== selectedId)) {
    parentSelect.add(new Option(parent.data?.label ?? parent.id, parent.id));
  }
  parentSelect.value = currentParent ?? "";
  const mergeTarget = editorForm.elements.namedItem("merge_target") as HTMLSelectElement;
  mergeTarget.replaceChildren(new Option("选择保留节点…", ""));
  for (const candidate of lastCells.filter((item) => item.shape !== "edge" && item.id !== selectedId)) {
    mergeTarget.add(new Option(candidate.data?.label ?? candidate.id, candidate.id));
  }
  (editorForm.elements.namedItem("importance") as HTMLSelectElement).value = data.importance?.level ?? "normal";
  (editorForm.elements.namedItem("status") as HTMLSelectElement).value = data.status ?? "open";
  (editorForm.elements.namedItem("reason") as HTMLInputElement).value = "";
  if (editorConflict) { editorConflict.hidden = true; editorConflict.textContent = ""; }
  editor.hidden = false;
  (editorForm.elements.namedItem("label") as HTMLInputElement).focus();
  const context = document.getElementById("node-editor-context");
  if (context) {
    context.textContent = "正在加载原话、关系和编辑历史…";
    const related = lastCells.filter((item) => item.shape === "edge"
      && (item.source?.cell ?? item.source) === selectedId || item.shape === "edge"
      && (item.target?.cell ?? item.target) === selectedId);
    const labels = new Map(lastCells.filter((item) => item.shape !== "edge").map((item) => [item.id, item.data?.label ?? item.id]));
    const relationText = related.map((edge) => {
      const source = edge.source?.cell ?? edge.source, target = edge.target?.cell ?? edge.target;
      return `${labels.get(source) ?? source} —${edge.data?.relation ?? "support"}→ ${labels.get(target) ?? target}`;
    });
    const [metadataResult, historyResult] = await Promise.allSettled([
      rest.metadata(data.metadata_refs ?? []), rest.history(MEETING_ID, selectedId),
    ]);
    const evidence = metadataResult.status === "fulfilled"
      ? metadataResult.value.records.map((record) => `原话：${record.display_name || record.speaker_ref || "发言人未知"}：${record.text}`)
      : [];
    const historyRows = historyResult.status === "fulfilled"
      ? historyResult.value.records.slice(-3).map((record: any) => `修改记录 v${record.version} · ${record.change?.actor ?? record.changed_by ?? "用户"} · ${record.change?.reason || record.trigger || "编辑"}`)
      : [];
    const lines = [...evidence, ...(relationText.length ? [`关系：${relationText.join("；")}`] : []), ...historyRows];
    context.textContent = lines.length ? lines.join("\n") : "暂无关联原话、关系或历史记录。";
  }
});
const undoOperationButton = document.getElementById("undo-last-operation") as HTMLButtonElement | null;
undoOperationButton?.addEventListener("click", async () => {
  if (undoableOperationVersion === null) return;
  undoOperationButton.disabled = true;
  try {
    const result = await rest.undoOperation(MEETING_ID, undoableOperationVersion, boardVersion);
    boardVersion = result.version;
    lastCells = result.cells;
    applyBoardUpdate(graph, scopedCells(lastCells));
    void decisionPanel.update(lastCells);
    undoableOperationVersion = null;
    undoOperationButton.hidden = true;
    setSummary(`已撤销删除或合并，当前版本 v${boardVersion}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      boardVersion = Number(error.payload?.current_version ?? boardVersion);
      if (Array.isArray(error.payload?.cells)) {
        lastCells = error.payload.cells;
        applyBoardUpdate(graph, scopedCells(lastCells));
      }
      setSummary(`看板已更新到 v${boardVersion}，请重新确认后再撤销`);
    } else setSummary("撤销失败，历史记录可能已不可用");
  } finally { undoOperationButton.disabled = false; }
});
document.getElementById("node-editor-close")?.addEventListener("click", () => { if (editor) editor.hidden = true; });
document.getElementById("node-editor-merge")?.addEventListener("click", async () => {
  if (!editorForm || !selectedId) return;
  const targetSelect = editorForm.elements.namedItem("merge_target") as HTMLSelectElement;
  const survivorId = targetSelect.value;
  const previewBox = document.getElementById("node-editor-merge-preview");
  if (!survivorId) { setSummary("请选择要保留的节点"); return; }
  try {
    const preview = await rest.previewMerge(MEETING_ID, selectedId, survivorId, boardVersion);
    if (previewBox) {
      const protectedText = preview.protected_node_ids.length
        ? `受保护节点：${preview.protected_node_ids.join("、")}（请先解除锁定或处理人工字段）`
        : "可以合并。";
      previewBox.hidden = false;
      previewBox.textContent = `将把“${preview.duplicate.label}”合并进“${preview.survivor.label}”；重挂 ${preview.reparented_children.length} 个直接子节点（涉及 ${preview.affected_descendants.length} 个后代）、改写 ${preview.rewritten_relations.length} 条关系、转移 ${preview.transferred_metadata_refs.length} 条原话引用。${protectedText}`;
    }
    if (preview.protected_node_ids.length) return;
    const reason = (editorForm.elements.namedItem("reason") as HTMLInputElement).value;
    if (!window.confirm(`确认合并“${preview.duplicate.label}”到“${preview.survivor.label}”？\n子节点、关系和原话引用按上方预览迁移；可从历史版本恢复。`)) return;
    const result = await rest.mergeNodes(MEETING_ID, selectedId, survivorId, boardVersion, reason);
    boardVersion = result.version;
    lastCells = result.cells;
    applyBoardUpdate(graph, scopedCells(lastCells));
    void decisionPanel.update(lastCells);
    if (editor) editor.hidden = true;
    selectCell("");
    undoableOperationVersion = boardVersion;
    if (undoOperationButton) undoOperationButton.hidden = false;
    setSummary(`已合并节点，当前版本 v${boardVersion}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      boardVersion = Number(error.payload?.current_version ?? boardVersion);
      if (Array.isArray(error.payload?.cells)) {
        lastCells = error.payload.cells;
        applyBoardUpdate(graph, scopedCells(lastCells));
        void decisionPanel.update(lastCells);
      }
      if (previewBox) { previewBox.hidden = false; previewBox.textContent = `看板已更新到 v${boardVersion}，请重新预览影响后再合并。`; }
    } else if (previewBox) { previewBox.hidden = false; previewBox.textContent = error instanceof Error ? error.message : "合并失败，请检查节点状态。"; }
  }
});
document.getElementById("node-editor-undo")?.addEventListener("click", async () => {
  if (!selectedId) return;
  try {
    const history = await rest.history(MEETING_ID, selectedId);
    const previous = [...history.records].reverse().find((record: any) =>
      record.version < boardVersion && record.cells?.some((cell: any) => cell.id === selectedId));
    if (!previous) { setSummary("没有可撤销的较早版本"); return; }
    const result = await rest.rollback(MEETING_ID, selectedId, previous.version, boardVersion);
    if (Array.isArray(result.cells)) lastCells = result.cells;
    boardVersion = result.version ?? boardVersion + 1;
    applyBoardUpdate(graph, scopedCells(lastCells));
    void decisionPanel.update(lastCells);
    if (editor) editor.hidden = true;
    setSummary(`已撤销该节点修改，当前版本 v${boardVersion}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      boardVersion = Number(error.payload?.current_version ?? boardVersion);
      if (Array.isArray(error.payload?.cells)) {
        lastCells = error.payload.cells;
        applyBoardUpdate(graph, scopedCells(lastCells));
      }
      setSummary(`看板已更新到 v${boardVersion}，请重新核对后再撤销`);
    } else setSummary("撤销失败，历史版本可能已被清理");
  }
});
editorForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedId) return;
  const entered = {
    label: (editorForm.elements.namedItem("label") as HTMLInputElement).value,
    type: (editorForm.elements.namedItem("type") as HTMLSelectElement).value,
    parent_id: (editorForm.elements.namedItem("parent_id") as HTMLSelectElement).value || null,
    importance: (editorForm.elements.namedItem("importance") as HTMLSelectElement).value,
    status: (editorForm.elements.namedItem("status") as HTMLSelectElement).value,
  };
  const fields = Object.fromEntries(Object.entries(entered).filter(([key, value]) => value !== editorInitial[key]));
  if (!Object.keys(fields).length) { setSummary("没有检测到字段变化"); return; }
  const reason = (editorForm.elements.namedItem("reason") as HTMLInputElement).value;
  const saveButton = editorForm.querySelector<HTMLButtonElement>("[type=submit]");
  if (saveButton) saveButton.disabled = true;
  try {
    const result = await rest.patchNode(MEETING_ID, selectedId, boardVersion, fields, reason);
    boardVersion = result.version;
    lastCells = result.cells;
    applyBoardUpdate(graph, scopedCells(lastCells));
    void decisionPanel.update(lastCells);
    if (editor) editor.hidden = true;
    setSummary("人工修改已保存并锁定相关字段");
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      boardVersion = Number(error.payload?.current_version ?? boardVersion);
      const latestNode = Array.isArray(error.payload?.cells)
        ? error.payload.cells.find((item: any) => item.id === selectedId) : null;
      if (Array.isArray(error.payload?.cells)) {
        lastCells = error.payload.cells;
        applyBoardUpdate(graph, scopedCells(lastCells));
        void decisionPanel.update(lastCells);
        markSelected(selectedId, true);
      }
      if (editorConflict) {
        editorConflict.hidden = false;
        editorConflict.textContent = `看板已更新到 v${boardVersion}。最新内容：${latestNode?.data?.label ?? "节点已变化"}；类型：${latestNode?.data?.type ?? "未知"}。你的草稿仍保留在表单中，核对后再次保存即可基于最新版本提交。`;
      }
    } else setSummary("节点修改保存失败");
  } finally { if (saveButton) saveButton.disabled = false; }
});

graph.on("cell:click", ({ cell }: any) => selectCell(cell.id));
const keyboardStatus = document.getElementById("board-keyboard-status");
function focusGraphNodeByKeyboard(id: string): void {
  selectCell(id);
  setLink({ focus: id, reach: id, route: undefined });
  renderScopedBoard();
  if (keyboardStatus) keyboardStatus.textContent = `已聚焦：${String(graph.getCellById(id)?.attr("label/text") ?? id)}`;
}
boardEl.addEventListener("keydown", (event: KeyboardEvent) => {
  if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter", "Escape"].includes(event.key)) return;
  const ids = graph.getNodes().map((node) => node.id);
  if (event.key === "Escape") {
    event.preventDefault();
    selectCell("");
    setLink({ focus: undefined, reach: undefined, route: undefined });
    renderScopedBoard();
    if (keyboardStatus) keyboardStatus.textContent = "已清除节点焦点";
    return;
  }
  if (["ArrowUp", "ArrowLeft", "ArrowDown", "ArrowRight"].includes(event.key)) {
    event.preventDefault();
    const direction = event.key === "ArrowUp" || event.key === "ArrowLeft" ? -1 : 1;
    const next = cycleNodeId(ids, selectedId || currentLink.reach || "", direction);
    if (next) focusGraphNodeByKeyboard(next);
  } else {
    const target = selectedId || currentLink.reach || ids[0];
    if (target) {
      event.preventDefault();
      focusGraphNodeByKeyboard(target);
    }
  }
});

// 节点拖拽落位：持久化坐标并冻结自动布局（position_frozen，广播回放后位置不丢）
graph.on("node:moved", ({ node }: any) => {
  const p = node.getPosition();
  rest.emitUserOp({ node_id: node.id, graph_id: MEETING_ID, op: "move",
                    payload: { x: p.x, y: p.y }, expected_version: boardVersion })
    .then((result) => { if (typeof result.version === "number") boardVersion = result.version; })
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
                      payload: { vertices: edge.getVertices() }, expected_version: boardVersion })
      .then((result) => { if (typeof result.version === "number") boardVersion = result.version; })
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
  const nodes = lastCells.filter((item) => item.shape !== "edge");
  const parentById = new Map(nodes.filter((item) => item.data?.parent_id).map((item) => [item.id, item.data.parent_id]));
  for (const edge of lastCells.filter((item) => item.shape === "edge" && item.data?.relation === "subordinate")) {
    parentById.set(edge.target?.cell ?? edge.target, edge.source?.cell ?? edge.source);
  }
  const children = cell.isEdge() ? [] : nodes.filter((item) => parentById.get(item.id) === selectedId);
  let childrenAction: "reparent" | "delete" | undefined;
  if (children.length) {
    const choice = window.prompt("该节点有子节点。输入 reparent 将子节点移到当前节点的上级；输入 delete 一并删除后代；取消则不删除。", "reparent");
    if (choice !== "reparent" && choice !== "delete") return;
    childrenAction = choice;
  }
  const affectedNodes = new Set([selectedId]);
  if (childrenAction === "delete") {
    let frontier = new Set(children.map((child) => child.id));
    while (frontier.size) {
      for (const id of frontier) affectedNodes.add(id);
    frontier = new Set(nodes.filter((item) => frontier.has(parentById.get(item.id))
        && !affectedNodes.has(item.id)).map((item) => item.id));
    }
  }
  const affectedRelations = cell.isEdge() ? 0 : lastCells.filter((item) => item.shape === "edge"
    && affectedNodes.has(item.source?.cell ?? item.source) || item.shape === "edge"
    && affectedNodes.has(item.target?.cell ?? item.target)).length;
  const affectedRefs = cell.isEdge() ? 0 : lastCells.filter((item) => affectedNodes.has(item.id))
    .reduce((count, item) => count + (item.data?.metadata_refs?.length ?? 0), 0);
  const impact = cell.isEdge() ? "该连线不会影响节点和原话。"
    : `影响 ${affectedNodes.size - 1} 个后代节点、${affectedRelations} 条关系、${affectedRefs} 条原话引用。`;
  if (!window.confirm(`确认删除${kind}「${label}」？\n\n影响预览：${impact}${childrenAction ? `\n子节点处理：${childrenAction === "reparent" ? "重挂到上级" : "递归删除"}` : ""}\n可通过历史版本恢复。`)) return;
  try {
    const r = await rest.emitUserOp({ node_id: selectedId, graph_id: MEETING_ID,
                                      op: "remove", payload: childrenAction ? { children_action: childrenAction } : {},
                                      expected_version: boardVersion });
    if (typeof r.version === "number") boardVersion = r.version;
    undoableOperationVersion = boardVersion;
    if (undoOperationButton) undoOperationButton.hidden = false;
    setSummary(r?.ok === false ? (r.error || "删除失败") : `已删除${kind}「${label}」`);
    selectCell("");
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      boardVersion = Number(error.payload?.current_version ?? boardVersion);
      if (Array.isArray(error.payload?.cells)) {
        lastCells = error.payload.cells;
        applyBoardUpdate(graph, scopedCells(lastCells));
      }
      setSummary(`看板已更新到 v${boardVersion}，请重新确认删除影响后重试`);
    } else setSummary("删除请求失败");
  }
}

// Delete / Backspace 快捷删除（输入控件内不拦截）
window.addEventListener("keydown", (e) => {
  if (document.body.classList.contains("task-plan-mode")) return;
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
graph.on("node:click", ({ node }) => {
  setLink({ focus: node.id, reach: node.id, route: undefined });
  renderScopedBoard();
});
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
window.addEventListener("pagehide", () => { disposeToolbar(); disposeFullscreen(); mascotView.dispose(); }, { once: true });

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
    if (typeof info?.version === "number" && Number.isFinite(info.version)) boardVersion = info.version;
    if (issueViewActive) renderIssueView();
    highlighter.clear();          // 先清旧高亮（引用的是旧 cell），再重渲染
    focusBaseStyles.clear();
    applyBoardUpdate(graph, scopedCells(cells));
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
document.getElementById("empty-compose")?.addEventListener("click", () => {
  document.getElementById("composer-input")?.focus();
});
document.getElementById("empty-record")?.addEventListener("click", () => {
  (document.getElementById("asr-record") as HTMLButtonElement | null)?.click();
});

// 光标采集 → 总线（节流/hover 停留阈值/拖拽阈值由后端 /api/cursor/config 下发，避免前后端常量漂移）
const cursorAdapter = new CursorEventAdapter(graph, (raw) => ws.sendCursor(raw));
fetch("/api/cursor/config")
  .then((r) => r.json())
  .then((cfg) => cursorAdapter.configure(cfg))
  .catch((e) => console.warn("[cursor] 采集配置下发失败，沿用默认值", e));
window.addEventListener("pagehide", () => cursorAdapter.dispose(), { once: true });

// 首屏拉取看板
fetch(`/api/board?meeting_id=${MEETING_ID}`)
  .then(async (r) => {
    if (!r.ok) throw new Error(`看板加载失败（HTTP ${r.status}）`);
    return r.json();
  })
  .then((d) => {
    if (!Array.isArray(d.cells)) throw new Error("看板数据格式无效");
    lastCells = d.cells;
    boardVersion = d.version ?? boardVersion;
    updateMeetingState(d.status || "draft");
    focusBaseStyles.clear();
    renderBoard(graph, scopedCells(d.cells));
    void decisionPanel.update(d.cells);
    refreshView(true);
    if (issueViewActive) renderIssueView();
  })
  .catch((e) => {
    if (meetingMessage) meetingMessage.textContent = `${e instanceof Error ? e.message : "看板加载失败"}；可点“新建会议”重新开始。`;
    console.warn("[board] 首屏加载失败", e);
  });

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
const meetingAgendaText = document.getElementById("meeting-agenda-text") as HTMLTextAreaElement | null;
const meetingAgendaFile = document.getElementById("meeting-agenda-file") as HTMLInputElement | null;
const meetingAgendaPreview = document.getElementById("meeting-agenda-preview");

function renderAgendaPreview(): void {
  if (!meetingAgendaPreview) return;
  meetingAgendaPreview.replaceChildren();
  const topics = parseAgenda(meetingAgendaText?.value ?? "");
  if (!topics.length) {
    const empty = document.createElement("li");
    empty.textContent = "尚未填写议程";
    meetingAgendaPreview.append(empty);
    return;
  }
  topics.forEach((topic) => {
    const item = document.createElement("li");
    const input = document.createElement("input");
    input.type = "text";
    input.className = "agenda-topic-edit";
    input.value = topic;
    input.setAttribute("aria-label", `编辑议题：${topic}`);
    item.append(input);
    meetingAgendaPreview.append(item);
  });
}

meetingAgendaText?.addEventListener("input", renderAgendaPreview);
meetingAgendaFile?.addEventListener("change", async () => {
  const file = meetingAgendaFile.files?.[0];
  if (!file || !meetingAgendaText) return;
  try {
    meetingAgendaText.value = await file.text();
    renderAgendaPreview();
    if (meetingError) meetingError.textContent = "";
  } catch {
    if (meetingError) meetingError.textContent = "读取议程文件失败；已保留文本框内容，可重新选择文件或直接粘贴。";
  }
});

createMeetingButton?.addEventListener("click", () => {
  if (!meetingModal || !meetingName || !meetingError) return;
  meetingError.textContent = "";
  meetingName.value = "";
  if (meetingAgendaText) meetingAgendaText.value = "";
  if (meetingAgendaFile) meetingAgendaFile.value = "";
  renderAgendaPreview();
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
    const confirmedTopics = [...(meetingAgendaPreview?.querySelectorAll<HTMLInputElement>(".agenda-topic-edit") ?? [])]
      .map((item) => item.value.trim()).filter(Boolean);
    const agendaText = confirmedTopics.length ? confirmedTopics.join("\n") : "";
    const result = await meetingsApi.create(title, agendaText);
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
  focusBaseStyles.clear();
  applyBoardUpdate(graph, scopedCells(saved.cells));
  void decisionPanel.update(saved.cells);
  refreshView();
}

document.getElementById("export-svg")?.addEventListener("click", () => {
  try { downloadSVG(graph); } catch { if (shareStatus) shareStatus.textContent = "SVG 导出失败"; }
});
document.getElementById("export-png")?.addEventListener("click", async () => {
  try { await downloadPNG(graph); } catch { if (shareStatus) shareStatus.textContent = "PNG 导出失败"; }
});

const meetingStateLabel = document.getElementById("meeting-state");
const meetingStartButton = document.getElementById("meeting-start") as HTMLButtonElement | null;
const meetingEndButton = document.getElementById("meeting-end") as HTMLButtonElement | null;
const meetingReopenButton = document.getElementById("meeting-reopen") as HTMLButtonElement | null;

function updateMeetingState(status: string): void {
  const labels: Record<string, string> = { draft: "草稿", live: "进行中", ended: "已结束" };
  if (meetingStateLabel) meetingStateLabel.textContent = `会议状态：${labels[status] || "未知"}`;
  if (meetingStartButton) meetingStartButton.hidden = status !== "draft";
  if (meetingEndButton) meetingEndButton.hidden = status !== "live" && status !== "draft";
  if (meetingReopenButton) meetingReopenButton.hidden = status !== "ended";
}

async function changeMeetingStatus(status: "live" | "ended"): Promise<void> {
  if (status === "ended") {
    const { review } = await meetingsApi.closePreview(MEETING_ID);
    const lines = [
      `待确认结论：${review.unconfirmed.length}`,
      ...review.unconfirmed.map((item) => `· ${item.label}`),
      `未决分歧：${review.disputes.length}`,
      ...review.disputes.map((item) => `· ${item.label || `${item.from || "观点"} ↔ ${item.to || "观点"}`}`),
      `缺负责人/期限的待办：${review.incomplete_actions.length}`,
      ...review.incomplete_actions.map((item) => `· ${item.label}（待补：${item.missing.join("、")}）`),
      "结束后仍可人工修正，也可以重新打开会议。",
    ];
    if (!window.confirm(`结束会议前请检查：\n\n${lines.join("\n")}\n\n确认结束？`)) return;
  }
  const result = await meetingsApi.setStatus(MEETING_ID, status);
  updateMeetingState(result.status);
}

meetingStartButton?.addEventListener("click", () => void changeMeetingStatus("live").catch(() => {
  if (meetingMessage) meetingMessage.textContent = "无法开始会议，请先创建并保存会议。";
}));
meetingEndButton?.addEventListener("click", () => void changeMeetingStatus("ended").catch(() => {
  if (meetingMessage) meetingMessage.textContent = "会议结束检查失败，请确认服务连接后重试。";
}));
meetingReopenButton?.addEventListener("click", () => void changeMeetingStatus("live").catch(() => {
  if (meetingMessage) meetingMessage.textContent = "重新打开会议失败，请重试。";
}));

async function exportMinutes(format: "markdown" | "html"): Promise<void> {
  const blob = await meetingsApi.minutes(MEETING_ID, format);
  downloadBlob(blob, `${MEETING_ID}-minutes.${format === "html" ? "html" : "md"}`);
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

document.getElementById("export-minutes-md")?.addEventListener("click", () => void exportMinutes("markdown").catch(() => {
  if (shareStatus) shareStatus.textContent = "纪要导出失败，请检查服务连接。";
}));
document.getElementById("export-minutes-html")?.addEventListener("click", () => void exportMinutes("html").catch(() => {
  if (shareStatus) shareStatus.textContent = "纪要导出失败，请检查服务连接。";
}));
document.getElementById("export-snapshot")?.addEventListener("click", async () => {
  try {
    const blob = await meetingsApi.localSnapshot(MEETING_ID);
    downloadBlob(blob, `${MEETING_ID}-snapshot.json`);
    if (shareStatus) shareStatus.textContent = "本地快照文件已下载，包含原话和历史记录。";
  } catch { if (shareStatus) shareStatus.textContent = "本地快照导出失败，请检查服务连接。"; }
});
document.getElementById("share-snapshot")?.addEventListener("click", async () => {
  try {
    const snapshot = await meetingsApi.shareSnapshot(MEETING_ID, collectLocalEdits());
    await refreshSavedBoard().catch((error) => console.warn("快照生成后刷新看板失败", error));
    const url = new URL(snapshot.url, location.origin).href;
    if (shareStatus) shareStatus.textContent = `v${snapshot.version} 本地只读链接已生成；仅能在可访问此服务的环境打开`;
    await navigator.clipboard.writeText(url).catch(() => {
      if (shareStatus) shareStatus.textContent = `${url}（仅可由能访问此服务的环境打开）`;
    });
  } catch { if (shareStatus) shareStatus.textContent = "快照生成失败"; }
});
document.getElementById("copy-link")?.addEventListener("click", async () => {
  const url = new URL(`/workspace.html?meeting_id=${encodeURIComponent(MEETING_ID)}${buildHash(currentLink)}`, location.origin).href;
  try { await navigator.clipboard.writeText(url); if (shareStatus) shareStatus.textContent = "已复制同一服务内可用的会议链接"; }
  catch { if (shareStatus) shareStatus.textContent = `${url}（仅可由能访问此服务的环境打开）`; }
});
