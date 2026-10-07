/** Shared read model for board hierarchy, semantic relationships, evidence and insights. */
export interface ProjectedNode {
  id: string;
  data: Record<string, any>;
  cell: any;
}

export interface ProjectedEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  cell: any;
}

export interface UnresolvedItem {
  id: string;
  kind: "dispute" | "confirmation";
  nodeIds: string[];
  label: string;
  source: "conflict-node" | "oppose-edge" | "conclusion";
}

export interface BoardProjection {
  nodes: ProjectedNode[];
  nodeById: Map<string, ProjectedNode>;
  structuralEdges: Array<{ src: string; tgt: string }>;
  semanticEdges: ProjectedEdge[];
  semanticEdgesByNode: Map<string, ProjectedEdge[]>;
  children: Map<string, string[]>;
  parentById: Map<string, string>;
  roots: string[];
  evidenceById: Map<string, string[]>;
  unresolved: UnresolvedItem[];
  conclusions: ProjectedNode[];
  actions: ProjectedNode[];
  focus: ProjectedNode | null;
  formatEvidenceTime: (record: { start_offset_ms?: number | null }) => string;
}

const endId = (end: any): string => typeof end === "string" ? end : String(end?.cell ?? "");
const closed = (data: Record<string, any>) => data.resolved === true || ["closed", "done", "completed", "confirmed", "superseded"].includes(String(data.status ?? "").toLowerCase());
const timestamp = (node: ProjectedNode): number => {
  const raw = node.data.updated_at ?? node.data.edit?.updated_at ?? node.data.updated_at_ms ?? 0;
  if (typeof raw === "number") return raw;
  const parsed = Date.parse(String(raw));
  return Number.isFinite(parsed) ? parsed : 0;
};
const relationOf = (cell: any) => String(cell.data?.relation ?? "support").toLowerCase();
const pairKey = (ids: string[]) => [...new Set(ids)].sort().join("\u0000");

export function projectBoard(cells: any[]): BoardProjection {
  const nodes: ProjectedNode[] = (cells ?? []).filter((cell) => cell.shape !== "edge").map((cell) => ({
    id: String(cell.id), data: cell.data ?? {}, cell,
  }));
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const rawEdges = (cells ?? []).filter((cell) => cell.shape === "edge").map((cell) => ({
    id: String(cell.id), source: endId(cell.source), target: endId(cell.target), relation: relationOf(cell), cell,
  })).filter((edge) => nodeById.has(edge.source) && nodeById.has(edge.target) && edge.source !== edge.target);

  // Presence of parent_id identifies v2, including an explicit null on root nodes.
  const isV2 = nodes.some((node) => Object.prototype.hasOwnProperty.call(node.data, "parent_id"));
  const structuralEdges: Array<{ src: string; tgt: string }> = [];
  if (isV2) {
    for (const node of nodes) {
      const parent = node.data.parent_id;
      if (typeof parent === "string" && parent !== node.id && nodeById.has(parent)) structuralEdges.push({ src: parent, tgt: node.id });
    }
  } else {
    // Legacy boards may contain two incoming hierarchy edges. Only the first
    // defines placement; the other remains a visible cross-parent link.
    const assigned = new Set<string>();
    for (const edge of rawEdges) if (["subordinate", "child"].includes(edge.relation) && !assigned.has(edge.target)) {
      structuralEdges.push({ src: edge.source, tgt: edge.target });
      assigned.add(edge.target);
    }
    // v1 often stored point→evidence only as a semantic support/oppose edge.
    // When exactly one point owns the evidence, use it for visual placement.
    const evidenceParents = new Map<string, Set<string>>();
    for (const edge of rawEdges) {
      if (!["support", "oppose"].includes(edge.relation)
        || nodeById.get(edge.source)?.data.type !== "point"
        || nodeById.get(edge.target)?.data.type !== "evidence") continue;
      const parents = evidenceParents.get(edge.target) ?? new Set<string>();
      parents.add(edge.source);
      evidenceParents.set(edge.target, parents);
    }
    for (const [target, parents] of evidenceParents) {
      if (assigned.has(target) || parents.size !== 1) continue;
      structuralEdges.push({ src: [...parents][0], tgt: target });
      assigned.add(target);
    }
  }

  const children = new Map<string, string[]>();
  const parentById = new Map<string, string>();
  for (const edge of structuralEdges) {
    // Defensive guard for malformed legacy data: retain the first parent only.
    if (parentById.has(edge.tgt)) continue;
    parentById.set(edge.tgt, edge.src);
    const siblings = children.get(edge.src) ?? [];
    siblings.push(edge.tgt);
    children.set(edge.src, siblings);
  }
  const roots = nodes.map((node) => node.id).filter((id) => !parentById.has(id));

  const semanticEdges = rawEdges.filter((edge) => !["subordinate", "child"].includes(edge.relation));
  const semanticEdgesByNode = new Map<string, ProjectedEdge[]>();
  for (const edge of semanticEdges) for (const id of [edge.source, edge.target]) {
    const related = semanticEdgesByNode.get(id) ?? [];
    related.push(edge);
    semanticEdgesByNode.set(id, related);
  }

  const evidenceById = new Map<string, string[]>();
  for (const node of nodes) for (const ref of node.data.metadata_refs ?? []) {
    if (typeof ref !== "string") continue;
    const owners = evidenceById.get(ref) ?? [];
    if (!owners.includes(node.id)) owners.push(node.id);
    evidenceById.set(ref, owners);
  }

  const unresolvedByKey = new Map<string, UnresolvedItem>();
  for (const edge of semanticEdges) if (edge.relation === "oppose") {
    const nodeIds = [edge.source, edge.target].sort();
    const key = `dispute:${pairKey(nodeIds)}`;
    if (!unresolvedByKey.has(key)) unresolvedByKey.set(key, {
      id: key, kind: "dispute", nodeIds, label: "尚未解决的反对关系", source: "oppose-edge",
    });
  }
  for (const node of nodes) {
    if (node.data.type === "conflict" && !closed(node.data)) {
      const participants = Array.isArray(node.data.participants) ? node.data.participants.filter((id: unknown) => typeof id === "string") : [];
      const nodeIds = participants.length ? [...new Set(participants)].sort() : [node.id];
      const key = `dispute:${pairKey(nodeIds)}`;
      if (!unresolvedByKey.has(key)) unresolvedByKey.set(key, {
        id: key, kind: "dispute", nodeIds, label: String(node.data.label ?? "尚未解决的分歧"), source: "conflict-node",
      });
    }
  }
  const conclusions = nodes.filter((node) => node.data.type === "conclusion");
  for (const node of conclusions) {
    const status = String(node.data.status ?? "").toLowerCase();
    if (!closed(node.data) && status !== "confirmed" && status !== "superseded") {
      unresolvedByKey.set(`confirmation:${node.id}`, {
        id: `confirmation:${node.id}`, kind: "confirmation", nodeIds: [node.id],
        label: String(node.data.label ?? "待确认结论"), source: "conclusion",
      });
    }
  }

  const actions = nodes.filter((node) => node.data.type === "action");
  const inProgressIssue = nodes.find((node) => node.data.type === "issue" && ["in_progress", "in-progress", "active", "进行中"].includes(String(node.data.status ?? "").toLowerCase()));
  const openHigh = nodes.filter((node) => !closed(node.data) && node.data.importance?.level === "high")
    .sort((a, b) => timestamp(b) - timestamp(a));
  const focus = inProgressIssue ?? openHigh[0] ?? null;

  return {
    nodes, nodeById, structuralEdges, semanticEdges, semanticEdgesByNode, children, parentById, roots,
    evidenceById, unresolved: [...unresolvedByKey.values()], conclusions, actions, focus,
    formatEvidenceTime(record) {
      if (typeof record.start_offset_ms !== "number" || !Number.isFinite(record.start_offset_ms)) return "时间未知";
      const seconds = Math.max(0, Math.floor(record.start_offset_ms / 1000));
      return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    },
  };
}

