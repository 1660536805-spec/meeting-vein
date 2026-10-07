import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, waitFor } from "@testing-library/dom";
import { mountAsrPanel } from "./panel";
import type { NormUtterance } from "./event";

const { getModelStatusMock, getProductStatusMock, getUtteranceStatusMock, transcribeAudioMock, submitUtteranceMock, recorderStartMock, recorderPauseMock, recorderResumeMock, recorderStopMock, recorderDisposeMock, recorderPermission, liveStartMock, livePauseMock, liveResumeMock, liveStopMock, liveFinishMock, liveState } = vi.hoisted(() => ({
  getModelStatusMock: vi.fn(), getProductStatusMock: vi.fn(), getUtteranceStatusMock: vi.fn(), transcribeAudioMock: vi.fn(), submitUtteranceMock: vi.fn(),
  recorderStartMock: vi.fn(), recorderPauseMock: vi.fn(), recorderResumeMock: vi.fn(), recorderStopMock: vi.fn(), recorderDisposeMock: vi.fn(),
  recorderPermission: { denied: false },
  liveStartMock: vi.fn(), livePauseMock: vi.fn(), liveResumeMock: vi.fn(), liveStopMock: vi.fn(), liveFinishMock: vi.fn(),
  liveState: { callbacks: null as null | { onReady?: () => void; onPartial: (text: string, latencyMs: number) => void; onSegment?: (segment: { segmentId: string; text: string; language: string; startOffsetMs: number; endOffsetMs: number }) => void; onError: (error: Error) => void } },
}));
vi.mock("./api", () => ({ getModelStatus: getModelStatusMock, getProductStatus: getProductStatusMock, getUtteranceStatus: getUtteranceStatusMock, transcribeAudio: transcribeAudioMock, submitUtterance: submitUtteranceMock }));
vi.mock("./live-stream", () => ({ LiveStream: class {
  constructor(callbacks: typeof liveState.callbacks) { liveState.callbacks = callbacks; }
  start = liveStartMock;
  pause = livePauseMock;
  resume = liveResumeMock;
  stop = liveStopMock;
  finish = liveFinishMock;
} }));
vi.mock("./recorder", () => ({
  RecorderController: class {
    supported = true;
    currentState = "idle";
    constructor(private callbacks: { onState: (state: string) => void; onStream?: (stream: MediaStream) => void; onComplete: (blob: Blob) => void; onError: (error: Error) => void }) {}
    start = recorderStartMock.mockImplementation(() => {
      if (recorderPermission.denied) { this.callbacks.onError(new Error("麦克风权限被拒绝")); return Promise.resolve(); }
      this.callbacks.onStream?.({} as MediaStream);
      this.currentState = "recording"; this.callbacks.onState("recording"); return Promise.resolve();
    });
    pause = recorderPauseMock.mockImplementation(() => { this.currentState = "paused"; this.callbacks.onState("paused"); });
    resume = recorderResumeMock.mockImplementation(() => { this.currentState = "recording"; this.callbacks.onState("recording"); });
    stop = recorderStopMock.mockImplementation(() => { this.currentState = "idle"; this.callbacks.onState("idle"); this.callbacks.onComplete(new Blob(["audio"], { type: "audio/webm" })); });
    dispose = recorderDisposeMock;
  },
}));

const event: NormUtterance = {
  utterance_id: "utt_1", meeting_id: "mtg_demo", session_id: null, seq: 1,
  speaker: { speaker_ref: "local:user", display_name: "发言人", is_resolved: false },
  text: "请记录行动项", language: "zh", start_offset_ms: 0, end_offset_ms: 1000,
  received_at_ms: 1, is_final: true, is_partial: false, source: "local_sensevoice",
};
function fixture(): HTMLElement {
  document.body.innerHTML = `<button id="composer-send">发送</button><section id="asr-panel">
    <button id="asr-record">开始录音</button><button id="asr-pause" disabled>暂停</button>
    <button id="asr-resume" disabled>继续</button><button id="asr-stop" disabled>停止</button>
    <output id="asr-timer">00:00</output><input id="asr-file" type="file" accept="audio/*">
    <output id="asr-model-status"></output><output id="llm-mode"></output><output id="product-status"></output>
    <p id="asr-transcript"></p><p id="asr-message"></p>
    <button id="asr-retry" hidden>重试发送</button><button id="asr-export" hidden>导出未送达文本</button></section>`;
  return document.getElementById("asr-panel")!;
}
function button(root: HTMLElement, id: string): HTMLButtonElement { return root.querySelector<HTMLButtonElement>(id)!; }
function text(root: HTMLElement, id: string): string { return root.querySelector<HTMLElement>(id)!.textContent || ""; }
async function upload(root: HTMLElement): Promise<void> {
  fireEvent.change(root.querySelector("#asr-file")!, { target: { files: [new File(["audio"], "clip.wav", { type: "audio/wav" })] } });
  await waitFor(() => expect(transcribeAudioMock).toHaveBeenCalled());
}

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  recorderPermission.denied = false;
  liveState.callbacks = null;
  getModelStatusMock.mockResolvedValue({ state: "ready", device: "cpu" });
  getProductStatusMock.mockResolvedValue({ llm_mode: "mock", llm_instance: { type: "mock" },
    asr: { final: { state: "ready" }, streaming: { state: "ready" } },
    persistence: { store_a: "ready", store_b: "ready", pending_retries: 0 } });
  transcribeAudioMock.mockResolvedValue({ ok: true, event, transcription: { text: event.text } });
  submitUtteranceMock.mockResolvedValue({ ok: true, utterance_id: event.utterance_id, duplicate: false, state: "committed", meta_id: "meta-1", board_version: 2 });
  getUtteranceStatusMock.mockResolvedValue({ utterance_id: event.utterance_id, meta_id: "meta-1", state: "committed", board_version: 2, attempts: 1 });
  liveFinishMock.mockResolvedValue(undefined);
});
afterEach(() => vi.useRealTimers());

