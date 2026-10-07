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
};
