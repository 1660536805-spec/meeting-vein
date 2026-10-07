import { projectBoard } from "./projection";

/** 查看态图遍历（Design_FrontendBoard §1.4）：reach 上下游 / route 最短路径 / lens 语义透镜。
 * 纯前端本地图算法——`fromJSON` 后由 cells 构建邻接表，不触发后端。
 * 深链格式：#focus=<id> / #reach=<id> / #route=<a>~<b> / #lens=<relation> */

export interface EdgeRef {
  edgeId: string;
  relation: string;      // ∈ EDGE_RELATIONS
  source: string;
  target: string;
  strength: number;
  transitive: boolean;   // 该边是否支持多跳递归展开（Design_InputProcessing §3.3）
}

export interface Adjacency {
  out: Map<string, EdgeRef[]>;   // source → 出边
  in: Map<string, EdgeRef[]>;    // target → 入边
  edges: any[];                  // 全部边 cell（lens 显隐用）
}

export interface RoutePath { nodes: string[]; edges: EdgeRef[]; }

export interface DeepLink {
  focus?: string;
  reach?: string;
  route?: [string, string];
  lens?: string;
}

function push(map: Map<string, EdgeRef[]>, key: string, ref: EdgeRef): void {
  const arr = map.get(key);
  if (arr) arr.push(ref); else map.set(key, [ref]);
}

/** 由 cells 构建邻接表（节点间关系网，Design_InputProcessing §3.3）。 */
export function buildAdjacency(cells: any[]): Adjacency {
  const out = new Map<string, EdgeRef[]>();
  const inn = new Map<string, EdgeRef[]>();
  const edges: any[] = [];
  for (const edge of projectBoard(cells).semanticEdges) {
    const { source, target } = edge;
    const ref: EdgeRef = {
      edgeId: edge.id,
      relation: edge.relation,
      source, target,
      strength: edge.cell.data?.strength ?? 0.5,
      transitive: edge.cell.data?.transitive ?? false,
    };
    push(out, source, ref);
    push(inn, target, ref);
  }
  for (const c of cells || []) if (c.shape === "edge") edges.push(c);
  return { out, in: inn, edges };
}

/** reach：选中节点 → 上游支撑（入边）+ 下游引用（出边）的全部关联节点。
 * 沿 transitive=true 的边递归展开多跳（其余仅一跳跃迁）。 */
export function reach(adj: Adjacency, nodeId: string): Set<string> {
  const found = new Set<string>();
  const walk = (id: string): void => {
    const neighbors: Array<[string, EdgeRef]> = [
      ...(adj.out.get(id) ?? []).map((e): [string, EdgeRef] => [e.target, e]),
      ...(adj.in.get(id) ?? []).map((e): [string, EdgeRef] => [e.source, e]),
    ];
    for (const [n, e] of neighbors) {
      if (n === nodeId || found.has(n)) continue;
      found.add(n);
      if (e.transitive) walk(n);
    }
  };
  walk(nodeId);
  return found;
}

/** route：两节点间最短关系路径（无向 BFS）。返回途经节点与逐段边（可用 edge.relation 标注语义）。 */
export function route(adj: Adjacency, from: string, to: string): RoutePath | null {
  if (from === to) return { nodes: [from], edges: [] };
  const prev = new Map<string, { node: string; edge: EdgeRef }>();
  const visited = new Set<string>([from]);
  const queue: string[] = [from];
  while (queue.length) {
    const cur = queue.shift() as string;
    const outgoing = adj.out.get(cur) ?? [];
    const incoming = (adj.in.get(cur) ?? []).map(
      (e): EdgeRef => ({ ...e, source: e.target, target: e.source }),   // 反向遍历入边
    );
    for (const e of [...outgoing, ...incoming]) {
      const next = e.target;
      if (visited.has(next)) continue;
      visited.add(next);
      prev.set(next, { node: cur, edge: e });
      if (next === to) return reconstruct(prev, from, to);
      queue.push(next);
    }
  }
  return null;
}

function reconstruct(prev: Map<string, { node: string; edge: EdgeRef }>, from: string, to: string): RoutePath {
  const nodes: string[] = [to];
  const edges: EdgeRef[] = [];
  let cur = to;
  while (cur !== from) {
    const step = prev.get(cur);
    if (!step) break;
    edges.unshift(step.edge);
    nodes.unshift(step.node);
    cur = step.node;
  }
  return { nodes, edges };
}

/** lens：按 relation 类型返回应可见的边 id 集合（relation 为空 / "all" 表示全部可见）。 */
export function lens(adj: Adjacency, relation?: string | null): Set<string> {
  const visible = new Set<string>();
  for (const c of adj.edges) {
    const rel = c.data?.relation ?? "support";
    if (!relation || relation === "all" || rel === relation) visible.add(c.id);
  }
  return visible;
}

/** 解析深链（#focus=/#reach=/#route=a~b/#lens=）。 */
export function parseHash(hash: string = location.hash): DeepLink {
  const link: DeepLink = {};
  const raw = hash.replace(/^#/, "");
  if (!raw) return link;
  for (const part of raw.split("&")) {
    const idx = part.indexOf("=");
    if (idx < 0) continue;
    const key = part.slice(0, idx);
    const value = decodeURIComponent(part.slice(idx + 1));
    if (!value) continue;
    if (key === "focus") link.focus = value;
    else if (key === "reach") link.reach = value;
    else if (key === "lens") link.lens = value;
    else if (key === "route") {
      const [a, b] = value.split("~");
      if (a && b) link.route = [a, b];
    }
  }
  return link;
}

/** 由 DeepLink 组装 hash（供分享/复制当前视图链接）。 */
export function buildHash(link: DeepLink): string {
  const parts: string[] = [];
  if (link.focus) parts.push(`focus=${encodeURIComponent(link.focus)}`);
  if (link.reach) parts.push(`reach=${encodeURIComponent(link.reach)}`);
  if (link.route) parts.push(`route=${encodeURIComponent(`${link.route[0]}~${link.route[1]}`)}`);
  if (link.lens) parts.push(`lens=${encodeURIComponent(link.lens)}`);
  return parts.length ? `#${parts.join("&")}` : "";
}
