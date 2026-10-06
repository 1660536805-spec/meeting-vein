export interface MeetingSummary {
  meeting_id: string;
  title: string;
  version: number;
  updated_at: string | null;
  stats: { nodes: number; edges: number; types: Record<string, number> };
}

export interface MeetingHistoryRecord {
  version: number;
  created_at: string;
  title: string;
  cells: any[];
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
  create: (title: string) => post<{ ok: boolean; meeting_id: string; title: string }>("/api/meetings", { title }),
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
