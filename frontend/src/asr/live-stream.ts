export interface LiveSegment {
  segmentId: string;
  text: string;
  language: string;
  startOffsetMs: number;
  endOffsetMs: number;
}

export interface LiveCallbacks {
  onReady?: () => void;
  onPartial: (text: string, latencyMs: number) => void;
  onSegment?: (segment: LiveSegment) => void;
  onError: (error: Error) => void;
}

export class PcmChunker {
  private readonly output = new DataView(new ArrayBuffer(15_360));
  private outputSamples = 0;
  private sourceIndex = 0;
  private nextOutputIndex = 0;
  private previous = 0;
  private paused = false;

  constructor(
    private readonly inputRate: number,
    private readonly onFrame: (frame: ArrayBuffer) => void,
  ) {}

  push(input: Float32Array): void {
    if (this.paused) return;
    for (const current of input) {
      const position = this.sourceIndex++;
      while (this.nextOutputIndex <= position) {
        const fraction = position === 0 ? 0 : this.nextOutputIndex - (position - 1);
        const sample = position === 0 ? current : this.previous + (current - this.previous) * fraction;
        const clamped = Math.max(-1, Math.min(1, sample));
        this.output.setInt16(this.outputSamples * 2, Math.round(clamped * (clamped < 0 ? 32_768 : 32_767)), true);
        this.outputSamples += 1;
        if (this.outputSamples === 7_680) {
          this.onFrame(this.output.buffer.slice(0));
          this.outputSamples = 0;
        }
        this.nextOutputIndex += this.inputRate / 16_000;
      }
      this.previous = current;
    }
  }

  pause(): void { this.paused = true; }
  resume(): void { this.paused = false; }
}

interface LiveDependencies {
  WebSocketClass?: typeof WebSocket;
  now?: () => number;
}

export class LiveStream {
  private socket: WebSocket | null = null;
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private node: AudioWorkletNode | null = null;
  private chunker: PcmChunker | null = null;
  private pending: Array<{ frame: ArrayBuffer; capturedAt: number }> = [];
  private sentAt = new Map<number, number>();
  private sentSeq = 0;
  private ready = false;
  private paused = false;
  private stopped = false;
  private failed = false;
  private text = "";
  private lastSeq = 0;
  private flushResolve: (() => void) | null = null;
  private flushTimer: number | null = null;
  private readonly WebSocketClass: typeof WebSocket;
  private readonly now: () => number;

  constructor(private readonly callbacks: LiveCallbacks, dependencies: LiveDependencies = {}) {
    this.WebSocketClass = dependencies.WebSocketClass || WebSocket;
    this.now = dependencies.now || (() => performance.now());
  }

