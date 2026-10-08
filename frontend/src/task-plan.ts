import { Graph } from "@antv/x6";
import { taskPlanApi, type PlanDocument, type PlanTask, type TaskStatus, type Source, type PlanAnswer } from "./task-plan-api";

const statuses: Record<TaskStatus, string> = { todo: "未开始", in_progress: "进行中", done: "已完成" };

/** Layers encode prerequisites, independent of the board's parent/support/opposition edges. */
export function taskLayers(tasks: PlanTask[]): Map<string, number> {
  const result = new Map<string, number>();
  const remaining = new Map(tasks.map(t => [t.id, t]));
  if (remaining.size !== tasks.length) throw new Error("任务 ID 重复");
  while (remaining.size) {
    const ready = [...remaining.values()].filter(t => t.depends_on.every(d => result.has(d)));
    if (!ready.length) throw new Error("前置任务不存在或依赖形成循环，请修改依赖。");
    for (const task of ready) {
      result.set(task.id, Math.max(-1, ...task.depends_on.map(d => result.get(d)!)) + 1);
      remaining.delete(task.id);
    }
  }
  return result;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, text = "", cls = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

export function mountTaskPlan(root: HTMLElement, meetingId: string) {
  const api = taskPlanApi(meetingId);
  let doc: PlanDocument = { meeting_id: meetingId, version: 0, draft: null, confirmed: null };
  let scope: "draft" | "confirmed" = "draft";
  let tasks: PlanTask[] = [], sources: Source[] = [];
  let selected = "", busy = false, dirty = false, unapplied = false, loaded = false;
  let answer: PlanAnswer | null = null;
  const header = el("header", "", "plan-header");
  const heading = el("div");
  heading.append(el("h2", "把讨论变成下一步"), el("p", "核对分工与日期，确认后跟进执行。箭头表示前置任务。"));
  const tools = el("div", "", "plan-tools");
  const dateLabel = el("label", "排期起点");
  const reference = el("input"); reference.type = "date";
  const today = new Date();
  reference.value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  dateLabel.append(reference);
  function button(text: string, run: () => void, cls = "") {
    const b = el("button", text, cls); b.type = "button"; b.addEventListener("click", run); return b;
  }
  const generate = button("生成任务草稿", () => {
    if ((dirty || unapplied) && !window.confirm("生成会替换未保存的修改，继续生成？")) return;
    if (!reference.value) { message.textContent = "请先选择排期起点。"; return; }
    void mutate(() => api.generate(doc.version, reference.value), "新草稿已生成。请核对建议字段，再确认计划。", "draft");
  }, "plan-primary");
  const refresh = button("刷新计划", () => {
    if ((dirty || unapplied) && !window.confirm("刷新会舍弃未保存的修改，继续刷新？")) return;
    void load();
  });
  const exportBtn = button("导出计划", () => {
    if (dirty || unapplied) { message.textContent = "请先保存草稿，再导出最新修改。"; return; }
    const a = el("a"); a.href = api.exportUrl(scope); a.download = `${meetingId}-task-plan.md`; a.click();
  });
  tools.append(dateLabel, generate, refresh, exportBtn); header.append(heading, tools);
  const notice = el("div", "", "plan-notice");
  const message = el("p", "", "plan-message"); message.setAttribute("role", "status"); message.setAttribute("aria-live", "polite");
  const switches = el("div", "", "plan-switches");
  const draftBtn = button("草稿", () => chooseScope("draft"));
  const confirmedBtn = button("执行计划", () => chooseScope("confirmed"));
  const save = button("保存草稿", () => void mutate(() => api.save(doc.version, tasks), "草稿已保存，尚未确认。"));
  const confirm = button("确认并开始执行", () => {
    if (doc.confirmed && !window.confirm("这份草稿将替换正式计划；同一任务的执行状态会保留。确认替换？")) return;
    void mutate(() => api.confirm(doc.version, tasks), "计划已人工确认，可以更新任务进度。", "confirmed");
  }, "plan-primary");
  switches.append(draftBtn, confirmedBtn, save, confirm);
  const body = el("div", "", "plan-body");
  const visual = el("section", "", "plan-visual"); visual.setAttribute("aria-label", "任务依赖关系");
  const canvas = el("div", "", "plan-canvas");
  // X6 autoResize observes its parent. Keep that parent independent of graph content.
  const canvasFrame = el("div", "", "plan-canvas-frame"); canvasFrame.append(canvas);
  const empty = el("div", "", "plan-empty");
  empty.append(el("h3", "还没有会后任务"), el("p", "先在会议中记录需要完成的工作，再生成草稿。真实 AI 可从原话提取任务；规则演示只读取已有行动项。"));
  const graphTools = el("div", "", "plan-graph-tools");
  graphTools.append(button("适应画布", () => graph?.zoomToFit({ maxScale: 1, minScale: .3, padding: 30 })),
                    button("放大", () => graph?.zoom(.15)), button("缩小", () => graph?.zoom(-.15)));
  const list = el("div", "", "plan-task-list"); list.setAttribute("aria-label", "任务列表：点击编辑");
  visual.append(canvasFrame, empty, graphTools, list);
  const aside = el("aside", "", "plan-aside");
  const inspector = el("section", "", "plan-inspector"); inspector.setAttribute("aria-label", "任务详情");
  const qa = el("section", "", "plan-qa"); qa.append(el("h3", "理解这份计划"), el("p", "仅依据当前会议原话和已保存的计划回答。"));
  const question = el("textarea"); question.rows = 2; question.maxLength = 2000; question.placeholder = "例如：哪些任务需要先做？"; question.setAttribute("aria-label", "计划问题");
  const replies = el("div", "", "plan-answer"); replies.setAttribute("aria-live", "polite");
  const ask = button("询问计划", () => void askQuestion());
  qa.append(question, ask, replies); aside.append(inspector, qa); body.append(visual, aside);
  root.replaceChildren(header, notice, switches, message, body);
  let graph: Graph | null = null;

  function chooseScope(next: "draft" | "confirmed") {
    if (busy) return;
    if ((dirty || unapplied) && !window.confirm("切换会舍弃未保存的修改，继续切换？")) return;
    scope = next; adopt(doc); message.textContent = "";
  }
  function adopt(next: PlanDocument) {
    doc = next;
    if (next.sources) sources = next.sources;
    if (!doc[scope]) scope = doc.draft ? "draft" : "confirmed";
    tasks = structuredClone(doc[scope]?.tasks ?? []);
    dirty = false; unapplied = false; answer = null; replies.replaceChildren();
    if (!tasks.some(t => t.id === selected)) selected = tasks[0]?.id ?? "";
    paint();
  }
  async function mutate(run: () => Promise<PlanDocument>, success: string, nextScope?: "draft" | "confirmed") {
    if (busy) return;
    busy = true; paintControls(); message.textContent = "正在处理，请稍候…";
    try {
      const next = await run(); if (nextScope) scope = nextScope;
      adopt(next); message.textContent = success;
    } catch (error) { message.textContent = error instanceof Error ? error.message : "操作失败。当前输入已保留，请重试。"; }
    finally { busy = false; paintControls(); }
  }
  async function load() {
    if (busy) return;
    busy = true; paintControls(); message.textContent = "正在读取任务计划…";
    try { adopt(await api.read()); loaded = true; message.textContent = ""; }
    catch (error) { message.textContent = error instanceof Error ? error.message : "计划读取失败，请刷新重试。"; }
    finally { busy = false; paintControls(); }
  }
  function paintControls() {
    for (const b of [generate, refresh, exportBtn, draftBtn, confirmedBtn, save, confirm, ask]) b.disabled = busy;
    reference.disabled = busy;
    exportBtn.disabled ||= !tasks.length || dirty || unapplied;
    draftBtn.hidden = !doc.draft; confirmedBtn.hidden = !doc.confirmed;
    draftBtn.setAttribute("aria-pressed", String(scope === "draft")); confirmedBtn.setAttribute("aria-pressed", String(scope === "confirmed"));
    save.hidden = confirm.hidden = scope !== "draft" || !doc.draft;
    save.disabled ||= !dirty || unapplied; confirm.disabled ||= !tasks.length || unapplied;
    ask.disabled ||= !tasks.length || dirty || unapplied;
    root.setAttribute("aria-busy", String(busy));
    inspector.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLButtonElement>("input,select,button").forEach(e => { e.disabled = busy; });
  }
  function paint() {
    const plan = doc[scope];
    const label = plan?.mode === "ai" ? "AI 编排" : "规则演示 · 真实 AI 尚未启用";
    notice.replaceChildren(el("strong", `${scope === "draft" ? "待人工确认" : "已人工确认"} · ${label}`),
      el("span", scope === "draft" ? "负责人、日期和依赖均可调整；建议字段需要你核对。" : `已完成 ${tasks.filter(t => t.status === "done").length} / ${tasks.length} 项 · 进度由成员手动更新`));
    if (!plan) notice.replaceChildren(el("span", "生成后先核对草稿，再确认执行。"));
    for (const warning of plan?.warnings ?? []) notice.append(el("p", warning));
    empty.hidden = Boolean(tasks.length); graphTools.hidden = !tasks.length;
    list.replaceChildren();
    for (const task of tasks) {
      const b = button(`${task.title} · ${task.owner || "负责人待确认"}`, () => select(task.id));
      b.setAttribute("aria-pressed", String(task.id === selected)); list.append(b);
    }
    paintGraph(); paintInspector(); paintControls();
  }
  function select(id: string) {
    if (busy) return;
    if (unapplied && !window.confirm("当前任务的输入尚未应用，切换会舍弃这些输入。继续切换？")) return;
    unapplied = false; selected = id; paint();
  }
  function paintGraph() {
    if (!graph && !root.hidden && tasks.length) {
      graph = new Graph({ container: canvas, autoResize: canvasFrame, panning: true, mousewheel: { enabled: true, modifiers: ["ctrl", "meta"] },
                          interacting: false, background: { color: "transparent" }, scaling: { min: .3, max: 2 } });
      graph.on("node:click", ({ node }) => select(node.id));
    }
    if (!graph) return;
    const colors = getComputedStyle(document.documentElement);
    const color = (name: string, fallback: string) => colors.getPropertyValue(name).trim() || fallback;
    let layers: Map<string, number>;
    try { layers = taskLayers(tasks); } catch (error) { message.textContent = (error as Error).message; return; }
    const rows = new Map<number, number>();
    // X6 rect defaults apply center-reference transforms to every <text> selector.
    const textStyle = { refX: 0, refY: 0, textAnchor: "start", fontFamily: "inherit" };
    graph.clearCells();
    for (const task of tasks) {
      const layer = layers.get(task.id)!; const row = rows.get(layer) ?? 0; rows.set(layer, row + 1);
      const highlighted = task.id === selected || answer?.task_ids.includes(task.id);
      graph.addNode({ id: task.id, x: 24 + layer * 306, y: 24 + row * 154, width: 244, height: 130,
        markup: [{ tagName: "rect", selector: "body" }, { tagName: "text", selector: "title" },
                 { tagName: "text", selector: "owner" }, { tagName: "text", selector: "dates" }, { tagName: "text", selector: "state" }],
        attrs: {
          body: { width: 244, height: 130, rx: 12, ry: 12, fill: color("--ant-bg-container", "#fff"), stroke: color(highlighted ? "--ant-primary" : "--ant-border", "#2864bc"), strokeWidth: highlighted ? 2 : 1 },
          title: { ...textStyle, x: 16, y: 16, textVerticalAnchor: "top", fill: color("--ant-text", "#152032"), fontSize: 15, fontWeight: 600, text: task.title, textWrap: { width: 212, height: 38, ellipsis: true } },
          owner: { ...textStyle, x: 16, y: 66, fill: color("--ant-text", "#152032"), fontSize: 13, text: task.owner || "负责人待确认", textWrap: { width: 212, height: 18, ellipsis: true } },
          dates: { ...textStyle, x: 16, y: 88, fill: color("--ant-text-secondary", "#455572"), fontSize: 12, text: `${task.start_date || "待定"} → ${task.due_date || "待定"}` },
          state: { ...textStyle, x: 16, y: 112, fill: color("--ant-primary", "#2864bc"), fontSize: 12, text: scope === "draft" ? "待确认草稿" : statuses[task.status] },
        },
      });
    }
    for (const task of tasks) for (const dep of task.depends_on) graph.addEdge({ source: { cell: dep, anchor: "right" }, target: { cell: task.id, anchor: "left" },
      router: { name: "manhattan" }, connector: { name: "rounded" }, attrs: { line: { stroke: color("--ant-primary", "#2864bc"), strokeWidth: 1.5, targetMarker: "classic" } } });
  }
  function sourceDetails(task: PlanTask) {
    const details = el("details", "", "plan-sources"); details.append(el("summary", "查看任务依据"));
    if (task.source_quote) details.append(el("blockquote", task.source_quote));
    const refs = task.source_refs.map(r => sources.find(s => s.meta_id === r)).filter((s): s is Source => Boolean(s));
    for (const source of refs) details.append(el("blockquote", `${source.speaker}：${source.text}`));
    if (!refs.length) details.append(el("p", `来自会议行动项 ${task.source_node_id || "原话"}；未关联可读取的逐句原话。`));
    return details;
  }
  function paintInspector() {
    inspector.replaceChildren(el("h3", scope === "draft" ? "核对任务" : "任务与进度"));
    const task = tasks.find(t => t.id === selected);
    if (!task) { inspector.append(el("p", "生成草稿后，点击任务查看详情。")); return; }
    if (scope === "confirmed") {
      inspector.append(el("h4", task.title), el("p", `${task.owner} · ${task.start_date} 至 ${task.due_date}`));
      const label = el("label", "执行状态"); const state = el("select");
      state.setAttribute("aria-label", "执行状态");
      for (const [key, text] of Object.entries(statuses)) { const option = el("option", text); option.value = key; state.append(option); }
      state.value = task.status;
      state.addEventListener("change", () => { const status = state.value as TaskStatus; state.value = task.status; void mutate(() => api.progress(doc.version, task.id, status), "任务进度已保存。"); });
      label.append(state); inspector.append(label);
    } else {
      const form = el("form");
      function field(text: string, value: string, type = "text") {
        const label = el("label", text); const input = el("input"); input.type = type; input.value = value; input.setAttribute("aria-label", text); label.append(input); form.append(label); return input;
      }
      const title = field("任务名称", task.title); title.maxLength = 240; title.required = true;
      const owner = field("负责人", task.owner); owner.maxLength = 128;
      const dates = el("div", "", "plan-date-fields");
      const start = field("开始日期", task.start_date || "", "date");
      const due = field("截止日期", task.due_date || "", "date");
      dates.append(start.parentElement!, due.parentElement!); form.append(dates);
      const deps = el("fieldset"); deps.append(el("legend", "前置任务（完成后才能开始）"));
      for (const other of tasks.filter(t => t.id !== task.id)) {
        const label = el("label", "", "plan-dependency"); const check = el("input"); check.type = "checkbox"; check.value = other.id; check.checked = task.depends_on.includes(other.id); label.append(check, el("span", other.title)); deps.append(label);
      }
      if (tasks.length < 2) deps.append(el("p", "当前没有其他任务。"));
      const apply = el("button", "应用修改"); apply.type = "submit";
      form.append(deps, apply);
      // Track field input immediately: switching, querying or confirming cannot drop unapplied edits.
      form.addEventListener("input", () => { unapplied = true; paintControls(); message.textContent = "请先点「应用修改」，再保存或确认草稿。"; });
      form.addEventListener("submit", event => {
        event.preventDefault(); if (busy) return;
        if (start.value && due.value && start.value > due.value) { message.textContent = "开始日期不能晚于截止日期。"; return; }
        const changed = { ...task, title: title.value.trim(), owner: owner.value.trim(), start_date: start.value || null, due_date: due.value || null,
          depends_on: [...deps.querySelectorAll<HTMLInputElement>("input:checked")].map(i => i.value) };
        const next = tasks.map(t => t.id === task.id ? changed : t);
        try { taskLayers(next); } catch (error) { message.textContent = (error as Error).message; return; }
        tasks = next; dirty = true; unapplied = false; paint(); message.textContent = "修改已应用到本地草稿，请保存或确认。";
      });
      inspector.append(form);
      if (task.suggested_fields.length) inspector.append(el("p", `建议字段待核对：${task.suggested_fields.map(s => ({ owner: "负责人", dates: "日期", dependencies: "依赖" }[s] || s)).join("、")}`, "plan-hint"));
    }
    if (task.rationale) inspector.append(el("p", task.rationale, "plan-rationale"));
    inspector.append(sourceDetails(task));
  }
  async function askQuestion() {
    if (busy || dirty) return;
    if (!question.value.trim()) { message.textContent = "请填写你想了解的问题。"; return; }
    busy = true; paintControls(); replies.textContent = "正在查看会议与任务计划…";
    try {
      answer = await api.ask(doc.version, scope, question.value.trim());
      replies.replaceChildren(el("p", answer.mode === "demo" ? "规则演示查询" : "AI 回答", "plan-hint"), el("p", answer.answer));
      for (const id of answer.task_ids) { const task = tasks.find(t => t.id === id); if (task) replies.append(button(`查看任务：${task.title}`, () => select(id))); }
      for (const source of answer.sources) { const details = el("details"); details.append(el("summary", `原话依据 · ${source.speaker}`), el("blockquote", source.text)); replies.append(details); }
      paintGraph();
    } catch (error) { replies.textContent = error instanceof Error ? error.message : "问答失败，请重新提问。"; }
    finally { busy = false; paintControls(); }
  }
  const themeListener = () => paintGraph();
  const unloadListener = (event: BeforeUnloadEvent) => { if (dirty || unapplied) { event.preventDefault(); event.returnValue = ""; } };
  window.addEventListener("huimai-theme-change", themeListener);
  window.addEventListener("beforeunload", unloadListener);
  return {
    activate: () => { if (!loaded) void load(); else paintGraph(); },
    dispose: () => { window.removeEventListener("huimai-theme-change", themeListener); window.removeEventListener("beforeunload", unloadListener); graph?.dispose(); },
  };
}
