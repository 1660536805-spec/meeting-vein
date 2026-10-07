import { afterEach, describe, expect, it, vi } from "vitest";
import { PcmChunker, LiveStream } from "./live-stream";

describe("PcmChunker", () => {
  it("emits one 480 ms PCM16 frame from 48 kHz microphone audio", () => {
    const frames: ArrayBuffer[] = [];
    const chunker = new PcmChunker(48_000, (frame) => frames.push(frame));
    for (let i = 0; i < 22; i++) chunker.push(new Float32Array(1024).fill(0.5));
    expect(frames).toHaveLength(0);
    chunker.push(new Float32Array(1024).fill(0.5));
    expect(frames).toHaveLength(1);
    expect(frames[0].byteLength).toBe(15_360);
    expect(new DataView(frames[0]).getInt16(0, true)).toBe(16_384);
  });

  it("does not emit paused audio and continues after resuming", () => {
    const frames: ArrayBuffer[] = [];
    const chunker = new PcmChunker(16_000, (frame) => frames.push(frame));
    chunker.push(new Float32Array(6_000).fill(0.5));
    chunker.pause();
    chunker.push(new Float32Array(9_600).fill(-0.5));
    expect(frames).toHaveLength(0);
    chunker.resume();
    chunker.push(new Float32Array(1_680).fill(0.5));
    expect(frames).toHaveLength(1);
    expect(new DataView(frames[0]).getInt16(15_358, true)).toBe(16_384);
  });
});

class FakeSocket extends EventTarget {
  static last: FakeSocket;
  static all: FakeSocket[] = [];
  readyState = 1;
  bufferedAmount = 0;
  sent: Array<ArrayBuffer | string> = [];
  constructor(readonly url: string) { super(); FakeSocket.last = this; FakeSocket.all.push(this); }
  send(data: ArrayBuffer | string): void { this.sent.push(data); }
  close(): void { this.readyState = 3; this.dispatchEvent(new Event("close")); }
  drop(): void { this.readyState = 3; this.dispatchEvent(new Event("close")); }
  receive(payload: object): void {
    this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(payload) }));
  }
}