/** Return a graph neighborhood around one node; unknown focus keeps the full board. */
export function projectLocalNeighborhood(cells: any[], focusId: string, maxHops = 2): any[] {
  const source = cells ?? [];
  const projection = projectBoard(source);
  if (!projection.nodeById.has(focusId)) return source;
  const adjacency = new Map<string, Set<string>>();
  const connect = (a: string, b: string) => {
    if (!adjacency.has(a)) adjacency.set(a, new Set());
    if (!adjacency.has(b)) adjacency.set(b, new Set());
    adjacency.get(a)!.add(b);
    adjacency.get(b)!.add(a);
  };
  for (const edge of projection.structuralEdges) connect(edge.src, edge.tgt);
  for (const edge of projection.semanticEdges) connect(edge.source, edge.target);
  // Secondary legacy hierarchy links must also be reachable in focused view.
  for (const cell of source) if (cell.shape === "edge" && ["subordinate", "child"].includes(relationOf(cell))) {
    const a = endId(cell.source), b = endId(cell.target);
    if (projection.nodeById.has(a) && projection.nodeById.has(b)) connect(a, b);
  }

  const radius = Math.max(0, Math.floor(maxHops));
  const included = new Set([focusId]);
  let frontier = [focusId];
  for (let depth = 0; depth < radius; depth += 1) {
    const next: string[] = [];
    for (const id of frontier) for (const neighbor of adjacency.get(id) ?? []) {
      if (included.has(neighbor)) continue;
      included.add(neighbor);
      next.push(neighbor);
    }
    frontier = next;
    if (!frontier.length) break;
  }
  return source.filter((cell) => {
    if (cell.shape !== "edge") return included.has(String(cell.id));
    const sourceId = typeof cell.source === "string" ? cell.source : String(cell.source?.cell ?? "");
    const targetId = typeof cell.target === "string" ? cell.target : String(cell.target?.cell ?? "");
    return included.has(sourceId) && included.has(targetId);
  });
}
