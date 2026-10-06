import { Graph } from "@antv/x6";
import { beginAutoAdjust, composeAutoFit, endAutoAdjust, getUserZoomFactor } from "./view-scale";

/** X6 看板渲染（Design_StructureGraph_Storage §3 / Research_X6_MindMap）。
 * 后端 Store A 的 cell 采用 X6 v1 风格 + 自定义 shape(amo-node)，本层做 v1→v2 归一化：
 *   - shape: amo-node → rect（内置，免注册）；edge 保留
 *   - position{x,y}/size{w,h} → 顶层 x/y/width/height（X6 v2 期望）
 *   - data.label → attrs.label.text（节点显示文字）
 * 归一化后与 X6 同构成 {nodes, edges}，fromJSON 零歧义（Design §3.2）。
 *
 * 视觉三重着色（辩论派系可视化，深色会议画布）：
 *   - 派系描边：拓扑推导——根节点主蓝，根下每个一级分支依次取派系色，子孙继承（同派系同色）
 *   - 类型填充：节点类型决定底色（NODE_STYLES），类型角标沿用类型色
 *   - 关系描边：边 relation 决定线色（support/subordinate 主蓝 / oppose 红 / duplicate 金 / replace 紫）
 * 布局：议题结构思维导图（对齐 issues-view 的层级规则）——议题居顶，观点分支横向分列，
 *   每列的论点/论据自上而下逐层展开；当根只有唯一实质分支（辩题容器，排除「其他」旁支）
 *   且其下仍有多个分支时，容器居中作为枢纽、改按其子分支分列，使对垒双方成为并列的大列。
 *   工作台为呈现层级一律重算坐标（不再保留散落的旧拖拽位置）；其余视图保留 position_frozen；
 *   用户拖弯边的 vertices 原样回放。 */
const PRIMARY = "#91b5f1";
const TEXT = "#f2efe4";
const BORDER = "#4c6380";
const isDecisionWorkspace = () => document.body.classList.contains("decision-workspace");
// AntV 基础分类 10 色：以类型稳定映射六色，底色按页面主题混合。
// https://antv.antgroup.com/zh/specification/language/palette/
const ANTV_NODE_COLORS: Record<string, string> = {
  issue: "#5B8FF9", point: "#61DDAA", evidence: "#65789B",
  conclusion: "#7262FD", action: "#F6903D", conflict: "#F08BB4",
};
// 亮色主题参考用户提供的白底、薄荷绿、浅蓝、淡紫与浅粉配色。
const LIGHT_NODE_STYLES: Record<string, { fill: string; stroke: string }> = {
  issue: { fill: "#F0F7FF", stroke: "#4B87DA" },
  point: { fill: "#EEFBF5", stroke: "#23B894" },
  evidence: { fill: "#EFF8FE", stroke: "#369BD2" },
  conclusion: { fill: "#F6F1FC", stroke: "#9B7BC8" },
  action: { fill: "#EFFCF7", stroke: "#20BE9C" },
  conflict: { fill: "#FFF2F7", stroke: "#DF5D94" },
};
const NODE_ICONS: Record<string, string> = {
  issue: "M3 5h25v19H13l-10 6V5Z",
  point: "M5 20v9m10-18v18m10-26v26",
  evidence: "M6 3h20v27H6Zm5 8h10m-10 7h10m-10 6h7",
  conclusion: "M5 30V3m0 4c8-7 15 7 23 0l-4 17c-8 6-12-7-19 0",
  action: "M4 17l8 8L29 5",
  conflict: "M16 9v11m0 5v1M16 2a14 14 0 1 0 0 28 14 14 0 1 0 0-28",
};
const isLightTheme = () => document.documentElement.dataset.theme === "light";
function mixColor(color: string, background: string, amount: number): string {
  const channels = [1, 3, 5].map(offset => {
    const foreground = parseInt(color.slice(offset, offset + 2), 16);
    const base = parseInt(background.slice(offset, offset + 2), 16);
    return Math.round(foreground * amount + base * (1 - amount)).toString(16).padStart(2, "0");
  });
  return `#${channels.join("")}`;
}
function nodeStyle(type: string) {
  const light = isLightTheme();
  const reference = LIGHT_NODE_STYLES[type] ?? LIGHT_NODE_STYLES.point;
  const color = light ? reference.stroke : ANTV_NODE_COLORS[type] ?? ANTV_NODE_COLORS.point;
  return {
    fill: light ? reference.fill : mixColor(color, "#1C2D43", 0.34),
    stroke: light ? color : mixColor(color, "#FFFFFF", 0.82),
    // 文字和图标加深，避免绿色、橙色在浅底上难以辨认。
    accent: light ? mixColor(color, "#182D48", 0.7) : mixColor(color, "#FFFFFF", 0.82),
    icon: NODE_ICONS[type] ?? NODE_ICONS.point,
  };
}
const graphBackground = () => isLightTheme() ? "#F8FCFF" : "#0b1b2e";
const graphText = () => isLightTheme() ? "#111827" : TEXT;

