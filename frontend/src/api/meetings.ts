export interface MeetingSummary {
  meeting_id: string;
  title: string;
  version: number;
  updated_at: string | null;
  stats: { nodes: number; edges: number; types: Record<string, number> };
  status?: "draft" | "live" | "ended";
  agenda?: string[];
}

export interface MeetingCloseReview {
  unconfirmed: Array<{ id: string; label: string }>;
  disputes: Array<{ from?: string; to?: string; label?: string }>;
  incomplete_actions: Array<{ id: string; label: string; missing: string[] }>;
  pending_questions: Array<{ id: string; text: string }>;
}

export interface MeetingHistoryRecord {
  version: number;
  created_at: string;
  title: string;
  cells: any[];
  meeting_status?: "draft" | "live" | "ended";
  agenda?: string[];
}

export interface UtteranceRecord {
  meta_id: string;
  kind: string;
  meeting_id?: string;
  text: string;
  speaker_ref: string;
  start_offset_ms: number;
  end_offset_ms: number;
  source: string;
}

export interface PendingRetryResult {
  ok: boolean;
  meeting_id: string;
  recovered: number;
  still_failing: number;
  skipped_max_attempts: number;
}

export interface ActionRecord {
  id: string;
  meeting_id: string;
  action_node_id: string;
  decision_node_id: string | null;
  event_id: string;
  owner: string | null;
  due_date: string | null;
  status: "open" | "in_progress" | "done" | "delayed" | "cancelled";
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ActionEvent {
  id: number;
  meeting_id: string;
  action_id: string;
  event_type: "registered" | "updated";
  before: ActionRecord | null;
  after: ActionRecord;
  created_at: string;
}

export interface PreparationSuggestion {
  text: string;
  source_kind: "agenda" | "material";
  source_ref: string;
  source_label: string;
}

export interface PreparationQuestion {
  id: string;
  meeting_id: string;
  event_id: string;
  text: string;
  source_kind: "agenda" | "material" | "manual";
  source_ref: string | null;
  status: "pending" | "resolved" | "dismissed";
  version: number;
  created_at: string;
  updated_at: string;
}

export interface PreparationMaterial {
  id: string;
  meeting_id: string;
  filename: string;
  content_sha256: string;
  created_at: string;
}

export interface MeetingTimelineEntry {
  link: { id: string; source_meeting_id: string; target_meeting_id: string;
    actor: string; reason: string; created_at: string };
  source_meeting: MeetingSummary;
  old_topic: { topic: { id: string; label: string }; confirmed_decisions: Array<{ id: string; label: string }> };
  new_topic: { topic: { id: string; label: string }; confirmed_decisions: Array<{ id: string; label: string }>;
    evidence: Array<{ id: string; label: string; source_utterances: Array<{ meta_id: string; text: string | null }> }> };
  unfinished_actions: Array<ActionRecord & { label: string }>;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const message = await response.text().catch(() => "");
    throw new Error(message || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

function post<T>(url: string, body: unknown = {}): Promise<T> {
  return request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const meetingsApi = {
  preparation: (id: string) => request<{ ok: boolean; materials: PreparationMaterial[];
    suggestions: PreparationSuggestion[]; questions: PreparationQuestion[] }>(
    `/api/meetings/${encodeURIComponent(id)}/preparation`),
  uploadMaterial: (id: string, filename: string, contentBase64: string) =>
    post<{ ok: boolean; material: PreparationMaterial }>(
      `/api/meetings/${encodeURIComponent(id)}/materials`, {
        filename, content_base64: contentBase64,
      }),
  addQuestion: (id: string, eventId: string, question: string,
                sourceKind: PreparationQuestion["source_kind"], sourceRef: string | null) =>
    post<{ ok: boolean; question: PreparationQuestion }>(
      `/api/meetings/${encodeURIComponent(id)}/questions`, {
        event_id: eventId, text: question, source_kind: sourceKind, source_ref: sourceRef,
      }),
  updateQuestion: (id: string, questionId: string, expectedVersion: number,
                   status: PreparationQuestion["status"]) => request<{ ok: boolean; question: PreparationQuestion }>(
    `/api/meetings/${encodeURIComponent(id)}/questions/${encodeURIComponent(questionId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: expectedVersion, status }),
    }),
  actions: (id: string) => request<{ ok: boolean; actions: ActionRecord[] }>(
    `/api/meetings/${encodeURIComponent(id)}/actions`),
  actionHistory: (id: string, actionId: string) => request<{ ok: boolean; events: ActionEvent[] }>(
    `/api/meetings/${encodeURIComponent(id)}/actions/${encodeURIComponent(actionId)}/history`),
  registerAction: (id: string, actionNodeId: string, eventId: string,
                   decisionNodeId: string | null = null) => post<{ ok: boolean; action: ActionRecord }>(
    `/api/meetings/${encodeURIComponent(id)}/actions`, {
      action_node_id: actionNodeId, event_id: eventId, decision_node_id: decisionNodeId,
    }),
  updateAction: (id: string, actionId: string, expectedVersion: number,
                 changes: Partial<Pick<ActionRecord, "owner" | "due_date" | "status">>) =>
    request<{ ok: boolean; action: ActionRecord }>(
      `/api/meetings/${encodeURIComponent(id)}/actions/${encodeURIComponent(actionId)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_version: expectedVersion, changes }),
      }),
  list: () => request<{ default_meeting_id: string; meetings: MeetingSummary[] }>("/api/meetings"),
  create: (title: string, agenda_text = "") => post<{ ok: boolean; meeting_id: string; title: string; agenda: string[] }>("/api/meetings", { title, agenda_text }),
  setStatus: (id: string, status: "live" | "ended") => post<{ ok: boolean; status: string; review?: MeetingCloseReview }>(`/api/meetings/${encodeURIComponent(id)}/status`, { status }),
  retryPending: (id: string) => post<PendingRetryResult>(`/api/meetings/${encodeURIComponent(id)}/retry_pending`),
  closePreview: (id: string) => request<{ ok: boolean; review: MeetingCloseReview }>(`/api/meetings/${encodeURIComponent(id)}/close-preview`),
  minutes: async (id: string, format: "markdown" | "html") => {
    const response = await fetch(`/api/meetings/${encodeURIComponent(id)}/minutes?format=${format}`);
    if (!response.ok) throw new Error(await response.text().catch(() => "纪要导出失败"));
    return response.blob();
  },
  localSnapshot: async (id: string) => {
    const response = await fetch(`/api/meetings/${encodeURIComponent(id)}/snapshot-file`);
    if (!response.ok) throw new Error(await response.text().catch(() => "本地快照导出失败"));
    return response.blob();
  },
  save: (id: string, edits?: object) => post<{ ok: boolean; version: number }>(
    `/api/board/${encodeURIComponent(id)}/save`, { edits }),
  shareSnapshot: (id: string, edits?: object) => post<{ ok: boolean; token: string; url: string; version: number }>(
    `/api/board/${encodeURIComponent(id)}/snapshot`, { edits }),
  rename: (id: string, title: string) => post<{ ok: boolean; title: string }>(
    `/api/meetings/${encodeURIComponent(id)}/rename`, { title }),
  board: (id: string) => request<{ graph_id: string; version: number; title: string; cells: any[] }>(
    `/api/board/${encodeURIComponent(id)}`),
  history: (id: string) => request<{ graph_id: string; records: MeetingHistoryRecord[] }>(
    `/api/board/${encodeURIComponent(id)}/history`),
  utterances: (id: string) => request<{ graph_id: string; utterances: UtteranceRecord[] }>(
    `/api/meetings/${encodeURIComponent(id)}/utterances`),
  materialSource: (id: string, sourceRef: string) => request<{ ok: boolean; source_ref: string; text: string }>(
    `/api/meetings/${encodeURIComponent(id)}/material-source?source_ref=${encodeURIComponent(sourceRef)}`),
  timeline: (id: string) => request<{ ok: boolean; entries: MeetingTimelineEntry[] }>(
    `/api/meetings/${encodeURIComponent(id)}/timeline`),
  linkMeeting: (targetId: string, body: { source_meeting_id: string; source_topic_node_id: string;
    target_topic_node_id: string; event_id: string; actor: string; reason: string }) =>
    post<{ ok: boolean }>(`/api/meetings/${encodeURIComponent(targetId)}/links`, body),
};
