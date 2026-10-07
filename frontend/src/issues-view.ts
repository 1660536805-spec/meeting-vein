import { projectBoard } from "./board/projection";

/** 议题结构视图：把一次会议的看板 cells 组织成「议题 → 观点 → 主题 → 要点」四级框架，
 * 复刻「远程办公与固定坐班-议题结构」参考页的层级化视觉语言。
 *
 * 纯渲染模块（不触碰页面级 DOM）：历史议题结构页（issues.ts）与看板内
 * 「议题结构」视图（main.ts）共用同一份 renderIssueStructure。
 *
 * 数据来源与看板/history 一致：通过共享投影读取 v2 parent_id 或 v1 subordinate 父子关系：
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

/** 节点 label 首行为标题、其余行为副标题（与看板 render.ts 的取法一致）。 */
function labelParts(cell: any): { title: string; subtitle: string } {
  const lines = String(cell?.data?.label ?? "").split("\n").map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return { title: "未命名", subtitle: "" };
  return { title: lines[0], subtitle: lines.slice(1).join(" · ") };
}

function buildIndex(cells: any[]): StructureIndex {
  const projection = projectBoard(cells);
  const nodes: StructureNode[] = [];
  const nodeById = new Map<string, StructureNode>();
  for (const projected of projection.nodes) {
    const cell = projected.cell;
    const { title, subtitle } = labelParts(cell);
    const node: StructureNode = {
      id: cell.id, type: cell.data?.type ?? "point", title, subtitle, data: cell.data ?? {},
    };
    nodes.push(node);
    nodeById.set(node.id, node);
  }
  return { nodes, nodeById, children: projection.children, roots: projection.roots };
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

function descendantCount(id: string, index: StructureIndex, visited = new Set<string>()): number {
  if (visited.has(id)) return 0;
  visited.add(id);
  return (index.children.get(id) ?? []).reduce((total, child) => total + 1 + descendantCount(child, index, visited), 0);
}

function renderBranch(
  node: StructureNode,
  order: number,
  index: StructureIndex,
  visited: Set<string>,
  onViewInGraph?: (nodeId: string) => void,
): HTMLElement {
  const branch = el("section", "branch");
  branch.dataset.nodeId = node.id;
  const color = BRANCH_COLORS[order % BRANCH_COLORS.length];
  branch.style.setProperty("--branch", color.base);
  branch.style.setProperty("--branch-soft", color.soft);
  branch.style.setProperty("--branch-line", color.line);

  const head = el("div", "branch-head");
  const side = el("div", "side");
  side.textContent = `观点 ${order + 1} · ${typeLabel(node.type).toUpperCase()}`;
  const heading = el("h2");
  const headingButton = el("button", "branch-path-button");
  headingButton.type = "button";
  headingButton.textContent = node.title;
  headingButton.addEventListener("click", () => {
    const host = branch.closest(".issue-structure");
    const breadcrumb = host?.querySelector<HTMLElement>(".issue-breadcrumb");
    if (breadcrumb) {
      host?.querySelectorAll<HTMLElement>(".branch").forEach((item) => { item.dataset.current = "false"; });
      branch.dataset.current = "true";
      breadcrumb.textContent = `${index.nodeById.get(index.roots[0])?.title ?? "议题"} / ${node.title}`;
    }
  });
  heading.append(headingButton);
  head.append(side, heading);
  if (onViewInGraph) {
    const view = el("button", "branch-view-button");
    view.type = "button";
    view.textContent = "在图中查看";
    view.addEventListener("click", () => onViewInGraph(node.id));
    head.append(view);
  }
  if (node.subtitle) {
    const thesis = el("div", "thesis");
    thesis.textContent = node.subtitle;
    head.append(thesis);
  }
  branch.append(head);

  const blocks = el("div", "blocks");
  const collapse = el("button", "branch-collapse-button");
  collapse.type = "button";
  const defaultCollapsed = descendantCount(node.id, index) >= 8;
  const setExpanded = (expanded: boolean) => {
    blocks.hidden = !expanded;
    collapse.setAttribute("aria-expanded", String(expanded));
    collapse.textContent = expanded ? "收起分支" : "展开分支";
  };
  collapse.addEventListener("click", () => setExpanded(blocks.hidden));
  head.append(collapse);
  setExpanded(!defaultCollapsed);
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
  branch.dataset.defaultCollapsed = String(defaultCollapsed);
  return branch;
}

/** 把 board cells 渲染成议题结构层级到 container（会先清空 container）。
 * 看板内视图与历史议题结构页共用此函数。 */
export function renderIssueStructure(
  container: HTMLElement,
  cells: any[],
  options: { onViewInGraph?: (nodeId: string) => void } = {},
): void {
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
  const primaryRoot = index.nodeById.get(rootIds[0]);
  const confirmed = index.nodes.filter((node) => node.type === "conclusion" &&
    (node.data.status === "confirmed" || node.data.resolved === true));
  const summary = el("section", "meeting-summary");
  summary.setAttribute("aria-label", "会议摘要");
  const summaryTitle = el("strong");
  summaryTitle.textContent = primaryRoot?.title ?? "会议摘要";
  const summaryText = el("p");
  summaryText.textContent = `已确认 ${confirmed.length} 项结论 · 尚未解决 ${projectBoard(cells).unresolved.length} 项 · 下一步 ${index.nodes.filter((node) => node.type === "action" && !["done", "completed", "closed"].includes(String(node.data.status ?? "").toLowerCase())).length} 项`;
  summary.append(summaryTitle, summaryText);
  container.append(summary);

  const confirmedSection = el("section", "confirmed-conclusions");
  const confirmedHeading = el("h2");
  confirmedHeading.textContent = `已确认结论 · ${confirmed.length}`;
  confirmedSection.append(confirmedHeading);
  if (confirmed.length) {
    const list = el("ul", "confirmed-list");
    for (const node of confirmed) {
      const item = el("li");
      const text = el("span");
      text.textContent = node.title;
      item.append(text);
      if (options.onViewInGraph) {
        const view = el("button");
        view.type = "button";
        view.textContent = "在图中查看";
        view.dataset.viewNode = node.id;
        view.addEventListener("click", () => options.onViewInGraph?.(node.id));
        item.append(view);
      }
      list.append(item);
    }
    confirmedSection.append(list);
  } else {
    const empty = el("p");
    empty.textContent = "确认后的结论会汇总在这里。";
    confirmedSection.append(empty);
  }
  container.append(confirmedSection);

  const controls = el("div", "issue-structure-controls");
  const search = el("input");
  search.type = "search";
  search.setAttribute("aria-label", "搜索议题结构");
  search.placeholder = "搜索议题、观点、结论或待办";
  const breadcrumb = el("nav", "issue-breadcrumb");
  breadcrumb.setAttribute("aria-label", "议题路径");
  breadcrumb.textContent = primaryRoot?.title ?? "议题";
  const expandCurrent = el("button");
  expandCurrent.type = "button";
  expandCurrent.textContent = "展开当前分支";
  controls.append(search, breadcrumb, expandCurrent);
  container.append(controls);

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
        wrap.append(renderBranch(index.nodeById.get(id)!, order, index, visited, options.onViewInGraph));
      });
      container.append(wrap);
    }
  }
  const branches = [...container.querySelectorAll<HTMLElement>(".branch")];
  const setExpanded = (branch: HTMLElement, expanded: boolean) => {
    const blocks = branch.querySelector<HTMLElement>(".blocks");
    const button = branch.querySelector<HTMLButtonElement>(".branch-collapse-button");
    if (!blocks || !button) return;
    blocks.hidden = !expanded;
    button.setAttribute("aria-expanded", String(expanded));
    button.textContent = expanded ? "收起分支" : "展开分支";
  };
  search.addEventListener("input", () => {
    const query = search.value.trim().toLocaleLowerCase();
    let matches = 0;
    for (const branch of branches) {
      const match = !query || branch.textContent?.toLocaleLowerCase().includes(query) === true;
      branch.hidden = !match;
      if (match) {
        matches += 1;
        if (query) setExpanded(branch, true);
        else setExpanded(branch, branch.dataset.defaultCollapsed !== "true");
      }
    }
    breadcrumb.textContent = query ? `${primaryRoot?.title ?? "议题"} / 搜索：${search.value.trim()}（${matches} 个分支）`
      : primaryRoot?.title ?? "议题";
  });
  expandCurrent.addEventListener("click", () => {
    const current = branches.find((branch) => branch.dataset.current === "true" && !branch.hidden)
      ?? branches.find((branch) => !branch.hidden && branch.querySelector<HTMLElement>(".blocks")?.hidden);
    if (current) setExpanded(current, true);
    else branches.filter((branch) => !branch.hidden).forEach((branch) => setExpanded(branch, true));
  });
}
