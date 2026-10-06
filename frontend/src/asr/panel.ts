import { getModelStatus, getProductStatus, submitUtterance, transcribeAudio } from "./api";
import type { NormUtterance } from "./event";
import { LiveStream, type LiveSegment } from "./live-stream";
import { RecorderController, type RecorderState } from "./recorder";

function required<T extends HTMLElement>(root: HTMLElement, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (!element) throw new Error(`缺少语音面板控件：${selector}`);
  return element;
}

export function mountAsrPanel(root: HTMLElement, meetingId: string): { dispose(): void } {
  const record = required<HTMLButtonElement>(root, "#asr-record");
  const pause = required<HTMLButtonElement>(root, "#asr-pause");
  const resume = required<HTMLButtonElement>(root, "#asr-resume");
  const stop = required<HTMLButtonElement>(root, "#asr-stop");
  const retry = required<HTMLButtonElement>(root, "#asr-retry");
  const file = required<HTMLInputElement>(root, "#asr-file");
  const timer = required<HTMLOutputElement>(root, "#asr-timer");
  const modelStatus = required<HTMLOutputElement>(root, "#asr-model-status");
  const llmMode = required<HTMLOutputElement>(root, "#llm-mode");
  const transcript = required<HTMLElement>(root, "#asr-transcript");
  const message = required<HTMLElement>(root, "#asr-message");

  let disposed = false;
  let recorderState: RecorderState = "idle";
  let busy = false;
  let pendingEvent: NormUtterance | null = null;
  let sequence = 0;
  let elapsedSeconds = 0;
  let clock: number | null = null;
  let modelPoll: number | null = null;
  let live: LiveStream | null = null;
  let liveSegments = 0;
  let liveFlush: Promise<void> | null = null;
  const listeners: Array<() => void> = [];

  function listen(element: HTMLElement, type: string, handler: EventListener): void {
    element.addEventListener(type, handler);
    listeners.push(() => element.removeEventListener(type, handler));
  }
  function setMessage(text: string): void { message.textContent = text; }
  function showError(error: unknown): void {
    setMessage(error instanceof Error ? error.message : "操作失败，请重试");
    retry.hidden = !pendingEvent;
  }
  function renderTimer(): void {
    timer.textContent = `${String(Math.floor(elapsedSeconds / 60)).padStart(2, "0")}:${String(elapsedSeconds % 60).padStart(2, "0")}`;
  }
  function stopClock(): void {
    if (clock !== null) window.clearInterval(clock);
    clock = null;
  }
  function syncControls(): void {
    record.disabled = !recorder.supported || recorderState !== "idle" || busy || !!pendingEvent;
    pause.disabled = recorderState !== "recording" || busy;
    resume.disabled = recorderState !== "paused" || busy;
    stop.disabled = !["recording", "paused"].includes(recorderState) || busy;
    file.disabled = busy || recorderState !== "idle" || !!pendingEvent;
    retry.disabled = busy;
  }

  async function submitPendingEvent(): Promise<void> {
    if (!pendingEvent || disposed) return;
    busy = true;
    retry.hidden = true;
    setMessage("正在送往会议看板…");
    syncControls();
    try {
      await submitUtterance(pendingEvent);
      if (disposed) return;
      pendingEvent = null;
      setMessage("已送达会议看板");
    } catch (error) {
      if (!disposed) showError(error);
    } finally {
      busy = false;
      if (!disposed) syncControls();
    }
  }

  async function processAudio(blob: Blob): Promise<void> {
    if (disposed || busy || pendingEvent) return;
    busy = true;
    retry.hidden = true;
    setMessage("正在识别语音…");
    syncControls();
    try {
      const result = await transcribeAudio(blob, meetingId, ++sequence);
      if (disposed) return;
      pendingEvent = result.event;
      transcript.textContent = result.event.text;
      await submitPendingEvent();
    } catch (error) {
      if (!disposed) showError(error);
    } finally {
      busy = false;
      if (!disposed) syncControls();
    }
  }

  /** 实时分段：静音切出的一段直接提交看板，不阻塞录音。 */
  async function submitLiveSegment(segment: LiveSegment): Promise<void> {
    if (disposed || !segment.text.trim()) return;
    const event: NormUtterance = {
      utterance_id: segment.segmentId,
      meeting_id: meetingId,
      session_id: null,
      seq: ++sequence,
      speaker: { speaker_ref: "local:user", display_name: "本地发言人", is_resolved: false },
      text: segment.text,
      language: segment.language || "auto",
      start_offset_ms: Math.max(0, segment.startOffsetMs),
      end_offset_ms: Math.max(0, segment.endOffsetMs),
      received_at_ms: Date.now(),
      is_final: true,
      is_partial: false,
      source: "local_streaming",
    };
    try {
      await submitUtterance(event);
      if (!disposed) setMessage("已送达会议看板");
    } catch (error) {
      if (!disposed) showError(error);
    }
  }

  /** 录音结束：等实时通道收尾；若实时通道未产出任何分段，再用整段录音兜底识别。 */
  async function handleComplete(blob: Blob): Promise<void> {
    const pending = liveFlush;
    if (pending) { try { await pending; } catch { /* 收尾失败不阻断整段兜底 */ } }
    if (disposed || liveSegments > 0) return;
    await processAudio(blob);
  }

  const recorder = new RecorderController({
    onState: (state) => {
      if (disposed) return;
      const previous = recorderState;
      recorderState = state;
      if (state === "recording") {
        if (previous === "paused") live?.resume();
        if (clock === null) clock = window.setInterval(() => { elapsedSeconds += 1; renderTimer(); }, 1000);
      } else {
        stopClock();
        if (state === "paused") live?.pause();
        if (state === "stopping" || state === "idle") {
          const session = live;
          live = null;
          if (session) liveFlush = session.finish();
        }
      }
      syncControls();
    },
    onStream: (stream) => {
      if (disposed) return;
      live?.stop();
      liveSegments = 0;
      liveFlush = null;
      transcript.textContent = "";
      setMessage("正在连接实时识别…");
      const session = new LiveStream({
        onReady: () => {
          if (!disposed && live === session) setMessage("实时识别已连接，开始说话即可显示文字");
        },
        onPartial: (text, latencyMs) => {
          if (disposed || live !== session || recorderState !== "recording") return;
          transcript.textContent = text;
          setMessage(`实时转写中 · 音频块响应约 ${latencyMs} ms`);
        },
        onSegment: (segment) => {
          if (disposed) return;
          liveSegments += 1;
          transcript.textContent = segment.text;
          setMessage("已切出一段，正在送往会议看板…");
          void submitLiveSegment(segment);
        },
        onError: (error) => {
          if (!disposed && live === session) setMessage(`${error.message}；停止后仍会识别完整录音`);
        },
      });
      live = session;
      void session.start(stream);
    },
    onComplete: (blob) => { void handleComplete(blob); },
    onError: (error) => { if (!disposed) showError(error); },
  });

  modelStatus.textContent = "正在检查本地 ASR…";
  llmMode.textContent = "正在检查看板模式…";
  async function refreshModelStatus(): Promise<void> {
    let shouldPoll = true;
    try {
      const status = await getModelStatus();
      if (disposed) return;
      const ready = status.state === "ready";
      shouldPoll = !ready || status.streaming_state === "loading" || status.streaming_state === "not_loaded";
      const streaming = status.streaming_state === "ready" ? "实时识别已就绪"
        : status.streaming_state === "loading" ? "实时识别模型加载中"
          : status.streaming_state === "error" ? "实时识别模型不可用" : "实时识别未加载";
      modelStatus.textContent = ready ? `本地 ASR 已就绪${status.device ? ` · ${status.device}` : ""} · ${streaming}`
        : status.state === "loading" ? "本地 ASR 加载中" : status.state === "error" ? `本地 ASR 不可用${status.error ? `：${status.error}` : ""}`
          : "本地 ASR 尚未加载";
    } catch (error) {
      if (disposed) return;
      modelStatus.textContent = `本地 ASR 不可用：${error instanceof Error ? error.message : "连接失败"}`;
    } finally {
      if (!disposed && shouldPoll) modelPoll = window.setTimeout(() => { void refreshModelStatus(); }, 5000);
    }
  }
  void refreshModelStatus();
  void getProductStatus().then((status) => {
    if (!disposed) llmMode.textContent = status.llm_mode === "mock" ? "看板分析：演示模式" : "看板分析：已配置模型";
  }).catch(() => { if (!disposed) llmMode.textContent = "看板分析：状态未知"; });

  listen(record, "click", () => {
    if (busy) return;
    elapsedSeconds = 0; renderTimer();
    void recorder.start();
  });
  listen(pause, "click", () => recorder.pause());
  listen(resume, "click", () => recorder.resume());
  listen(stop, "click", () => recorder.stop());
  listen(retry, "click", () => { void submitPendingEvent(); });
  listen(file, "change", () => {
    const selected = file.files?.[0];
    if (selected) void processAudio(selected);
    file.value = "";
  });
  syncControls();
  return { dispose(): void {
    if (disposed) return;
    disposed = true;
    listeners.forEach((remove) => remove());
    stopClock();
    if (modelPoll !== null) window.clearTimeout(modelPoll);
    live?.stop();
    live = null;
    recorder.dispose();
  } };
}
