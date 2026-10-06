export type RecorderState = "idle" | "recording" | "paused" | "stopping";

export interface RecorderCallbacks {
  onState: (state: RecorderState) => void;
  onStream?: (stream: MediaStream) => void;
  onComplete: (audio: Blob) => void;
  onError: (error: unknown) => void;
}

export type MediaRecorderConstructor = typeof MediaRecorder;

export class RecorderController {
  private recorder: MediaRecorder | null = null;
  private stream: MediaStream | null = null;
  private chunks: Blob[] = [];
  private state: RecorderState = "idle";
  private suppressCompletion = false;
  private starting = false;
  private generation = 0;

  constructor(
    private readonly callbacks: RecorderCallbacks,
    private readonly getUserMedia: typeof navigator.mediaDevices.getUserMedia =
      navigator.mediaDevices?.getUserMedia.bind(navigator.mediaDevices),
    private readonly MediaRecorderClass: MediaRecorderConstructor | undefined =
      typeof MediaRecorder === "undefined" ? undefined : MediaRecorder,
  ) {}

  get currentState(): RecorderState { return this.state; }
  get supported(): boolean { return Boolean(this.MediaRecorderClass && this.getUserMedia); }

  async start(): Promise<void> {
    if (!this.supported || !this.getUserMedia || !this.MediaRecorderClass) {
      this.callbacks.onError(new Error("当前浏览器不支持麦克风录音，请上传音频文件"));
      return;
    }
    if (this.state !== "idle" || this.starting) return;

    this.starting = true;
    const generation = this.generation;
    try {
      const stream = await this.getUserMedia({ audio: true });
      if (generation !== this.generation) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream = stream;
      const mimeType = this.chooseMimeType(this.MediaRecorderClass);
      this.recorder = mimeType
        ? new this.MediaRecorderClass(this.stream, { mimeType })
        : new this.MediaRecorderClass(this.stream);
      this.chunks = [];
      this.suppressCompletion = false;
      this.recorder.addEventListener("dataavailable", this.collectChunk);
      this.recorder.addEventListener("stop", this.finishRecording, { once: true });
      this.recorder.start();
      this.callbacks.onStream?.(stream);
      this.setState("recording");
    } catch (error) {
      this.stopTracks();
      if (generation === this.generation) this.callbacks.onError(error);
    } finally {
      this.starting = false;
    }
  }

  pause(): void {
    if (this.state !== "recording" || !this.recorder) return;
    this.recorder.pause();
    this.setState("paused");
  }

  resume(): void {
    if (this.state !== "paused" || !this.recorder) return;
    this.recorder.resume();
    this.setState("recording");
  }

  stop(): void {
    if (!this.recorder || (this.state !== "recording" && this.state !== "paused")) return;
    this.setState("stopping");
    this.recorder.stop();
  }

  dispose(): void {
    this.generation += 1;
    this.suppressCompletion = true;
    if (this.recorder && this.recorder.state !== "inactive") this.recorder.stop();
    this.stopTracks();
    this.recorder = null;
    this.chunks = [];
    this.setState("idle");
  }

  private readonly collectChunk = (event: Event): void => {
    const data = (event as BlobEvent).data;
    if (data && data.size > 0) this.chunks.push(data);
  };

  private readonly finishRecording = (): void => {
    const audio = new Blob(this.chunks, { type: this.recorder?.mimeType || "audio/webm" });
    this.stopTracks();
    this.recorder = null;
    this.chunks = [];
    this.setState("idle");
    if (this.suppressCompletion) return;
    if (audio.size > 0) this.callbacks.onComplete(audio);
    else this.callbacks.onError(new Error("录音没有产生有效音频"));
  };

  private stopTracks(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }

  private setState(state: RecorderState): void {
    if (this.state === state) return;
    this.state = state;
    this.callbacks.onState(state);
  }

  private chooseMimeType(Recorder: MediaRecorderConstructor): string | undefined {
    if (typeof Recorder.isTypeSupported !== "function") return undefined;
    return ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((type) =>
      Recorder.isTypeSupported(type));
  }
}
