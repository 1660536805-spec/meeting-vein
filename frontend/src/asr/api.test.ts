import { describe, expect, it, vi } from "vitest";
import { getModelStatus, getProductStatus, getUtteranceStatus, submitUtterance, transcribeAudio } from "./api";

const event = {
  utterance_id: "utt_local_1",
  meeting_id: "mtg_demo",
  session_id: null,
  seq: 7,
  speaker: { speaker_ref: "local:user", display_name: "本地发言人", is_resolved: false },
  text: "我们先完成原型验收",
  language: "zh",
  start_offset_ms: 0,
  end_offset_ms: 1200,
  received_at_ms: 1,
  is_final: true as const,
  is_partial: false as const,
  source: "local_sensevoice" as const,
};

const transcription = {
  text: event.text,
  language: "zh",
  start_ms: 0,
  end_ms: 1200,
  audio_duration_ms: 1200,
  tags: [],
};

function fakeFetch(response: Response): typeof fetch {
  return vi.fn(async () => response) as unknown as typeof fetch;
}

describe("local ASR transport", () => {
  it("uploads audio with the selected meeting and sequence", async () => {
    const fetcher = fakeFetch(new Response(JSON.stringify({ ok: true, transcription, event }), { status: 200 }));
    const result = await transcribeAudio(new Blob(["audio"], { type: "audio/webm" }), "mtg_demo", 7, fetcher);

    expect(result.event).toEqual(event);
    expect(fetcher).toHaveBeenCalledWith("/asr/v1/audio/transcriptions", expect.objectContaining({ method: "POST" }));
    const form = vi.mocked(fetcher).mock.calls[0][1]?.body as FormData;
    expect(form.get("meeting_id")).toBe("mtg_demo");
    expect(form.get("seq")).toBe("7");
    expect((form.get("file") as File).name).toBe("recording.webm");
  });

  it("uses an m4a filename for MP4 microphone audio", async () => {
    const fetcher = fakeFetch(new Response(JSON.stringify({ ok: true, transcription, event }), { status: 200 }));
    await transcribeAudio(new Blob(["audio"], { type: "audio/mp4" }), "mtg_demo", 8, fetcher);
    const form = vi.mocked(fetcher).mock.calls[0][1]?.body as FormData;
    expect((form.get("file") as File).name).toBe("recording.m4a");
  });

  it("reads ASR and product status from same-origin proxies", async () => {
    const modelFetcher = fakeFetch(new Response(JSON.stringify({ ok: true, state: "ready", model: "SenseVoiceSmall" })));
    const productFetcher = fakeFetch(new Response(JSON.stringify({
      llm_mode: "mock", llm_instance: { type: "mock", model: null },
      asr: { streaming: { state: "unavailable" }, final: { state: "unavailable" } },
      persistence: { store_a: "ready", store_b: "ready", pending_retries: 0 },
    })));
    expect((await getModelStatus(modelFetcher)).state).toBe("ready");
    expect((await getProductStatus(productFetcher)).llm_mode).toBe("mock");
    expect(vi.mocked(modelFetcher).mock.calls[0][0]).toBe("/asr/models/status");
    expect(vi.mocked(productFetcher).mock.calls[0][0]).toBe("/api/status");
  });

  it("submits the exact normalized event to the board", async () => {
    const payload = { ok: true, utterance_id: "utt_local_1", duplicate: false, state: "committed", meta_id: "meta-1", board_version: 2 };
    const fetcher = fakeFetch(new Response(JSON.stringify(payload)));
    expect(await submitUtterance(event, fetcher)).toEqual(payload);
    expect(fetcher).toHaveBeenCalledWith("/api/utterances", expect.objectContaining({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(event),
    }));
  });

  it("queries the processing state by stable utterance and meeting ids", async () => {
    const status = { utterance_id: "utt_local_1", meta_id: "meta-1", state: "processing", attempts: 1, board_version: null };
    const fetcher = fakeFetch(new Response(JSON.stringify(status)));
    expect(await getUtteranceStatus("utt_local_1", "mtg_demo", fetcher)).toEqual(status);
    expect(fetcher).toHaveBeenCalledWith("/api/utterances/utt_local_1/status?meeting_id=mtg_demo", undefined);
  });

  it("shows ASR's useful error message", async () => {
    const fetcher = fakeFetch(new Response(JSON.stringify({
      ok: false, error: { code: "model_not_ready", message: "本地语音模型尚未完成加载" },
    }), { status: 503 }));
    await expect(transcribeAudio(new Blob(["x"]), "mtg_demo", 9, fetcher))
      .rejects.toThrow("本地语音模型尚未完成加载");
  });

  it("reports invalid JSON as a readable response failure", async () => {
    const fetcher = fakeFetch(new Response("not-json", { status: 200 }));
    await expect(getModelStatus(fetcher)).rejects.toThrow("响应格式");
  });

  it("reports unavailable ASR and unavailable board separately", async () => {
    const unavailable = vi.fn(async () => { throw new TypeError("network unreachable"); }) as unknown as typeof fetch;
    await expect(transcribeAudio(new Blob(["x"]), "mtg_demo", 10, unavailable))
      .rejects.toThrow("无法连接本地 ASR 服务");

    const board = fakeFetch(new Response(JSON.stringify({ detail: "server error" }), { status: 500 }));
    await expect(submitUtterance(event, board)).rejects.toThrow("看板");
  });
});
