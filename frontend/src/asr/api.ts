import type { NormUtterance } from "./event";

export type { NormUtterance } from "./event";

export interface TranscriptionResult {
  text: string;
  language: string;
  start_ms: number | null;
  end_ms: number | null;
  audio_duration_ms: number | null;
  tags: string[];
}

export interface TranscriptionResponse {
  ok: true;
  transcription: TranscriptionResult;
  event: NormUtterance;
}

export interface IngestResponse {
  ok: true;
  utterance_id: string;
  duplicate: boolean;
  state: "accepted" | "processing" | "committed" | "failed";
  meta_id: string;
  board_version: number | null;
  board_effect?: "linked" | "unlinked" | null;
  error?: string;
  batched?: boolean;
}

export interface UtteranceStatus extends Omit<IngestResponse, "ok" | "duplicate" | "batched"> {
  attempts: number;
  last_error?: string | null;
  updated_at_ms?: number;
}

export interface ModelStatus {
  state: "not_loaded" | "loading" | "ready" | "error";
  device?: string | null;
  error?: string | null;
  streaming_state?: "not_loaded" | "loading" | "ready" | "error";
  streaming_error?: string | null;
}

export interface ProductStatus {
  llm_mode: "mock" | "real" | "unknown";
  llm_instance: { type: "mock" | "openai_compatible" | "custom"; model?: string | null };
  asr: {
    streaming: { state: string; error?: string | null };
    final: { state: string; error?: string | null; device?: string | null };
  };
  persistence: {
    store_a: string;
    store_b: string;
    pending_retries: number;
  };
  pending_by_meeting?: Record<string, number>;
}

async function fetchOrThrow(
  fetcher: typeof fetch,
  url: string,
  message: string,
  init?: RequestInit,
): Promise<Response> {
  try {
    return await fetcher(url, init);
  } catch {
    throw new Error(message);
  }
}

async function readJson(response: Response): Promise<Record<string, unknown>> {
  try {
    const payload: unknown = await response.json();
    if (payload && typeof payload === "object" && !Array.isArray(payload)) {
      return payload as Record<string, unknown>;
    }
  } catch {
    // The caller owns the destination-specific error message.
  }
  throw new Error("服务响应格式无效，请稍后重试");
}

function serverError(payload: Record<string, unknown>, fallback: string): Error {
  const detail = payload.error;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const message = (detail as Record<string, unknown>).message;
    if (typeof message === "string" && message.trim()) return new Error(message);
  }
  return new Error(fallback);
}

export async function getModelStatus(fetcher: typeof fetch = fetch): Promise<ModelStatus> {
  const response = await fetchOrThrow(fetcher, "/asr/models/status", "无法连接本地 ASR 服务");
  let payload: Record<string, unknown>;
  try {
    payload = await readJson(response);
  } catch {
    throw new Error(response.ok ? "本地 ASR 服务响应格式无效" : `本地 ASR 服务不可用（HTTP ${response.status}）`);
  }
  if (!response.ok) throw serverError(payload, `本地 ASR 服务不可用（HTTP ${response.status}）`);
  if (!["not_loaded", "loading", "ready", "error"].includes(String(payload.state))) {
    throw new Error("服务响应格式无效，请稍后重试");
  }
  return payload as unknown as ModelStatus;
}

export async function getProductStatus(fetcher: typeof fetch = fetch): Promise<ProductStatus> {
  const response = await fetchOrThrow(fetcher, "/api/status", "无法连接会议看板服务");
  const payload = await readJson(response);
  if (!response.ok) throw serverError(payload, "无法读取看板状态");
  if (payload.llm_mode !== "mock" && payload.llm_mode !== "real" && payload.llm_mode !== "unknown") {
    throw new Error("服务响应格式无效，请稍后重试");
  }
  const llmInstance = payload.llm_instance as ProductStatus["llm_instance"] | undefined;
  const asr = payload.asr as ProductStatus["asr"] | undefined;
  const persistence = payload.persistence as ProductStatus["persistence"] | undefined;
  if (!llmInstance || !asr?.streaming || !asr?.final || !persistence) {
    throw new Error("服务响应格式无效，请稍后重试");
  }
  const pendingByMeeting = payload.pending_by_meeting;
  return {
    llm_mode: payload.llm_mode, llm_instance: llmInstance, asr, persistence,
    pending_by_meeting: pendingByMeeting && typeof pendingByMeeting === "object" && !Array.isArray(pendingByMeeting)
      ? pendingByMeeting as Record<string, number> : undefined,
  };
}

export async function transcribeAudio(
  blob: Blob,
  meetingId: string,
  seq: number,
  fetcher: typeof fetch = fetch,
): Promise<TranscriptionResponse> {
  const form = new FormData();
  const filename = blob instanceof File && blob.name
    ? blob.name
    : blob.type.includes("mp4") ? "recording.m4a" : "recording.webm";
  form.append("file", blob, filename);
  form.append("meeting_id", meetingId);
  form.append("seq", String(seq));
  form.append("language", "auto");
  const response = await fetchOrThrow(fetcher, "/asr/v1/audio/transcriptions", "无法连接本地 ASR 服务", {
    method: "POST", body: form,
  });
  const payload = await readJson(response);
  if (!response.ok || payload.ok !== true) throw serverError(payload, "语音识别失败，请重试");
  if (!payload.event || typeof payload.event !== "object" || typeof (payload.event as Record<string, unknown>).text !== "string") {
    throw new Error("服务响应格式无效，请稍后重试");
  }
  return payload as unknown as TranscriptionResponse;
}

export async function submitUtterance(
  event: NormUtterance,
  fetcher: typeof fetch = fetch,
): Promise<IngestResponse> {
  const response = await fetchOrThrow(fetcher, "/api/utterances", "无法连接会议看板服务", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(event),
  });
  const payload = await readJson(response);
  if (!response.ok || payload.ok !== true) throw serverError(payload, "看板未能接收转写，请重试发送");
  if (typeof payload.utterance_id !== "string" || typeof payload.duplicate !== "boolean" ||
      !["accepted", "processing", "committed", "failed"].includes(String(payload.state)) ||
      typeof payload.meta_id !== "string") {
    throw new Error("服务响应格式无效，请稍后重试");
  }
  return payload as unknown as IngestResponse;
}

export async function getUtteranceStatus(
  utteranceId: string,
  meetingId: string,
  fetcher: typeof fetch = fetch,
): Promise<UtteranceStatus> {
  const query = new URLSearchParams({ meeting_id: meetingId });
  const response = await fetchOrThrow(fetcher,
    `/api/utterances/${encodeURIComponent(utteranceId)}/status?${query}`, "无法连接会议看板服务");
  const payload = await readJson(response);
  if (!response.ok) throw serverError(payload, "无法读取转写处理状态");
  if (typeof payload.utterance_id !== "string" || typeof payload.meta_id !== "string" ||
      !["accepted", "processing", "committed", "failed"].includes(String(payload.state))) {
    throw new Error("服务响应格式无效，请稍后重试");
  }
  return payload as unknown as UtteranceStatus;
}