const NODE_TYPE_LABELS: Record<string, string> = {
  issue: "议题",
  point: "要点",
  evidence: "论据",
  conclusion: "结论",
  action: "待办",
  conflict: "不同意见",
};

/** 边按 data.relation 差异化着色（5 类）。 */
const EDGE_COLORS: Record<string, string> = {
  support: PRIMARY,
  subordinate: PRIMARY,
  oppose: "#e39bad",
  duplicate: "#e0b375",
  replace: "#b9a0df",
};
const EDGE_LABELS: Record<string, string> = {
  subordinate: "归属",
  oppose: "反对",
  duplicate: "重复",
  replace: "替换",
};

/** 派系色板（根下一级分支依次取色，子孙继承同色；不含根主蓝，避免与根混淆）。 */
const FACTION_COLORS = ["#722ed1", "#fa8c16", "#13c2c2", "#eb2f96", "#52c41a", "#faad14"];

/** 树形布局参数：列宽（横向观点间距）/ 行高（列内纵向间距）/ 顶部与左侧留白。 */
const X_GAP = 250, LEVEL_H = 150, TOP = 40, LEFT = 60;

/** 节点四向连接桩：新增导线的起止锚点（默认淡显、hover 高亮，见 index.html 的 .x6-port-body）。
 * 不定义 ports 时 connecting 无从起手，用户无法手动连边。 */
function nodePorts(stroke: string) {
  const circle = () => ({ r: 4, magnet: true, stroke, strokeWidth: 1.5, fill: graphBackground() });
  return {
    groups: {
      left: { position: "left", attrs: { circle: circle() } },
      top: { position: "top", attrs: { circle: circle() } },
      right: { position: "right", attrs: { circle: circle() } },
      bottom: { position: "bottom", attrs: { circle: circle() } },
    },
    items: [
      { id: "left", group: "left" },
      { id: "top", group: "top" },
      { id: "right", group: "right" },
      { id: "bottom", group: "bottom" },
    ],
  };
}

/** 把一条边（无论来自后端还是前端手绘）统一成 X6 v2 的边 cell 并套样式。
 * 后端 cell 与本地新建边共用同一套外观，保证「框↔框」连线风格一致；
 * vertices 为用户拖弯/拖移产生的顶点（后端持久化后随快照回放）。 */
function styleEdge(id: string, source: any, target: any, data: any, vertices: any[] = [], colorOverride?: string): any {
  const relation = data?.relation ?? "support";
  const decision = isDecisionWorkspace();
  const decisionColors: Record<string, string> = isLightTheme() ? { support: "#23B894", subordinate: "#369BD2", oppose: "#DF5D94", duplicate: "#D99A26", replace: "#9B7BC8" } : { support: "#24eac0", subordinate: "#19c6ff", oppose: "#ff619c", duplicate: "#edbc61", replace: "#ac83ff" };
  const color = colorOverride ?? (decision || isLightTheme() ? decisionColors[relation] : EDGE_COLORS[relation]) ?? PRIMARY;
  // 仅对非默认关系（subordinate/oppose/duplicate/replace）标注文字，避免满屏 "support" 干扰阅读。
  const labels = !decision && relation === "support" ? [] : [{
    position: 0.5,
    attrs: {
      ...(decision ? { body: { fill: isLightTheme() ? mixColor(color, "#FFFFFF", 0.07) : "#061925", stroke: color, strokeWidth: 0.8, vectorEffect: "non-scaling-stroke", rx: 12, ry: 12, refX: -7, refY: -4, refWidth: "100%", refHeight: "100%", refWidth2: 14, refHeight2: 8 } } : {}),
      label: {
        text: relation === "support" ? "支持" : relation === "replace" && decision ? "取代" : EDGE_LABELS[relation] ?? relation,
        fill: isLightTheme() ? mixColor(color, "#182D48", 0.65) : color,
        fontSize: decision ? 14 : 12,
        fontWeight: 600,
        ...(!decision ? { background: { fill: isLightTheme() ? "#fff" : "#162940", stroke: BORDER, strokeWidth: 0.5, padding: 2 } } : {}),
      },
    },
  }];
  return {
    id,
    shape: "edge",
    source,
    target,
    zIndex: 0,
    labels,
    vertices,
    ...(decision ? { connector: { name: "smooth" } } : {}),
    attrs: {
      line: {
        stroke: color,
        strokeWidth: 1.2,
        vectorEffect: "non-scaling-stroke",
        targetMarker: { name: "block", width: 9, height: 9 },
      },
    },
    data,
  };
}

function endId(end: any): string {
  return typeof end === "string" ? end : (end?.cell ?? "");
}

function rawPos(c: any): { x: number; y: number } {
  return { x: c.x ?? c.position?.x ?? 0, y: c.y ?? c.position?.y ?? 0 };
}

