/** 议题结构视图：把一次会议的看板 cells 组织成「议题 → 观点 → 主题 → 要点」四级框架，
 * 复刻「远程办公与固定坐班-议题结构」参考页的层级化视觉语言。
 *
 * 纯渲染模块（不触碰页面级 DOM）：历史议题结构页（issues.ts）与看板内
 * 「议题结构」视图（main.ts）共用同一份 renderIssueStructure。
 *
 * 数据来源与看板/history 一致：board cells 的节点类型与边方向（父→子）推导层级，
 * 不新增后端字段：
 *   - 议题（无父节点）→ 根卡片
 *   - 议题下的一级分支 → 观点分支（按索引取参考页蓝/粉/青/橙/紫/绿色板）
 *   - 观点分支下仍有下级的节点 → 主题块；其叶子按类型聚为「要点 / 论据 / 结论…」块
 *   - 更深层级递归为嵌套要点列表
 * 容器「下钻」规则与 board/render.ts 的 assignFactions 一致：当议题仅有唯一实质分支
 *   （排除「其他」旁支）且该分支自身还有多个下级时，改按该分支的子节点划分观点。 */

const TYPE_LABELS: Record<string, string> = {
  issue: "议题", point: "要点", evidence: "论据",
  conclusion: "结论", action: "待办", conflict: "不同意见",
};

/** 观点分支色板（对应参考页的正/反方配色，扩展到多分支）。 */
const BRANCH_COLORS = [
  { base: "#4c8dff", soft: "rgba(76,141,255,.12)", line: "rgba(76,141,255,.38)" },
  { base: "#ff6b9a", soft: "rgba(255,107,154,.12)", line: "rgba(255,107,154,.38)" },
  { base: "#13c2c2", soft: "rgba(19,194,194,.12)", line: "rgba(19,194,194,.38)" },
  { base: "#fa8c16", soft: "rgba(250,140,22,.12)", line: "rgba(250,140,22,.38)" },
  { base: "#722ed1", soft: "rgba(114,46,209,.16)", line: "rgba(114,46,209,.42)" },
  { base: "#52c41a", soft: "rgba(82,196,26,.12)", line: "rgba(82,196,26,.38)" },
];

const ISSUE_ACCENT = "#f0b429";

interface StructureNode {
  id: string;
  type: string;
  title: string;
  subtitle: string;
  data: any;
}

