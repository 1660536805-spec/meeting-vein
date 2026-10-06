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
  batched?: boolean;
}

export interface ModelStatus {
  state: "not_loaded" | "loading" | "ready" | "error";
  device?: string | null;
  error?: string | null;
  streaming_state?: "not_loaded" | "loading" | "ready" | "error";
  streaming_error?: string | null;
}

export interface ProductStatus {
  llm_mode: "mock" | "configured";
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
  const payload = await readJson(response);
  if (!response.ok) throw serverError(payload, "无法读取本地 ASR 状态");
  if (!["not_loaded", "loading", "ready", "error"].includes(String(payload.state))) {
    throw new Error("服务响应格式无效，请稍后重试");
  }
  return payload as unknown as ModelStatus;
}

export async function getProductStatus(fetcher: typeof fetch = fetch): Promise<ProductStatus> {
  const response = await fetchOrThrow(fetcher, "/api/status", "无法连接会议看板服务");
  const payload = await readJson(response);
  if (!response.ok) throw serverError(payload, "无法读取看板状态");
  if (payload.llm_mode !== "mock" && payload.llm_mode !== "configured") {
    throw new Error("服务响应格式无效，请稍后重试");
  }
  return { llm_mode: payload.llm_mode };
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
  if (typeof payload.utterance_id !== "string" || typeof payload.duplicate !== "boolean") {
    throw new Error("服务响应格式无效，请稍后重试");
  }
  return payload as unknown as IngestResponse;
}