/** 派系分配：根=主蓝；根下每个一级分支依次取一色，子孙继承（边方向统一 父→子，source=上位）。
 * 下钻：当根只有唯一一个「实质分支」（辩题容器 issue，排除「其他」旁支）且其下仍有多个分支时，
 * 容器本身取主蓝、改按它的子分支取色——使对垒会议的两个阵营锚点分到不同颜色（否则同挂容器下会两派同色）。 */
function assignFactions(rawNodes: any[], rawEdges: { src: string; tgt: string }[]): Map<string, string> {
  const nodeIds = new Set(rawNodes.map((n) => n.id));
  const children = new Map<string, string[]>();
  const hasParent = new Set<string>();
  for (const e of rawEdges) {
    if (!nodeIds.has(e.src) || !nodeIds.has(e.tgt) || e.src === e.tgt) continue;
    if (!children.has(e.src)) children.set(e.src, []);
    children.get(e.src)!.push(e.tgt);
    hasParent.add(e.tgt);
  }
  const faction = new Map<string, string>();
  const assignSubtree = (id: string, color: string) => {
    faction.set(id, color);
    for (const k of children.get(id) ?? []) assignSubtree(k, color);
  };
  const isOther = (id: string) => id.startsWith("n_issue_other");
  const roots = rawNodes.map((n) => n.id).filter((id) => !hasParent.has(id));
  for (const r of roots) faction.set(r, PRIMARY);
  let fi = 0;
  for (const r of roots) {
    const kids = children.get(r) ?? [];
    const real = kids.filter((k) => !isOther(k));
    let branches = kids;
    if (real.length === 1 && (children.get(real[0]) ?? []).length >= 2) {
      faction.set(real[0], PRIMARY);
      branches = [...(children.get(real[0]) ?? []), ...kids.filter(isOther)];
    }
    for (const k of branches) {
      if (faction.has(k)) continue;
      assignSubtree(k, FACTION_COLORS[fi % FACTION_COLORS.length]);
      fi++;
    }
  }
  for (const n of rawNodes) if (!faction.has(n.id)) faction.set(n.id, PRIMARY); // 兜底（环/多父孤岛）
  return faction;
}

/** 议题结构思维导图布局：议题居顶，其下观点分支横向分列，每列的论点/论据自上而下逐层展开。
 * 下钻规则与 assignFactions/issues-view.resolveBranches 一致：当根仅有唯一「实质分支」（排除「其他」旁支）
 * 且其下仍有 ≥2 个分支时，该容器作为枢纽居中，改按其子分支分列，使对垒双方成为并列大列。
 * 工作台（decision）为呈现层级一律重算坐标，忽略 position_frozen；其余视图保留人工拖拽坐标。
 * 多根会议无法构成单一思维导图，返回 structured=false 交由调用方回退（泳道/平铺）。 */
function treeLayout(
  rawNodes: any[],
  rawEdges: { src: string; tgt: string }[],
): { pos: Map<string, { x: number; y: number }>; structured: boolean } {
  const nodeById = new Map(rawNodes.map((n) => [n.id, n]));
  const children = new Map<string, string[]>();
  const hasParent = new Set<string>();
  for (const e of rawEdges) {
    if (!nodeById.has(e.src) || !nodeById.has(e.tgt) || e.src === e.tgt) continue;
    if (!children.has(e.src)) children.set(e.src, []);
    children.get(e.src)!.push(e.tgt);
    hasParent.add(e.tgt);
  }
  const roots = rawNodes.map((n) => n.id).filter((id) => !hasParent.has(id));
  const pos = new Map<string, { x: number; y: number }>();
  const placed = new Set<string>();
  const decision = isDecisionWorkspace();
  const keepFrozen = !decision;            // 工作台重算层级，其余视图尊重人工拖拽
  const gap = decision ? 285 : X_GAP;      // 工作台节点宽 245，285 与旧泳道同宽且可被视口 pitch 识别

  /** 列内纵向堆叠：row 为所在行号（0=根行，1=观点行，2+=论据…），返回列内下一个可用行号。 */
  const layoutColumn = (id: string, row: number, colX: number): number => {
    if (placed.has(id)) return row;
    placed.add(id);
    const c = nodeById.get(id)!;
    const kids = (children.get(id) ?? []).filter((k) => !placed.has(k));
    if (keepFrozen && c.data?.edit?.position_frozen) {
      pos.set(id, rawPos(c));
      let next = row + 1;
      for (const k of kids) next = layoutColumn(k, next, colX);
      return next;
    }
    pos.set(id, { x: colX, y: TOP + row * LEVEL_H });
    let next = row + 1;
    for (const k of kids) next = layoutColumn(k, next, colX);
    return next;
  };

  if (roots.length === 1) {
    const rootId = roots[0];
    placed.add(rootId);
    const kids = (children.get(rootId) ?? []).filter((k) => !placed.has(k));
    const isOther = (id: string) => id.startsWith("n_issue_other");
    const real = kids.filter((k) => !isOther(k));
    // 下钻：唯一实质分支且有多个下级 → 该容器居中作枢纽，改按其子分支分列
    let hubId: string | null = null;
    let branchIds = kids;
    if (real.length === 1 && (children.get(real[0]) ?? []).length >= 2) {
      hubId = real[0];
      branchIds = [...(children.get(hubId) ?? []), ...kids.filter(isOther)];
    }
    const startRow = hubId ? 2 : 1;
    let firstX = LEFT, lastX = LEFT;
    branchIds.forEach((b, k) => {
      const colX = LEFT + k * gap;
      if (k === 0) firstX = colX;
      lastX = colX;
      layoutColumn(b, startRow, colX);
    });
    const centerX = branchIds.length ? (firstX + lastX) / 2 : LEFT;
    if (hubId) {
      placed.add(hubId);
      pos.set(hubId, { x: centerX, y: TOP + LEVEL_H });
    }
    pos.set(rootId, { x: centerX, y: TOP });
    // 兜底：游离节点排到末尾新列
    let k = branchIds.length;
    for (const n of rawNodes) {
      if (!placed.has(n.id)) layoutColumn(n.id, startRow, LEFT + (k++) * gap);
    }
    return { pos, structured: true };
  }

  // 多根兜底：每个根视作独立分支横向排（无法构成单一思维导图，交调用方回退）
  roots.forEach((r, k) => layoutColumn(r, 1, LEFT + k * X_GAP));
  let k = roots.length;
  for (const n of rawNodes) {
    if (!placed.has(n.id)) layoutColumn(n.id, 1, LEFT + (k++) * X_GAP);
  }
  return { pos, structured: false };
}