describe("ASR panel", () => {
  it("shows live text during recording, freezes on pause and replaces it with final text", async () => {
    const root = fixture(); const panel = mountAsrPanel(root, "mtg_demo");
    fireEvent.click(button(root, "#asr-record"));
    expect(liveStartMock).toHaveBeenCalledTimes(1);
    liveState.callbacks?.onReady?.();
    expect(text(root, "#asr-message")).toContain("开始说话");
    liveState.callbacks?.onPartial("正在说话", 850);
    expect(text(root, "#asr-transcript")).toBe("正在说话");
    expect(text(root, "#asr-message")).toContain("850");
    expect(submitUtteranceMock).not.toHaveBeenCalled();
    fireEvent.click(button(root, "#asr-pause"));
    expect(livePauseMock).toHaveBeenCalledTimes(1);
    fireEvent.click(button(root, "#asr-resume"));
    expect(liveResumeMock).toHaveBeenCalledTimes(1);
    fireEvent.click(button(root, "#asr-stop"));
    expect(liveFinishMock).toHaveBeenCalled();
    await waitFor(() => expect(text(root, "#asr-transcript")).toBe(event.text));
    liveState.callbacks?.onPartial("过期文字", 1200);
    expect(text(root, "#asr-transcript")).toBe(event.text);
    panel.dispose();
  });

  it("still sends final text when live streaming fails", async () => {
    const root = fixture(); mountAsrPanel(root, "mtg_demo");
    fireEvent.click(button(root, "#asr-record"));
    liveState.callbacks?.onError(new Error("流式模型尚未就绪"));
    expect(text(root, "#asr-message")).toContain("流式模型尚未就绪");
    fireEvent.click(button(root, "#asr-stop"));
    await waitFor(() => expect(submitUtteranceMock).toHaveBeenCalledWith(event));
  });

  it("submits each live segment to the board and skips the whole recording fallback", async () => {
    const root = fixture(); mountAsrPanel(root, "mtg_demo");
    fireEvent.click(button(root, "#asr-record"));
    liveState.callbacks?.onSegment?.({ segmentId: "utt_local_stream_1", text: "第一个分支", language: "zh", startOffsetMs: 0, endOffsetMs: 1200 });
    await waitFor(() => expect(submitUtteranceMock).toHaveBeenCalledTimes(1));
    expect(submitUtteranceMock).toHaveBeenCalledWith(expect.objectContaining({
      utterance_id: "utt_local_stream_1", text: "第一个分支", meeting_id: "mtg_demo", source: "local_streaming",
    }));
    fireEvent.click(button(root, "#asr-stop"));
    await waitFor(() => expect(liveFinishMock).toHaveBeenCalled());
    expect(transcribeAudioMock).not.toHaveBeenCalled();
    expect(submitUtteranceMock).toHaveBeenCalledTimes(1);
  });

  it("uses the full recording only as a review transcript after partial streaming failure", async () => {
    const reviewEvent = { ...event, text: "开场补充，已识别片段，尾部结论" };
    transcribeAudioMock.mockResolvedValueOnce({ ok: true, event: reviewEvent, transcription: { text: reviewEvent.text } });
    const root = fixture(); mountAsrPanel(root, "mtg_demo");
    fireEvent.click(button(root, "#asr-record"));
    liveState.callbacks?.onSegment?.({ segmentId: "utt_local_stream_1", text: "已识别片段", language: "zh", startOffsetMs: 0, endOffsetMs: 1200 });
    await waitFor(() => expect(submitUtteranceMock).toHaveBeenCalledTimes(1));
    liveState.callbacks?.onError(new Error("实时识别连接已断开"));
    fireEvent.click(button(root, "#asr-stop"));
    await waitFor(() => expect(transcribeAudioMock).toHaveBeenCalled());
    expect(text(root, "#asr-transcript")).toBe(reviewEvent.text);
    expect(text(root, "#asr-message")).toContain("建议补录遗漏：开场补充；尾部结论");
    expect(submitUtteranceMock).toHaveBeenCalledTimes(1);
  });

  it("keeps an accepted segment in local storage until the server commits it", async () => {
    vi.useFakeTimers();
    submitUtteranceMock.mockResolvedValueOnce({ ok: true, utterance_id: "utt_1", duplicate: false,
      state: "accepted", meta_id: "meta-1", board_version: null });
    getUtteranceStatusMock.mockResolvedValueOnce({ utterance_id: "utt_1", meta_id: "meta-1", state: "processing", attempts: 1 })
      .mockResolvedValueOnce({ utterance_id: "utt_1", meta_id: "meta-1", state: "committed", attempts: 1, board_version: 2 });
    const root = fixture(); mountAsrPanel(root, "mtg_demo");
    fireEvent.change(root.querySelector("#asr-file")!, { target: { files: [new File(["audio"], "clip.wav", { type: "audio/wav" })] } });
    await vi.waitFor(() => expect(submitUtteranceMock).toHaveBeenCalledTimes(1));
    expect(JSON.parse(localStorage.getItem("amo:asr-outbox:mtg_demo") || "[]")).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1200);
    await vi.waitFor(() => expect(getUtteranceStatusMock).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(1200);
    await vi.waitFor(() => expect(getUtteranceStatusMock).toHaveBeenCalledTimes(2));
    expect(localStorage.getItem("amo:asr-outbox:mtg_demo")).toBe("[]");
  });
  it("shows loading, ready and the actual LLM instance", async () => {
    let resolve!: (value: unknown) => void;
    getModelStatusMock.mockReturnValue(new Promise((done) => { resolve = done; }));
    getProductStatusMock.mockResolvedValue({ llm_mode: "real", llm_instance: { type: "openai_compatible", model: "test-model" },
      asr: { final: { state: "ready" }, streaming: { state: "ready" } },
      persistence: { store_a: "ready", store_b: "ready", pending_retries: 2 } });
    const root = fixture(); const panel = mountAsrPanel(root, "mtg_demo");
    expect(text(root, "#asr-model-status")).toContain("检查");
    resolve({ state: "ready", device: "cpu" });
    await waitFor(() => expect(text(root, "#asr-model-status")).toContain("就绪"));
    expect(text(root, "#llm-mode")).toContain("真实模型实例");
    expect(text(root, "#llm-mode")).toContain("test-model");
    expect(text(root, "#product-status")).toContain("待重试 2");
    panel.dispose();
  });

  it("reports streaming and final ASR state independently", async () => {
    getModelStatusMock.mockResolvedValue({
      state: "error", error: "终稿模型不可用", streaming_state: "ready",
    });
    getProductStatusMock.mockResolvedValue({ llm_mode: "mock", llm_instance: { type: "mock" },
      asr: { final: { state: "error", error: "终稿模型不可用" }, streaming: { state: "ready" } },
      persistence: { store_a: "ready", store_b: "ready", pending_retries: 0 } });
    const root = fixture(); const panel = mountAsrPanel(root, "mtg_demo");
    await waitFor(() => {
      expect(text(root, "#asr-model-status")).toContain("终稿不可用");
      expect(text(root, "#asr-model-status")).toContain("实时识别已就绪");
    });
    panel.dispose();
  });

  it("records, pauses, resumes, stops and submits", async () => {
    const root = fixture(); const panel = mountAsrPanel(root, "mtg_demo");
    fireEvent.click(button(root, "#asr-record"));
    expect(recorderStartMock).toHaveBeenCalledTimes(1);
    expect(button(root, "#asr-pause").disabled).toBe(false);
    fireEvent.click(button(root, "#asr-pause"));
    expect(button(root, "#asr-resume").disabled).toBe(false);
    fireEvent.click(button(root, "#asr-resume"));
    fireEvent.click(button(root, "#asr-stop"));
    await waitFor(() => expect(submitUtteranceMock).toHaveBeenCalledWith(event));
    expect(text(root, "#asr-transcript")).toBe(event.text);
    expect(text(root, "#asr-message")).toContain("已整理进看板");
    expect(recorderPauseMock).toHaveBeenCalledTimes(1);
    expect(recorderResumeMock).toHaveBeenCalledTimes(1);
    panel.dispose(); expect(recorderDisposeMock).toHaveBeenCalled();
  });

  it("keeps the transcript and retries the exact event after board failure", async () => {
    submitUtteranceMock.mockRejectedValueOnce(new Error("看板不可用"));
    getUtteranceStatusMock.mockRejectedValueOnce(new Error("状态暂不可查"));
    const root = fixture(); mountAsrPanel(root, "mtg_demo");
    await upload(root);
    await waitFor(() => expect(button(root, "#asr-retry").hidden).toBe(false));
    expect(text(root, "#asr-transcript")).toBe(event.text);
    fireEvent.click(button(root, "#asr-retry"));
    await waitFor(() => expect(submitUtteranceMock).toHaveBeenCalledTimes(2));
    expect(submitUtteranceMock).toHaveBeenNthCalledWith(2, event);
    expect(transcribeAudioMock).toHaveBeenCalledTimes(1);
  });

  it("does not replace an unsent event with a new recording", async () => {
    submitUtteranceMock.mockRejectedValueOnce(new Error("看板不可用"));
    getUtteranceStatusMock.mockRejectedValueOnce(new Error("状态暂不可查"));
    const root = fixture(); mountAsrPanel(root, "mtg_demo");
    await upload(root);
    await waitFor(() => expect(button(root, "#asr-retry").hidden).toBe(false));
    expect(button(root, "#asr-record").disabled).toBe(true);
    expect(root.querySelector<HTMLInputElement>("#asr-file")!.disabled).toBe(true);
    fireEvent.change(root.querySelector("#asr-file")!, { target: { files: [new File(["new"], "new.wav", { type: "audio/wav" })] } });
    expect(transcribeAudioMock).toHaveBeenCalledTimes(1);
    fireEvent.click(button(root, "#asr-retry"));
    await waitFor(() => expect(submitUtteranceMock).toHaveBeenCalledTimes(2));
    expect(submitUtteranceMock).toHaveBeenNthCalledWith(2, event);
  });

  it("keeps upload and manual entry usable after microphone permission is denied", async () => {
    recorderPermission.denied = true;
    const root = fixture(); mountAsrPanel(root, "mtg_demo");
    fireEvent.click(button(root, "#asr-record"));
    expect(text(root, "#asr-message")).toContain("麦克风权限被拒绝");
    expect(root.querySelector<HTMLInputElement>("#asr-file")!.disabled).toBe(false);
    await upload(root);
    await waitFor(() => expect(submitUtteranceMock).toHaveBeenCalledWith(event));
    expect(button(document.body, "#composer-send").disabled).toBe(false);
  });

  it("refreshes loading model status until it becomes ready", async () => {
    vi.useFakeTimers();
    getModelStatusMock.mockResolvedValueOnce({ state: "loading" }).mockResolvedValueOnce({ state: "ready", device: "cpu" });
    const root = fixture(); const panel = mountAsrPanel(root, "mtg_demo");
    await vi.advanceTimersByTimeAsync(5000);
    expect(getModelStatusMock).toHaveBeenCalledTimes(2);
    expect(text(root, "#asr-model-status")).toContain("就绪");
    panel.dispose();
  });

  it("keeps checking while the final model is ready but the live model loads", async () => {
    vi.useFakeTimers();
    getModelStatusMock.mockResolvedValueOnce({ state: "ready", device: "mps", streaming_state: "loading" })
      .mockResolvedValueOnce({ state: "ready", device: "mps", streaming_state: "ready" });
    const root = fixture(); const panel = mountAsrPanel(root, "mtg_demo");
    await vi.advanceTimersByTimeAsync(5000);
    expect(getModelStatusMock).toHaveBeenCalledTimes(2);
    expect(text(root, "#asr-model-status")).toContain("实时识别已就绪");
    panel.dispose();
  });

  it("leaves manual entry usable if the ASR service is unreachable", async () => {
    getModelStatusMock.mockRejectedValue(new Error("ASR 不可用"));
    getProductStatusMock.mockResolvedValue({ llm_mode: "mock", llm_instance: { type: "mock" },
      asr: { final: { state: "unavailable" }, streaming: { state: "unavailable" } },
      persistence: { store_a: "ready", store_b: "ready", pending_retries: 0 } });
    transcribeAudioMock.mockRejectedValue(new Error("ASR 不可用"));
    const root = fixture(); mountAsrPanel(root, "mtg_demo");
    await upload(root);
    await waitFor(() => expect(text(root, "#asr-message")).toContain("ASR 不可用"));
    expect(text(root, "#asr-model-status")).toContain("不可用");
    expect(button(document.body, "#composer-send").disabled).toBe(false);
  });

  it("renders recognized HTML literally", async () => {
    transcribeAudioMock.mockResolvedValue({ ok: true, event: { ...event, text: "<img src=x onerror=alert(1)>" } });
    const root = fixture(); mountAsrPanel(root, "mtg_demo");
    await upload(root);
    await waitFor(() => expect(text(root, "#asr-transcript")).toContain("<img"));
    expect(root.querySelector("img")).toBeNull();
  });
});
