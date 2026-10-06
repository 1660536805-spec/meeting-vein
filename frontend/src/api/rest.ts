/** REST 客户端（Design_FrontendBoard §4.1）。
 * 所有后端 HTTP 调用集中于此，供 BoardSDK 复用；返回类型对齐后端契约。 */

export interface CellDiff {
  cell_id: string;
  shape: string;
  changed?: string[];
}

/** 结构化变更集（§4.4）：前端「本次 AI 改了什么」可视化。 */
export interface GraphChangeSet {
  batch_id: string;
  meeting_summary_ref: string;
  added: CellDiff[];
  modified: CellDiff[];
  removed: CellDiff[];
  skipped: CellDiff[];
  generated_at: number;
}

/** 落库回执（§3.4 repair receipt）。 */
export interface RepairReceipt {
  version: number;
  applied: number;
  applied_ops: number;
  skipped: any[];
  changed_nodes: string[];
  rollback_to: number;
  errors: string[];
  change_set?: GraphChangeSet;
}

export interface BoardGraph {
  graph_id: string;
  version: number;
  updated_at: string | null;
  cells: any[];
}

export interface MetadataRecord {
  meta_id: string;
  kind?: string;
  text: string;
  speaker_ref?: string;
  start_offset_ms?: number;
  end_offset_ms?: number;
  source?: string;
}

/** 用户操作事件（§2）：{op_id, graph_id, cell_id, op, payload, actor, ts_ms}。 */
export interface UserOp {
  node_id: string;              // 对应 cell_id
  graph_id: string;
  op: string;                   // move/edit_label/edit_type/link/lock/set_importance/add/remove
  payload?: Record<string, any>;
  actor?: string;
  ts_ms?: number;
}

export interface UpdateResult {
  ok: boolean;
  version: number;
  repair_receipt: RepairReceipt;
  change_set?: GraphChangeSet;
  cells: any[];
}

/** 光标采集参数（Cursor §9，GET /api/cursor/config 下发）。 */
export interface CursorConfig {
  throttle_ms: number;
  hover_settle_ms: number;
  drag_px_threshold: number;
  cursor_independent_trigger: boolean;
}

/** 历史会议摘要（GET /api/meetings）。 */
export interface MeetingSummaryInfo {
  meeting_id: string;
  title: string;
  version: number;
  updated_at: string | null;
  stats: { nodes: number; edges: number; types: Record<string, number> };
}

export interface MeetingsResponse {
  default_meeting_id: string;
  meetings: MeetingSummaryInfo[];
}

/** 版本历史记录（Storage §8，GET /api/board/:graph_id/history）。 */
export interface HistoryRecord {
  graph_id: string;
  cell_id: string;
  version: number;
  changed_by: string;
  trigger: string;
  snapshot: { data: Record<string, any>; position?: { x: number; y: number } | null };
  created_at: string;
}

/** 会议重命名结果（POST /api/meetings/:graph_id/rename）。 */
export interface RenameResult {
  ok: boolean;
  meeting_id?: string;
  title?: string;
  version?: number;
  updated_at?: string | null;
  error?: string;
}

/** 新建会议结果（POST /api/meetings）：预建空文档，返回 meeting_id 供前端切换。 */
export interface CreateMeetingResult {
  ok: boolean;
  meeting_id?: string;
  title?: string;
  version?: number;
  updated_at?: string | null;
  error?: string;
}

/** 显式保存结果（POST /api/board/:graph_id/save）：版本打点。 */
export interface SaveResult {
  ok: boolean;
  graph_id?: string;
  version?: number;
  updated_at?: string | null;
  error?: string;
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} @ ${url}`);
  return (await res.json()) as T;
}

function post<T>(url: string, body: any): Promise<T> {
  return jsonFetch<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

export const rest = {
  loadBoard: (graphId: string) => jsonFetch<BoardGraph>(`/api/board/${encodeURIComponent(graphId)}`),

  outline: (graphId: string) =>
    jsonFetch<{ graph_id: string; outline: string }>(`/api/board/${encodeURIComponent(graphId)}/outline`),

  update: (
    graphId: string,
    operations: any[],
    opts: { thought?: string; batch_id?: string; meeting_summary_ref?: string } = {},
  ) =>
    post<UpdateResult>(`/api/board/${encodeURIComponent(graphId)}/update`, { operations, ...opts }),

  lock: (nodeId: string, graphId: string, locked: boolean, lockedBy = "human") =>
    post<any>(`/api/node/${encodeURIComponent(nodeId)}/lock`, {
      graph_id: graphId, locked, locked_by: lockedBy,
    }),

  setImportance: (nodeId: string, graphId: string, level: string) =>
    post<any>(`/api/node/${encodeURIComponent(nodeId)}/importance`, { graph_id: graphId, level }),

  emitUserOp: (op: UserOp) =>
    post<any>(`/api/node/${encodeURIComponent(op.node_id)}/op`, {
      graph_id: op.graph_id,
      op: op.op,
      payload: op.payload ?? {},
      actor: op.actor ?? "web:user",
      ts_ms: op.ts_ms,
    }),

  rollback: (graphId: string, cellId: string, version: number) =>
    post<any>(`/api/board/${encodeURIComponent(graphId)}/rollback`, { cell_id: cellId, version }),

  history: (graphId: string, cellId?: string) =>
    jsonFetch<{ graph_id: string; records: any[] }>(
      `/api/board/${encodeURIComponent(graphId)}/history` +
        (cellId ? `?cell_id=${encodeURIComponent(cellId)}` : ""),
    ),

  snapshot: (graphId: string) =>
    post<{ ok: boolean; token: string; url: string; version: number }>(
      `/api/board/${encodeURIComponent(graphId)}/snapshot`, {}),

  metadata: (ids: string[]) =>
    jsonFetch<{ records: MetadataRecord[] }>(`/api/metadata?ids=${encodeURIComponent(ids.join(","))}`),

  mascotConfig: () =>
    jsonFetch<{ idle_return_ms: number; states: string[] }>("/api/mascot/config"),

  cursorConfig: () => jsonFetch<CursorConfig>("/api/cursor/config"),

  listMeetings: () => jsonFetch<MeetingsResponse>("/api/meetings"),

  createMeeting: (title: string) => post<CreateMeetingResult>("/api/meetings", { title }),

  saveBoard: (graphId: string) =>
    post<SaveResult>(`/api/board/${encodeURIComponent(graphId)}/save`, {}),

  renameMeeting: (graphId: string, title: string) =>
    post<RenameResult>(`/api/meetings/${encodeURIComponent(graphId)}/rename`, { title }),
};