/** 把后端 cells 归一化为 X6 v2 的 {nodes, edges}，套派系着色、树形布局与会议看板主题。 */
function normalize(cells: any[]): { nodes: any[]; edges: any[] } {
  const rawNodes: any[] = [];
  const rawEdges: { id: string; src: string; tgt: string; relation: string; raw: any }[] = [];
  for (const c of cells || []) {
    if (c.shape === "edge") {
      rawEdges.push({
        id: c.id,
        src: endId(c.source),
        tgt: endId(c.target),
        relation: c.data?.relation ?? "support",
        raw: c,
      });
    } else {
      rawNodes.push(c);
    }
  }

  const faction = assignFactions(rawNodes, rawEdges);
  const { pos, structured } = treeLayout(rawNodes, rawEdges.map((e) => ({ src: e.src, tgt: e.tgt })));
  const decision = isDecisionWorkspace();
  // 单根会议走议题结构思维导图；多根无法构成单一层级树 → 工作台回退类型泳道平铺
  if (decision && !structured) {
    const laneRows = [0, 0, 0, 0];
    const lanes: Record<string, number> = { issue: 0, point: 1, evidence: 1, conflict: 1, conclusion: 2, action: 3 };
    for (const node of rawNodes) {
      const lane = lanes[node.data?.type] ?? 1;
      const row = laneRows[lane]++;
      pos.set(node.id, node.data?.edit?.position_frozen ? rawPos(node) : { x: 30 + lane * 285, y: 45 + row * 135 });
    }
  }

  const nodes: any[] = rawNodes.map((c) => {
    const style = nodeStyle(c.data?.type);
    const cmd = c.data?.cmd?.mark as string | undefined;   // 用户指令标记：gray=灰化 / strike=删除线 / move=已移动
    const grayed = cmd === "gray";
    const struck = cmd === "strike";
    const stroke = grayed ? decision ? "#8291a4" : "#b3b3b3" : decision || isLightTheme() ? style.stroke : (faction.get(c.id) ?? style.stroke);
    const p = pos.get(c.id) ?? rawPos(c);
    const high = c.data?.importance?.level === "high";     // 高优节点：更粗描边 + 加粗文字
    const lines = String(c.data?.label ?? "").split("\n");
    const title = lines[0];
    const subtitle = lines.slice(1).join("\n") || NODE_TYPE_LABELS[c.data?.type] || "要点";
    const gradientId = `decision-surface-${String(c.id).replace(/[^\w-]/g, "_")}`;
    return {
      id: c.id,
      shape: "rect",
      markup: [
        ...(decision ? [{ tagName: "defs", children: [{ tagName: "linearGradient", attrs: { id: gradientId, x1: "0%", y1: "0%", x2: "100%", y2: "100%" }, children: [
          { tagName: "stop", attrs: { offset: "0%", "stop-color": style.fill } },
          { tagName: "stop", attrs: { offset: "100%", "stop-color": mixColor(style.fill, "#1C2D43", 0.75) } },
        ] }] }] : []),
        { tagName: "rect", selector: "body" },
        ...(decision ? [{ tagName: "circle", selector: "iconBadge" }, { tagName: "path", selector: "icon" }] : []),
        { tagName: "text", selector: "typeLabel" },
        { tagName: "text", selector: "label" },
      ],
      x: p.x, y: p.y,
      width: decision ? 245 : c.width ?? c.size?.width ?? 220,
      height: decision ? 98 : Math.max(c.height ?? c.size?.height ?? 64, 78),
      zIndex: 10,
      ports: nodePorts(stroke),
      attrs: {
        body: {
          fill: grayed ? isLightTheme() ? "#eceff3" : decision ? "#1a2735" : "#ececec" : decision && !isLightTheme() ? `url(#${gradientId})` : style.fill,
          stroke,
          strokeWidth: high ? 1.4 : 1,
          vectorEffect: "non-scaling-stroke",
          rx: decision ? 13 : 9, ry: decision ? 13 : 9,
        },
        ...(decision ? {
          iconBadge: { cx: 34, cy: 49, r: 21, fill: style.stroke, opacity: c.data?.type === "action" ? 1 : 0, pointerEvents: "none" },
          icon: { d: style.icon, transform: "translate(18,33)", fill: "none", stroke: c.data?.type === "action" ? "#fff" : grayed ? stroke : style.stroke, strokeWidth: c.data?.type === "point" ? 5 : c.data?.type === "action" ? 3 : 2, strokeLinecap: c.data?.type === "point" ? "butt" : "round", strokeLinejoin: "round", pointerEvents: "none" },
        } : {}),
        typeLabel: {
          text: decision ? `${subtitle}${grayed ? "·暂不考虑" : ""}` : grayed ? `${NODE_TYPE_LABELS[c.data?.type] ?? "要点"}·暂不考虑` : (NODE_TYPE_LABELS[c.data?.type] ?? "要点"),
          fill: grayed ? "#9c9c9c" : style.accent,
          fontSize: decision ? 12 : 11,
          fontWeight: decision ? 400 : 700,
          ...(decision ? { textWrap: { width: -78, height: 30, ellipsis: "…" } } : {}),
          refX: decision ? 62 : 15,
          refY: decision ? 72 : 19,
          textAnchor: "start",
          textVerticalAnchor: "middle",
        },
        label: {
          text: decision ? title : c.data?.label ?? "",
          fill: grayed ? "#9c9c9c" : graphText(),
          fontSize: decision ? 17 : 14,
          fontWeight: decision ? 600 : high ? 700 : 500,
          textDecoration: struck ? "line-through" : "none",
          textWrap: { width: decision ? -78 : -20, height: decision ? 40 : -12, ellipsis: "…" },
          refX: decision ? 62 : 15,
          refY: decision ? 34 : 48,
          textAnchor: "start",
          textVerticalAnchor: "middle",
        },
      },
      data: c.data,
    };
  });

  const edges: any[] = rawEdges.map((e) => {
    const source = rawNodes.find((n) => n.id === e.src);
    const sourceColor = decision && e.relation === "support" ? nodeStyle(source?.data?.type).stroke : undefined;
    const start = pos.get(e.src), end = pos.get(e.tgt);
    const endpoint = (raw: any, id: string, from: boolean) => {
      if (!decision || !start || !end || raw?.port) return raw;
      const horizontal = Math.abs(end.x - start.x) > 100;
      const forward = horizontal ? end.x > start.x : end.y > start.y;
      return { ...(typeof raw === "object" ? raw : {}), cell: id, port: horizontal ? (from === forward ? "right" : "left") : (from === forward ? "bottom" : "top") };
    };
    return styleEdge(e.id, endpoint(e.raw.source, e.src, true), endpoint(e.raw.target, e.tgt, false), e.raw.data, e.raw.vertices ?? [], sourceColor);
  });

  return { nodes, edges };
}