describe("LiveStream", () => {
  afterEach(() => { vi.useRealTimers(); FakeSocket.all = []; vi.unstubAllGlobals(); });

  it("keeps audio paused when worklet loading finishes after pause", async () => {
    let finishLoading!: () => void;
    const loading = new Promise<void>((resolve) => { finishLoading = resolve; });
    class FakeContext {
      sampleRate = 16_000;
      destination = {};
      audioWorklet = { addModule: () => loading };
      createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      resume() { return Promise.resolve(); }
      close() { return Promise.resolve(); }
    }
    class FakeNode {
      static last: FakeNode;
      port: { onmessage: ((event: MessageEvent<Float32Array>) => void) | null } = { onmessage: null };
      constructor() { FakeNode.last = this; }
      connect() {}
      disconnect() {}
    }
    vi.stubGlobal("AudioContext", FakeContext);
    vi.stubGlobal("AudioWorkletNode", FakeNode);
    const stream = new LiveStream({ onPartial: vi.fn(), onError: vi.fn() }, {
      WebSocketClass: FakeSocket as unknown as typeof WebSocket,
    });
    const started = stream.start({} as MediaStream);
    stream.pause();
    finishLoading();
    await started;
    FakeSocket.last.receive({ type: "ready" });
    FakeNode.last.port.onmessage?.({ data: new Float32Array(7_680) } as MessageEvent<Float32Array>);
    expect(FakeSocket.last.sent).toHaveLength(0);
    stream.resume();
    FakeNode.last.port.onmessage?.({ data: new Float32Array(7_680) } as MessageEvent<Float32Array>);
    expect(FakeSocket.last.sent).toHaveLength(1);
    stream.stop();
  });

  it("buffers a delayed ready message and drains frames as results arrive", () => {
    const onError = vi.fn();
    const stream = new LiveStream({ onPartial: vi.fn(), onError }, {
      WebSocketClass: FakeSocket as unknown as typeof WebSocket,
    });
    stream.connect();
    const internal = stream as unknown as { queue: (frame: ArrayBuffer) => void };
    for (let i = 0; i < 5; i++) internal.queue(new ArrayBuffer(15_360));
    expect(onError).not.toHaveBeenCalled();
    FakeSocket.last.receive({ type: "ready" });
    expect(FakeSocket.last.sent).toHaveLength(3);
    FakeSocket.last.receive({ type: "partial", seq: 1, text: "" });
    FakeSocket.last.receive({ type: "partial", seq: 2, text: "" });
    expect(FakeSocket.last.sent).toHaveLength(5);
    stream.stop();
  });
  it("reports a connection setup failure so final recording can continue", async () => {
    class BrokenSocket { constructor() { throw new Error("blocked"); } }
    const onError = vi.fn();
    const stream = new LiveStream({ onPartial: vi.fn(), onError }, {
      WebSocketClass: BrokenSocket as unknown as typeof WebSocket,
    });
    await stream.start({} as MediaStream);
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: "浏览器无法启动实时音频处理" }));
  });

  it("appends ordered partial text and ignores messages after stop", () => {
    const onPartial = vi.fn();
    const onReady = vi.fn();
    const stream = new LiveStream({ onPartial, onReady, onError: vi.fn() }, {
      WebSocketClass: FakeSocket as unknown as typeof WebSocket,
      now: () => 1_000,
    });
    stream.connect();
    FakeSocket.last.receive({ type: "ready" });
    expect(onReady).toHaveBeenCalledTimes(1);
    FakeSocket.last.receive({ type: "partial", seq: 1, text: "今天" });
    FakeSocket.last.receive({ type: "partial", seq: 2, text: "开会" });
    expect(onPartial).toHaveBeenLastCalledWith("今天开会", expect.any(Number));
    stream.stop();
    FakeSocket.last.receive({ type: "partial", seq: 3, text: "错误" });
    expect(onPartial).toHaveBeenCalledTimes(2);
  });

  it("emits a segment when the server cuts a final segment and restarts accumulation", () => {
    const onPartial = vi.fn();
    const onSegment = vi.fn();
    const stream = new LiveStream({ onPartial, onSegment, onError: vi.fn() }, {
      WebSocketClass: FakeSocket as unknown as typeof WebSocket,
    });
    stream.connect();
    FakeSocket.last.receive({ type: "ready" });
    FakeSocket.last.receive({ type: "partial", seq: 1, text: "第一个" });
    FakeSocket.last.receive({ type: "final", segment_id: "utt_local_stream_a", text: "第一个分支", language: "zh", start_offset_ms: 0, end_offset_ms: 1_200 });
    expect(onSegment).toHaveBeenCalledWith({ segmentId: "utt_local_stream_a", text: "第一个分支", language: "zh", startOffsetMs: 0, endOffsetMs: 1_200 });
    FakeSocket.last.receive({ type: "partial", seq: 2, text: "第二个" });
    expect(onPartial).toHaveBeenLastCalledWith("第二个", expect.any(Number));
    stream.stop();
  });

  it("flushes remaining text on finish and closes after the server acknowledges", async () => {
    const stream = new LiveStream({ onPartial: vi.fn(), onError: vi.fn() }, {
      WebSocketClass: FakeSocket as unknown as typeof WebSocket,
    });
    stream.connect();
    FakeSocket.last.receive({ type: "ready" });
    const done = stream.finish();
    expect(FakeSocket.last.sent).toContain(JSON.stringify({ type: "flush" }));
    FakeSocket.last.receive({ type: "flushed" });
    await done;
    expect(FakeSocket.last.readyState).toBe(3);
  });

  it("reconnects with the same session and replays only frames the server did not acknowledge", async () => {
    vi.useFakeTimers();
    const onError = vi.fn();
    const stream = new LiveStream({ onPartial: vi.fn(), onError }, {
      WebSocketClass: FakeSocket as unknown as typeof WebSocket,
      sessionId: "client_session_0123456789abcdef",
    });
    stream.connect();
    const first = FakeSocket.last;
    expect(new URL(first.url).searchParams.get("session_id")).toBe("client_session_0123456789abcdef");
    first.receive({ type: "ready", seq: 0, partial_text: "" });
    const internal = stream as unknown as { queue: (frame: ArrayBuffer) => void };
    const acknowledged = new ArrayBuffer(15_360);
    const replayed = new ArrayBuffer(15_360);
    internal.queue(acknowledged);
    first.receive({ type: "partial", seq: 1, text: "已确认" });
    internal.queue(replayed);
    expect(first.sent).toContain(replayed);
    first.drop();

    await vi.advanceTimersByTimeAsync(400);
    const second = FakeSocket.last;
    second.receive({ type: "ready", seq: 1, partial_text: "已确认" });
    expect(second.sent).toContain(replayed);
    expect(second.sent).not.toContain(acknowledged);
    second.receive({ type: "partial", seq: 2, text: "恢复后" });
    expect(onError).not.toHaveBeenCalled();
    stream.stop();
  });

  it("acknowledges a replayed final segment without submitting it twice", () => {
    const onSegment = vi.fn();
    const stream = new LiveStream({ onPartial: vi.fn(), onSegment, onError: vi.fn() }, {
      WebSocketClass: FakeSocket as unknown as typeof WebSocket,
      sessionId: "client_session_0123456789abcdef",
    });
    stream.connect();
    const first = FakeSocket.last;
    first.receive({ type: "ready", seq: 0, partial_text: "" });
    const final = { type: "final", seq: 0, segment_id: "stable_segment_1", text: "稳定分段", language: "zh", start_offset_ms: 0, end_offset_ms: 480 };
    first.receive(final);
    expect(first.sent).toContain(JSON.stringify({ type: "ack", segment_id: "stable_segment_1" }));
    first.drop();
    stream.connect();
    const resumed = FakeSocket.last;
    resumed.receive({ type: "ready", seq: 0, partial_text: "" });
    resumed.receive(final);
    expect(onSegment).toHaveBeenCalledTimes(1);
    expect(resumed.sent).toContain(JSON.stringify({ type: "ack", segment_id: "stable_segment_1" }));
    stream.stop();
  });

  it("reports an ASR process restart while keeping the live stream recoverable", async () => {
    vi.useFakeTimers();
    const onError = vi.fn();
    const stream = new LiveStream({ onPartial: vi.fn(), onError }, {
      WebSocketClass: FakeSocket as unknown as typeof WebSocket,
      sessionId: "client_session_0123456789abcdef",
    });
    stream.connect();
    FakeSocket.last.receive({ type: "ready", seq: 3, generation: "process-a", partial_text: "旧进程文字" });
    FakeSocket.last.drop();
    await vi.advanceTimersByTimeAsync(400);
    FakeSocket.last.receive({ type: "ready", seq: 0, generation: "process-b", partial_text: "" });
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining("已重启") }));
    expect((stream as unknown as { stopped: boolean }).stopped).toBe(false);
    stream.stop();
  });
});