  connect(): void {
    if (this.socket || this.stopped) return;
    const url = new URL("/asr/v1/audio/stream", window.location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const socket = new this.WebSocketClass(url.toString());
    this.socket = socket;
    socket.addEventListener("message", (event: MessageEvent) => {
      if (this.stopped || this.failed) return;
      let payload: Record<string, unknown>;
      try { payload = JSON.parse(String(event.data)) as Record<string, unknown>; }
      catch { this.fail("实时识别响应无效"); return; }
      if (payload.type === "ready") {
        this.ready = true;
        this.callbacks.onReady?.();
        this.flushPending();
      } else if (payload.type === "error") {
        this.fail(typeof payload.message === "string" ? payload.message : "实时识别失败");
      } else if (payload.type === "partial" && typeof payload.text === "string" && typeof payload.seq === "number") {
        if (payload.seq <= this.lastSeq) return;
        this.lastSeq = payload.seq;
        const capturedAt = this.sentAt.get(payload.seq) ?? this.now();
        for (const seq of this.sentAt.keys()) if (seq <= payload.seq) this.sentAt.delete(seq);
        this.flushPending();
        if (!payload.text.trim()) return;
        const separator = /[a-zA-Z0-9]$/.test(this.text) && /^[a-zA-Z0-9]/.test(payload.text) ? " " : "";
        this.text += separator + payload.text;
        this.callbacks.onPartial(this.text, Math.round(this.now() - capturedAt));
      } else if (payload.type === "final" && typeof payload.text === "string" && typeof payload.segment_id === "string") {
        this.text = "";
        this.callbacks.onSegment?.({
          segmentId: payload.segment_id,
          text: payload.text,
          language: typeof payload.language === "string" ? payload.language : "auto",
          startOffsetMs: typeof payload.start_offset_ms === "number" ? payload.start_offset_ms : 0,
          endOffsetMs: typeof payload.end_offset_ms === "number" ? payload.end_offset_ms : 0,
        });
      } else if (payload.type === "flushed") {
        if (this.flushResolve) this.endFlush();
      }
    });
    socket.addEventListener("error", () => this.fail("实时识别连接失败"));
    socket.addEventListener("close", () => { if (!this.stopped && !this.failed) this.fail("实时识别连接已断开"); });
  }

  async start(stream: MediaStream): Promise<void> {
    try {
      this.connect();
      const context = new AudioContext();
      this.context = context;
      await context.audioWorklet.addModule("/worklets/asr-capture.js");
      if (this.stopped) { await context.close(); return; }
      this.chunker = new PcmChunker(context.sampleRate, (frame) => this.queue(frame));
      if (this.paused) this.chunker.pause();
      this.source = context.createMediaStreamSource(stream);
      this.node = new AudioWorkletNode(context, "asr-capture");
      this.node.port.onmessage = (event: MessageEvent<Float32Array>) => this.chunker?.push(event.data);
      this.source.connect(this.node);
      this.node.connect(context.destination);
      await context.resume();
    } catch {
      if (!this.stopped) this.fail("浏览器无法启动实时音频处理");
    }
  }

  pause(): void { this.paused = true; this.chunker?.pause(); }
  resume(): void { this.paused = false; this.chunker?.resume(); }

  /** 收尾：请服务端把最后未定型的文本切成一段（final），收到 flushed 后关闭。 */
  finish(timeoutMs = 1500): Promise<void> {
    if (this.stopped || !this.socket || this.socket.readyState !== 1) {
      this.stop();
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      this.flushResolve = resolve;
      this.flushTimer = window.setTimeout(() => this.endFlush(), timeoutMs);
      try {
        this.socket!.send(JSON.stringify({ type: "flush" }));
      } catch {
        this.endFlush();
      }
    });
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.pending = [];
    this.node?.disconnect();
    if (this.node) this.node.port.onmessage = null;
    this.source?.disconnect();
    void this.context?.close();
    this.socket?.close();
  }

  private endFlush(): void {
    if (this.flushTimer !== null) { window.clearTimeout(this.flushTimer); this.flushTimer = null; }
    const resolve = this.flushResolve;
    this.flushResolve = null;
    this.stop();
    resolve?.();
  }

  private queue(frame: ArrayBuffer): void {
    if (this.stopped || this.failed) return;
    const capturedAt = this.now() - 480;
    if (this.pending.length >= 10) { this.fail("实时识别处理跟不上录音"); return; }
    this.pending.push({ frame, capturedAt });
    if (this.ready) this.flushPending();
  }

  private flushPending(): void {
    if (!this.ready || !this.socket || this.socket.readyState !== 1) return;
    while (this.pending.length && this.sentSeq - this.lastSeq < 3) {
      if (this.socket.bufferedAmount > 30_720) return;
      const next = this.pending.shift();
      if (!next) return;
      this.sentSeq += 1;
      this.sentAt.set(this.sentSeq, next.capturedAt);
      this.socket.send(next.frame);
    }
  }

  private fail(message: string): void {
    if (this.failed || this.stopped) return;
    this.failed = true;
    this.callbacks.onError(new Error(message));
    if (this.flushResolve) this.endFlush();
    else this.stop();
  }
}