/** 空状态显隐：看板无节点时展示引导文案（#board-empty 由 index.html 提供，缺失则跳过）。 */
export function toggleEmptyState(visible: boolean): void {
  const el = document.getElementById("board-empty");
  if (el) el.style.display = visible ? "flex" : "none";
}

/** ===== 本地编辑叠加层 =====
 * 后端 board.update 是全量快照（每个光标事件都会回推一次，见 server._drive），
 * 而用户在画布上的连线/拖拽只存在于前端。若直接 fromJSON 覆盖，
 * 刚连好的「框↔框」导线会在下一次回推时被抹掉。
 * 这里记录本地新增/删除的边、本地移动过的节点坐标，每次应用远端快照时重新叠加。 */
const localAddedEdges = new Map<string, any>();
const localRemovedEdgeIds = new Set<string>();
const localNodePositions = new Map<string, { x: number; y: number }>();
let applyingRemote = false;      // 正在应用远端快照：期间的增删改不计入本地编辑
let lastRemoteFingerprint = "";  // 上一次应用的远端快照指纹，未变则整图不动
let lastSpecs = new Map<string, string>(); // 上一次应用的每个 cell 的规格（增量 patch 比对基准）

/** 监听图内编辑事件，登记本地改动（只在 createGraph 里绑定一次）。 */
function trackLocalEdits(graph: Graph): void {
  graph.on("edge:connected", ({ edge, isNew }: any) => {
    if (applyingRemote || !isNew) return;
    localAddedEdges.set(edge.id, styleEdge(edge.id, edge.getSource(), edge.getTarget(), edge.getData()));
    localRemovedEdgeIds.delete(edge.id);
  });
  graph.on("edge:removed", ({ edge }: any) => {
    if (applyingRemote) return;
    if (localAddedEdges.delete(edge.id)) return;   // 本地新增的又被删掉：撤销登记
    localRemovedEdgeIds.add(edge.id);
  });
  graph.on("node:change:position", ({ node }: any) => {
    if (applyingRemote) return;
    const p = node.getPosition();
    localNodePositions.set(node.id, { x: p.x, y: p.y });
  });
}