interface StructureIndex {
  nodes: StructureNode[];
  nodeById: Map<string, StructureNode>;
  children: Map<string, string[]>;
  roots: string[];
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function typeLabel(type: string): string {
  return TYPE_LABELS[type] ?? type;
}

function endId(end: any): string {
  return typeof end === "string" ? end : (end?.cell ?? "");
}

/** 节点 label 首行为标题、其余行为副标题（与看板 render.ts 的取法一致）。 */
function labelParts(cell: any): { title: string; subtitle: string } {
  const lines = String(cell?.data?.label ?? "").split("\n").map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return { title: "未命名", subtitle: "" };
  return { title: lines[0], subtitle: lines.slice(1).join(" · ") };
}

function buildIndex(cells: any[]): StructureIndex {
  const nodes: StructureNode[] = [];
  const nodeById = new Map<string, StructureNode>();
  for (const cell of cells || []) {
    if (cell.shape === "edge") continue;
    const { title, subtitle } = labelParts(cell);
    const node: StructureNode = {
      id: cell.id, type: cell.data?.type ?? "point", title, subtitle, data: cell.data ?? {},
    };
    nodes.push(node);
    nodeById.set(node.id, node);
  }
  const children = new Map<string, string[]>();
  const hasParent = new Set<string>();
  for (const cell of cells || []) {
    if (cell.shape !== "edge") continue;
    const src = endId(cell.source);
    const tgt = endId(cell.target);
    if (!nodeById.has(src) || !nodeById.has(tgt) || src === tgt) continue;
    if (!children.has(src)) children.set(src, []);
    children.get(src)!.push(tgt);
    hasParent.add(tgt);
  }
  const roots = nodes.map((node) => node.id).filter((id) => !hasParent.has(id));
  return { nodes, nodeById, children, roots };
}

const isOther = (id: string) => id.startsWith("n_issue_other");

/** 观点分支解析：兼容「议题 → 观点」直接相连与「议题 → 容器 → 观点」两种形态。
 * 后者沿用看板派系分配的「下钻」规则（唯一实质子节点且其下仍有多个分支）。 */
function resolveBranches(rootId: string, index: StructureIndex): { containerId: string | null; branchIds: string[] } {
  const kids = index.children.get(rootId) ?? [];
  const real = kids.filter((id) => !isOther(id));
  if (real.length === 1 && (index.children.get(real[0]) ?? []).length >= 2) {
    return {
      containerId: real[0],
      branchIds: [...(index.children.get(real[0]) ?? []), ...kids.filter(isOther)],
    };
  }
  return { containerId: null, branchIds: kids };
}

function legendItem(color: string, text: string): HTMLElement {
  const span = el("span");
  const swatch = el("i");
  swatch.style.background = color;
  const label = el("span");
  label.textContent = text;
  span.append(swatch, label);
  return span;
}

function renderLegend(index: StructureIndex): HTMLElement {
  const legend = el("div", "legend");
  legend.append(legendItem(ISSUE_ACCENT, "层级 1 · 议题"));
  legend.append(legendItem(BRANCH_COLORS[0].base, "层级 2 · 观点分支"));
  legend.append(legendItem("#7f93ad", "层级 3/4 · 主题 → 要点"));
  const counts = new Map<string, number>();
  for (const node of index.nodes) counts.set(node.type, (counts.get(node.type) ?? 0) + 1);
  const stats = el("span");
  stats.textContent = [...counts.entries()].map(([type, count]) => `${typeLabel(type)} ${count}`).join(" · ");
  legend.append(stats);
  return legend;
}

function appendBadge(parent: HTMLElement, type: string, className: string): void {
  const badge = el("span", className);
  badge.dataset.type = type;
  badge.textContent = typeLabel(type);
  parent.append(badge);
}

function renderRootCard(node: StructureNode): HTMLElement {
  const card = el("div", "root");
  const tag = el("span", "tag");
  tag.textContent = `${typeLabel(node.type)} · ${node.type.toUpperCase()}`;
  const heading = el("h1");
  heading.textContent = node.title;
  card.append(tag, heading);
  if (node.subtitle) {
    const thesis = el("p");
    thesis.textContent = node.subtitle;
    card.append(thesis);
  }
  return card;
}

/** 递归要点：叶子节点渲染为 li，若仍有下级则内嵌一层 ul.points（层级 4+）。 */
function renderPoint(node: StructureNode, index: StructureIndex, visited: Set<string>): HTMLLIElement {
  const item = el("li");
  const key = el("span", "k");
  key.textContent = node.title;
  item.append(key);
  if (node.subtitle) {
    const sub = el("span", "sub");
    sub.textContent = node.subtitle;
    item.append(sub);
  }
  appendBadge(item, node.type, "src");
  const kids = (index.children.get(node.id) ?? []).filter((id) => !visited.has(id));
  if (kids.length) {
    const nested = el("ul", "points");
    for (const id of kids) {
      visited.add(id);
      nested.append(renderPoint(index.nodeById.get(id)!, index, visited));
    }
    item.append(nested);
  }
  return item;
}

/** 主题块（有下级的节点）：标题 + 序号 + 类型角标，其子节点逐条展开。 */
function renderTopicBlock(node: StructureNode, order: number, index: StructureIndex, visited: Set<string>): HTMLElement {
  const block = el("div", "block");
  const head = el("div", "block-title");
  const idx = el("span", "idx");
  idx.textContent = String(order);
  const label = el("span", "block-label");
  label.textContent = node.title;
  head.append(idx, label);
  appendBadge(head, node.type, "badge");
  block.append(head);
  if (node.subtitle) {
    const thesis = el("p", "block-thesis");
    thesis.textContent = node.subtitle;
    block.append(thesis);
  }
  const points = el("ul", "points");
  const kids = (index.children.get(node.id) ?? []).filter((id) => !visited.has(id));
  for (const id of kids) {
    visited.add(id);
    points.append(renderPoint(index.nodeById.get(id)!, index, visited));
  }
  block.append(points);
  return block;
}

/** 观点分支中的叶子：按节点类型聚合成「要点 / 论据 / 结论…」块。 */
function renderLeafGroup(type: string, ids: string[], order: number, index: StructureIndex, visited: Set<string>): HTMLElement {
  const block = el("div", "block");
  const head = el("div", "block-title");
  const idx = el("span", "idx");
  idx.textContent = String(order);
  const label = el("span", "block-label");
  label.textContent = typeLabel(type);
  head.append(idx, label);
  appendBadge(head, type, "badge");
  block.append(head);
  const points = el("ul", "points");
  for (const id of ids) {
    visited.add(id);
    points.append(renderPoint(index.nodeById.get(id)!, index, visited));
  }
  block.append(points);
  return block;
}

function renderBranch(node: StructureNode, order: number, index: StructureIndex, visited: Set<string>): HTMLElement {
  const branch = el("section", "branch");
  const color = BRANCH_COLORS[order % BRANCH_COLORS.length];
  branch.style.setProperty("--branch", color.base);
  branch.style.setProperty("--branch-soft", color.soft);
  branch.style.setProperty("--branch-line", color.line);

  const head = el("div", "branch-head");
  const side = el("div", "side");
  side.textContent = `观点 ${order + 1} · ${typeLabel(node.type).toUpperCase()}`;
  const heading = el("h2");
  heading.textContent = node.title;
  head.append(side, heading);
  if (node.subtitle) {
    const thesis = el("div", "thesis");
    thesis.textContent = node.subtitle;
    head.append(thesis);
  }
  branch.append(head);

  const blocks = el("div", "blocks");
  const kids = (index.children.get(node.id) ?? []).filter((id) => !visited.has(id));
  const hasDownstream = (id: string) => (index.children.get(id) ?? []).some((child) => !visited.has(child));
  const topics = kids.filter(hasDownstream);
  const leaves = kids.filter((id) => !hasDownstream(id));

  let order2 = 1;
  for (const id of topics) {
    visited.add(id);
    blocks.append(renderTopicBlock(index.nodeById.get(id)!, order2++, index, visited));
  }
  const groups = new Map<string, string[]>();
  for (const id of leaves) {
    const type = index.nodeById.get(id)!.type;
    if (!groups.has(type)) groups.set(type, []);
    groups.get(type)!.push(id);
  }
  for (const [type, ids] of groups) {
    blocks.append(renderLeafGroup(type, ids, order2++, index, visited));
  }
  if (!kids.length) {
    const note = el("p", "branch-empty");
    note.textContent = "该观点下暂无细分要点。";
    blocks.append(note);
  }
  branch.append(blocks);
  return branch;
}

/** 把 board cells 渲染成议题结构层级到 container（会先清空 container）。
 * 看板内视图与历史议题结构页共用此函数。 */
export function renderIssueStructure(container: HTMLElement, cells: any[]): void {
  container.replaceChildren();
  const index = buildIndex(cells);
  if (!index.nodes.length) {
    const note = el("p", "issues-empty");
    note.textContent = "该会议还没有可组织的议题内容。输入发言或补充要点后，这里会生成结构框架。";
    container.append(note);
    return;
  }
  container.append(renderLegend(index));

  const rootIds = index.roots.length ? index.roots : index.nodes.map((node) => node.id);
  const visited = new Set<string>();
  for (const rootId of rootIds) {
    if (visited.has(rootId)) continue;
    const root = index.nodeById.get(rootId);
    if (!root) continue;
    visited.add(rootId);
    container.append(renderRootCard(root));

    const { containerId, branchIds } = resolveBranches(rootId, index);
    if (containerId) visited.add(containerId);
    const branches = branchIds.filter((id) => !visited.has(id));
    if (branches.length) {
      const wrap = el("div", "branches");
      branches.forEach((id, order) => {
        visited.add(id);
        wrap.append(renderBranch(index.nodeById.get(id)!, order, index, visited));
      });
      container.append(wrap);
    }
  }
}
