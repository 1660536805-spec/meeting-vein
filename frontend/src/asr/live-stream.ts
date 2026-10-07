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
  sessionId?: string;
}

export class LiveStream {
  private socket: WebSocket | null = null;
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private node: AudioWorkletNode | null = null;
  private chunker: PcmChunker | null = null;
  private pending: Array<{ frame: ArrayBuffer; capturedAt: number }> = [];
  private inFlight = new Map<number, { frame: ArrayBuffer; capturedAt: number }>();
  private sentAt = new Map<number, number>();
  private sentSeq = 0;
  private ready = false;
  private paused = false;
  private stopped = false;
  private failed = false;
  private text = "";
  private lastSeq = 0;
  private reconnectAttempts = 0;
  private reconnectTimer: number | null = null;
  private finishing = false;
  private flushSent = false;
  private flushResolve: (() => void) | null = null;
  private flushTimer: number | null = null;
  private readonly WebSocketClass: typeof WebSocket;
  private readonly now: () => number;
  private readonly sessionId: string;
  private readonly receivedSegments = new Set<string>();
  private serverGeneration: string | null = null;

  constructor(private readonly callbacks: LiveCallbacks, dependencies: LiveDependencies = {}) {
    this.WebSocketClass = dependencies.WebSocketClass || WebSocket;
    this.now = dependencies.now || (() => performance.now());
    this.sessionId = dependencies.sessionId || (typeof crypto?.randomUUID === "function"
      ? crypto.randomUUID().replace(/-/g, "")
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`);
  }

  connect(): void {
    if (this.socket || this.stopped || this.failed) return;
    const url = new URL("/asr/v1/audio/stream", window.location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.searchParams.set("session_id", this.sessionId);
    const socket = new this.WebSocketClass(url.toString());
    this.socket = socket;
    socket.addEventListener("message", (event: MessageEvent) => {
      if (this.stopped || this.failed) return;
      let payload: Record<string, unknown>;
      try { payload = JSON.parse(String(event.data)) as Record<string, unknown>; }
      catch { this.fail("实时识别响应无效"); return; }
      if (payload.type === "ready") {
        const generation = typeof payload.generation === "string" ? payload.generation : null;
        const serviceRestarted = this.serverGeneration !== null && generation !== null && this.serverGeneration !== generation;
        if (generation !== null) this.serverGeneration = generation;
        const serverSeq = typeof payload.seq === "number" && Number.isFinite(payload.seq) ? Math.max(0, payload.seq) : 0;
        const replay = [...this.inFlight.entries()]
          .filter(([seq]) => seq > serverSeq)
          .sort(([a], [b]) => a - b)
          .map(([, frame]) => frame);
        this.pending = [...replay, ...this.pending];
        this.inFlight.clear();
        this.sentAt.clear();
        this.sentSeq = serverSeq;
        this.lastSeq = serverSeq;
        this.text = typeof payload.partial_text === "string" ? payload.partial_text : "";
        this.ready = true;
        this.reconnectAttempts = 0;
        this.flushSent = false;
        this.callbacks.onReady?.();
        if (serviceRestarted) {
          this.callbacks.onError(new Error("本地 ASR 服务已重启；实时识别已恢复，停录后将提供整段核对稿"));
        }
        this.flushPending();
        this.maybeFlush();
      } else if (payload.type === "error") {
        this.reconnect(typeof payload.message === "string" ? payload.message : "实时识别失败", socket);
      } else if (payload.type === "partial" && typeof payload.text === "string" && typeof payload.seq === "number") {
        const isNewPartial = payload.seq > this.lastSeq;
        const capturedAt = this.sentAt.get(payload.seq) ?? this.now();
        this.acknowledgeFrames(payload.seq);
        if (!isNewPartial) return;
        if (!payload.text.trim()) return;
        const separator = /[a-zA-Z0-9]$/.test(this.text) && /^[a-zA-Z0-9]/.test(payload.text) ? " " : "";
        this.text += separator + payload.text;
        this.callbacks.onPartial(this.text, Math.round(this.now() - capturedAt));
      } else if (payload.type === "final" && typeof payload.text === "string" && typeof payload.segment_id === "string") {
        if (typeof payload.seq === "number") this.acknowledgeFrames(payload.seq);
        this.text = "";
        if (!this.receivedSegments.has(payload.segment_id)) {
          this.receivedSegments.add(payload.segment_id);
          this.callbacks.onSegment?.({
            segmentId: payload.segment_id,
            text: payload.text,
            language: typeof payload.language === "string" ? payload.language : "auto",
            startOffsetMs: typeof payload.start_offset_ms === "number" ? payload.start_offset_ms : 0,
            endOffsetMs: typeof payload.end_offset_ms === "number" ? payload.end_offset_ms : 0,
          });
        }
        this.sendControl({ type: "ack", segment_id: payload.segment_id });
      } else if (payload.type === "flushed") {
        if (this.flushResolve) this.endFlush();
      }
    });
    socket.addEventListener("error", () => this.reconnect("实时识别连接失败", socket));
    socket.addEventListener("close", () => this.reconnect("实时识别连接已断开", socket));
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
  finish(timeoutMs = 5000): Promise<void> {
    if (this.stopped || this.failed) {
      this.stop();
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      this.finishing = true;
      this.flushResolve = resolve;
      this.flushTimer = window.setTimeout(() => this.endFlush(), timeoutMs);
      this.flushPending();
      this.maybeFlush();
    });
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.pending = [];
    this.inFlight.clear();
    if (this.reconnectTimer !== null) { window.clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
    if (this.flushTimer !== null) { window.clearTimeout(this.flushTimer); this.flushTimer = null; }
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
    while (this.pending.length && this.inFlight.size < 3) {
      if (this.socket.bufferedAmount > 30_720) return;
      const next = this.pending.shift();
      if (!next) return;
      this.sentSeq += 1;
      this.sentAt.set(this.sentSeq, next.capturedAt);
      this.inFlight.set(this.sentSeq, next);
      try { this.socket.send(next.frame); }
      catch {
        this.inFlight.delete(this.sentSeq);
        this.sentAt.delete(this.sentSeq);
        this.sentSeq -= 1;
        this.pending.unshift(next);
        this.reconnect("实时识别连接发送失败", this.socket);
        return;
      }
    }
    this.maybeFlush();
  }

  private acknowledgeFrames(seq: number): void {
    if (!Number.isFinite(seq) || seq < 0) return;
    this.lastSeq = Math.max(this.lastSeq, seq);
    for (const pendingSeq of this.inFlight.keys()) {
      if (pendingSeq <= seq) this.inFlight.delete(pendingSeq);
    }
    for (const pendingSeq of this.sentAt.keys()) {
      if (pendingSeq <= seq) this.sentAt.delete(pendingSeq);
    }
    this.flushPending();
  }

  private sendControl(payload: Record<string, unknown>): void {
    if (!this.socket || this.socket.readyState !== 1) return;
    try { this.socket.send(JSON.stringify(payload)); }
    catch { this.reconnect("实时识别连接发送失败", this.socket); }
  }

  private maybeFlush(): void {
    if (!this.finishing || this.flushSent || !this.ready || this.pending.length || this.inFlight.size) return;
    this.flushSent = true;
    this.sendControl({ type: "flush" });
  }

  private reconnect(message: string, source?: WebSocket): void {
    if (this.stopped || this.failed || (source && this.socket !== source)) return;
    if (this.flushResolve && this.flushTimer === null) { this.endFlush(); return; }
    if (this.reconnectTimer !== null) return;
    const socket = this.socket;
    this.socket = null;
    this.ready = false;
    if (socket && socket.readyState < 2) {
      try { socket.close(); } catch { /* The remote end may already be gone. */ }
    }
    if (this.reconnectAttempts >= 3) { this.fail(message); return; }
    const delays = [400, 900, 1600];
    const delay = delays[this.reconnectAttempts++];
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      try { this.connect(); }
      catch { this.reconnect("实时识别重连失败"); }
    }, delay);
  }

  private fail(message: string): void {
    if (this.failed || this.stopped) return;
    this.failed = true;
    this.callbacks.onError(new Error(message));
    if (this.flushResolve) this.endFlush();
    else this.stop();
  }
}