/** 重置叠加层（整图重绘 = 以后端为准的新开始）。 */
function resetLocalEdits(): void {
  localAddedEdges.clear();
  localRemovedEdgeIds.clear();
  localNodePositions.clear();
  lastRemoteFingerprint = "";
  lastSpecs.clear();
}

export function collectLocalEdits(): {
  added_edges: Array<{ id: string; source: string; target: string; relation: string }>;
  removed_edge_ids: string[];
  node_positions: Record<string, { x: number; y: number }>;
} {
  return {
    added_edges: Array.from(localAddedEdges.values()).map((edge) => ({
      id: edge.id,
      source: edgeEnd(edge.source) || "",
      target: edgeEnd(edge.target) || "",
      relation: edge.data?.relation || "support",
    })),
    removed_edge_ids: Array.from(localRemovedEdgeIds),
    node_positions: Object.fromEntries(localNodePositions),
  };
}

export function clearLocalEdits(): void {
  resetLocalEdits();
}

function edgeEnd(endpoint: any): string | undefined {
  return typeof endpoint === "string" ? endpoint : endpoint?.cell;
}

/** 把远端 cells 归一化，并叠加本地编辑，产出最终可 fromJSON 的图。 */
function mergeLocalEdits(cells: any[]): { nodes: any[]; edges: any[] } {
  const { nodes, edges } = normalize(cells);
  for (const n of nodes) {
    const p = localNodePositions.get(n.id);
    if (p) { n.x = p.x; n.y = p.y; }
  }
  const nodeIds = new Set(nodes.map((n) => n.id));
  const merged = edges.filter((e) => !localRemovedEdgeIds.has(e.id));
  const present = new Set(merged.map((e) => e.id));
  for (const [id, json] of localAddedEdges) {
    if (present.has(id)) continue;
    // 端点在远端已消失的本地边无法落地，丢弃，避免悬空导线（远端边由后端保证有效，不校验）
    if (!nodeIds.has(edgeEnd(json.source) as string) || !nodeIds.has(edgeEnd(json.target) as string)) continue;
    merged.push(json);
  }
  return { nodes, edges: merged };
}

/** 远端快照指纹：轻量滚动哈希（FNV-1a）。
 * 旧实现 JSON.stringify(cells) 每次推送都对整图做一次全量序列化（O(size) 字符串分配），
 * 高频光标回推时开销可观。这里逐 cell 混入影响渲染的字段，返回定长短串；
 * 长度（cell 数）一并混入，降低不同图同哈希的歧义。 */
function fingerprint(cells: any[]): string {
  const list = cells ?? [];
  let h = 0x811c9dc5;
  const mix = (s: string): void => {
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
  };
  for (const c of list) {
    mix(c.id ?? "");
    mix(c.shape ?? "");
    mix(`${c.x ?? c.position?.x ?? ""},${c.y ?? c.position?.y ?? ""},${c.width ?? c.size?.width ?? ""},${c.height ?? c.size?.height ?? ""}`);
    if (c.source) mix(JSON.stringify(c.source));
    if (c.target) mix(JSON.stringify(c.target));
    if (c.vertices) mix(JSON.stringify(c.vertices));
    if (c.data) mix(JSON.stringify(c.data));
  }
  return (h >>> 0).toString(36) + ":" + list.length;
}

/** 增量应用归一化后的图：按 cell id 比对，仅增删改变化的部分，避免整图重建。
 * 整图 fromJSON 会清空并重放所有 cell，打断正在进行的拖拽/连线并丢失选择态；
 * 增量 patch 只在真正变化时才动对应 cell。受限环境（测试 mock 无 getCells 等）回退整图重建。 */
function patchGraph(graph: Graph, next: { nodes: any[]; edges: any[] }): void {
  const g = graph as any;
  if (
    typeof g.getCells !== "function" ||
    typeof g.addNode !== "function" ||
    typeof g.addEdge !== "function" ||
    typeof g.removeCell !== "function"
  ) {
    graph.fromJSON(next);
    lastSpecs.clear();
    return;
  }

  const specs = new Map<string, string>();
  for (const n of next.nodes) specs.set(n.id, `n:${JSON.stringify(n)}`);
  for (const e of next.edges) specs.set(e.id, `e:${JSON.stringify(e)}`);

  // 1) 删除已消失/内容变更的节点（X6 删节点会联动删其关联边，故节点先于边处理）
  for (const [id, prev] of lastSpecs) {
    const now = specs.get(id);
    if (prev.startsWith("n:") && prev !== now && g.getCellById(id)) g.removeCell(id);
  }
  // 2) 删除已消失/内容变更的边
  for (const [id, prev] of lastSpecs) {
    const now = specs.get(id);
    if (prev.startsWith("e:") && prev !== now && g.getCellById(id)) g.removeCell(id);
  }
  // 3) 新增/重建节点（变更的已在步骤 1 移除，此处 getCellById 为空）
  for (const n of next.nodes) {
    if (lastSpecs.get(n.id) === specs.get(n.id)) continue;
    if (g.getCellById(n.id)) g.removeCell(n.id);
    g.addNode(n);
  }
  // 4) 新增/重建边：节点被重建时其关联边已随节点消失，故「未变但已不在图中」也要补回
  for (const e of next.edges) {
    const exists = !!g.getCellById(e.id);
    if (lastSpecs.get(e.id) === specs.get(e.id) && exists) continue;
    if (exists) g.removeCell(e.id);
    g.addEdge(e);
  }
  lastSpecs = specs;
}

/** 应用一份远端快照（含本地编辑叠加）。返回是否真的应用了变化。 */
function applyRemoteSnapshot(graph: Graph, cells: any[]): boolean {
  const fp = fingerprint(cells);
  if (fp === lastRemoteFingerprint) return false;
  lastRemoteFingerprint = fp;
  const merged = mergeLocalEdits(cells);
  applyingRemote = true;
  try {
    patchGraph(graph, merged);
  } finally {
    applyingRemote = false;
  }
  return true;
}

/** 工作台默认按四个横向节点的宽度显示，不因整图越来越大而不断缩小。 */
function applyWorkspaceDefaultZoom(graph: Graph): void {
  if (getUserZoomFactor() !== null) return;
  const nodes = graph.getNodes();
  if (!nodes.length) return;                       // 空图（含单测 mock）无需定默认视口
  const boxes = nodes.map((node) => node.getBBox());
  const nodeWidth = Math.max(245, ...boxes.map((box) => box.width));
  let pitch = Infinity;
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      const step = Math.abs(a.x - b.x);
      if (step >= nodeWidth && Math.abs(a.y - b.y) < Math.min(a.height, b.height) / 2) {
        pitch = Math.min(pitch, step);
      }
    }
  }
  if (!Number.isFinite(pitch)) pitch = nodeWidth + 40;
  const rowWidth = nodeWidth + 3 * pitch;
  const availableWidth = graph.container.clientWidth - 32;
  if (availableWidth <= 0) return;
  beginAutoAdjust();
  try {
    graph.zoom(availableWidth / rowWidth, { absolute: true });
  } finally {
    endAutoAdjust();
  }
}

/** 首次出现内容时设置默认视口；后续推送不重置用户缩放。 */
let initialFocusDone = false;

function focusInitialIssue(graph: Graph, cells: any[]): void {
  if (initialFocusDone || !cells.some((cell) => cell.shape !== "edge")) return;
  initialFocusDone = true;
  if (isDecisionWorkspace()) {
    applyWorkspaceDefaultZoom(graph);
    const root = graph.getCellById("n_issue_root") ?? graph.getNodes()[0];
    if (root) graph.centerCell(root);
    return;
  }
  if (graph.container.clientWidth > 850 && !isDecisionWorkspace()) return;
  composeAutoFit(graph, { padding: 16, maxScale: 1 });   // 用户手动缩放过则按系数组合，不覆盖
}

export function createGraph(container: HTMLElement, readOnly = false): Graph {
  const graph = new Graph({
    container,
    background: { color: isDecisionWorkspace() ? "transparent" : graphBackground() },
    grid: { visible: !isDecisionWorkspace(), type: "dot", size: 18, args: { color: isLightTheme() ? "#cfdaea" : "#29415f", thickness: 1 } },
    interacting: readOnly ? false : { nodeMovable: true }, // 历史快照仅查看，不产生编辑事件
    connecting: {
      allowBlank: false,                    // 不允许多余的悬空连线
      allowLoop: false,                     // 禁止自环
      allowEdge: false,                     // 不允许连到边上
      allowMulti: false,                    // 同一对节点只保留一条边，避免重复连线
      snap: true,
      highlight: true,                      // 拖线时高亮可连接的锚点
      router: "normal",
      connector: { name: "smooth" },        // 柔和弧线（贝塞尔过渡，拖弯顶点同样平滑）
      // 手动新建的边沿用与后端同构的样式/数据，便于与渲染层一致
      createEdge(this: Graph) {
        return this.createEdge({
          shape: "edge",
          zIndex: 0,
          attrs: {
            line: {
              stroke: PRIMARY,
              strokeWidth: 1.2,
        vectorEffect: "non-scaling-stroke",
              targetMarker: { name: "block", width: 9, height: 9 },
            },
          },
          data: { relation: "support" },
        });
      },
    },
    panning: true,
    // 直接滚轮即缩放（以鼠标位置为中心），无需按住 Ctrl。
    mousewheel: { enabled: true },
  });
  // X6 构造时按容器当前尺寸写死内联 width/height；首帧为 0 时会锁死 0×0（画布不可见）。
  // 清掉内联让 flex 接管，并随容器尺寸变化同步画布（ResizeObserver，写入值收敛无死循环）。
  const syncSize = () => {
    container.style.width = "";
    container.style.height = "";
    const w = container.clientWidth;
    const h = container.clientHeight;
    if (w > 0 && h > 0) {
      graph.resize(w, h);
      if (isDecisionWorkspace() && initialFocusDone) applyWorkspaceDefaultZoom(graph);
    }
  };
  syncSize();
  new ResizeObserver(syncSize).observe(container);
  // 只在此处绑定一次的图内编辑监听（renderBoard/applyBoardUpdate 不得再绑定）。
  trackLocalEdits(graph);
  // 只更新外观，保留节点位置、编辑内容、选择与当前缩放。
  const updateTheme = () => {
    graph.drawBackground({ color: isDecisionWorkspace() ? "transparent" : graphBackground() });
    graph.drawGrid({ type: "dot", args: { color: isLightTheme() ? "#cfdaea" : "#29415f", thickness: 1 } });
    const factions = assignFactions(graph.getNodes().map(node => ({ id: node.id })), graph.getEdges().map(edge => ({ src: endId(edge.getSource()), tgt: endId(edge.getTarget()) })));
    for (const node of graph.getNodes()) {
      const data = node.getData();
      const style = nodeStyle(data?.type);
      const gray = data?.cmd?.mark === "gray";
      node.attr("body/fill", gray ? isLightTheme() ? "#eceff3" : "#1a2735" : style.fill);
      node.attr("body/stroke", gray ? "#8291a4" : isDecisionWorkspace() || isLightTheme() ? style.stroke : (factions.get(node.id) ?? style.stroke));
      node.attr("label/fill", gray ? "#9c9c9c" : graphText());
      node.attr("typeLabel/fill", gray ? "#9c9c9c" : style.accent);
      if (isDecisionWorkspace()) {
        node.attr("icon/stroke", data?.type === "action" ? "#fff" : gray ? "#8291a4" : style.stroke);
        node.attr("iconBadge/fill", style.stroke);
      }
      for (const group of ["left", "right", "top", "bottom"]) {
        node.prop(`ports/groups/${group}/attrs/circle/fill`, graphBackground());
        node.prop(`ports/groups/${group}/attrs/circle/stroke`, style.stroke);
      }
    }
    for (const edge of graph.getEdges()) {
      const source = edge.getSourceCell()?.getData();
      const sourceColor = isDecisionWorkspace() && edge.getData()?.relation === "support" ? nodeStyle(source?.type).stroke : undefined;
      const styled = styleEdge(edge.id, edge.getSource(), edge.getTarget(), edge.getData(), edge.getVertices(), sourceColor);
      edge.attr(styled.attrs);
      edge.setLabels(styled.labels);
    }
  };
  window.addEventListener("huimai-theme-change", updateTheme);
  graph.on("disposed", () => window.removeEventListener("huimai-theme-change", updateTheme));
  return graph;
}

/** 全量渲染（GET /api/board 返回后）。整图重绘视为新的开始。 */
export function renderBoard(graph: Graph, cells: any[]): void {
  initialFocusDone = false;
  resetLocalEdits();
  applyRemoteSnapshot(graph, cells);
  toggleEmptyState(!(cells || []).some((c) => c.shape !== "edge"));
  focusInitialIssue(graph, cells);
}

/** 增量应用（WS board.update）。仅远端快照变化时才重建整图，保留用户的本地编辑与视角；
 * fit=true 时缩放至全图可见（树形布局较宽，首载与历史打开需全貌；WS 增量不打断用户当前视口）。 */
export function applyBoardUpdate(graph: Graph, cells: any[], fit = false): void {
  applyRemoteSnapshot(graph, cells);
  toggleEmptyState(!(cells || []).some((c) => c.shape !== "edge"));
  focusInitialIssue(graph, cells);
  if (fit && (cells || []).some((c) => c.shape !== "edge")) {
    if (isDecisionWorkspace()) applyWorkspaceDefaultZoom(graph);
    else composeAutoFit(graph, { padding: 40, maxScale: 1 });
  }
}

/** 居中并定位到某节点（AI 批次推送后契合最近发言 / 深链 #focus）。 */
export function focusNode(graph: Graph, nodeId: string): boolean {
  const cell = graph.getCellById(nodeId);
  if (!cell) return false;
  graph.centerCell(cell, { padding: 120 });
  return true;
}
