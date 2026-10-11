import { getModelStatus, getProductStatus, getUtteranceStatus, submitUtterance, transcribeAudio, type ProductStatus } from "./api";
import { meetingsApi } from "../api/meetings";
import type { NormUtterance } from "./event";
import { LiveStream, type LiveSegment } from "./live-stream";
import { RecorderController, type RecorderState } from "./recorder";
import { uncoveredTranscriptSegments } from "./transcript-reconcile";

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
  const exportPending = required<HTMLButtonElement>(root, "#asr-export");
  const reviewBeforeSubmit = required<HTMLInputElement>(root, "#asr-review-before-submit");
  const transcriptEdit = required<HTMLTextAreaElement>(root, "#asr-transcript-edit");
  const speakerReview = required<HTMLLabelElement>(root, "#asr-speaker-review");
  const speakerEdit = required<HTMLInputElement>(root, "#asr-speaker-edit");
  const confirmTranscript = required<HTMLButtonElement>(root, "#asr-confirm-transcript");
  const file = required<HTMLInputElement>(root, "#asr-file");
  const timer = required<HTMLOutputElement>(root, "#asr-timer");
  const modelStatus = required<HTMLOutputElement>(root, "#asr-model-status");
  const llmMode = required<HTMLOutputElement>(root, "#llm-mode");
  const productStatus = root.querySelector<HTMLOutputElement>("#product-status");
  const retryBatches = root.querySelector<HTMLButtonElement>("#asr-retry-batches");
  const transcript = required<HTMLElement>(root, "#asr-transcript");
  const reviewProgress = required<HTMLOutputElement>(root, "#asr-review-progress");
  const message = required<HTMLElement>(root, "#asr-message");

  let disposed = false;
  let recorderState: RecorderState = "idle";
  let busy = false;
  let batchRetrying = false;
  let pendingBatchCount = 0;
  const outboxKey = `amo:asr-outbox:${meetingId}`;
  const reviewKey = `amo:asr-review:${meetingId}`;
  let pendingEvents: NormUtterance[] = [];
  let reviewDrafts: NormUtterance[] = [];
  let reviewIndex = 0;
  let reviewReference = "";
  try {
    const saved = JSON.parse(localStorage.getItem(outboxKey) ?? "[]");
    if (Array.isArray(saved)) pendingEvents = saved.filter((item) => item?.meeting_id === meetingId && typeof item?.utterance_id === "string");
  } catch { /* Invalid or unavailable browser storage leaves an empty queue. */ }
  try {
    const saved = JSON.parse(localStorage.getItem(reviewKey) ?? "null");
    if (saved?.meeting_id === meetingId) {
      const validEvent = (item: any): item is NormUtterance => item?.meeting_id === meetingId
        && typeof item?.utterance_id === "string" && typeof item?.text === "string";
      if (Array.isArray(saved.events)) reviewDrafts = saved.events.filter(validEvent);
      else if (validEvent(saved)) reviewDrafts = [saved]; // Upgrade a saved single-event draft.
      if (reviewDrafts.length) {
        reviewIndex = Math.max(0, Math.min(reviewDrafts.length - 1,
          Number.isInteger(saved.index) ? saved.index : 0));
        reviewReference = typeof saved.reference_text === "string" ? saved.reference_text : "";
      }
    }
  } catch { /* Invalid or unavailable browser storage leaves no review draft. */ }
  let sequence = 0;
  let elapsedSeconds = 0;
  let clock: number | null = null;
  let modelPoll: number | null = null;
  let deliveryPoll: number | null = null;
  let live: LiveStream | null = null;
  let liveSegments = 0;
  let liveSegmentTexts: string[] = [];
  let liveError: string | null = null;
  let liveFlush: Promise<void> | null = null;
  let productAsrStatusReceived = false;
  const listeners: Array<() => void> = [];

  function listen(element: HTMLElement, type: string, handler: EventListener): void {
    element.addEventListener(type, handler);
    listeners.push(() => element.removeEventListener(type, handler));
  }
  function setMessage(text: string): void { message.textContent = text; }
  function committedMessage(effect: "linked" | "unlinked" | null | undefined): string {
    return effect === "linked" ? "原话已关联到看板" :
      "分段已处理，但没有入板；请核对转写并补录遗漏信息";
  }
  function renderPersistenceStatus(status: ProductStatus): void {
    pendingBatchCount = status.pending_by_meeting?.[meetingId] ?? 0;
    if (productStatus) {
      const a = status.persistence.store_a === "ready" ? "Store A 正常" : "Store A 不可用";
      const b = status.persistence.store_b === "ready" ? "Store B 正常" : "Store B 不可用";
      productStatus.textContent = `本地存储：${a} · ${b} · 当前会议待整理 ${pendingBatchCount} 批 · 全部会议 ${status.persistence.pending_retries} 批`;
    }
    if (retryBatches) {
      retryBatches.hidden = pendingBatchCount === 0;
      retryBatches.textContent = `重试整理 ${pendingBatchCount} 个积压批次`;
    }
  }
  function showError(error: unknown): void {
    setMessage(error instanceof Error ? error.message : "操作失败，请重试");
    retry.hidden = pendingEvents.length === 0;
  }
  function persistOutbox(): void {
    try { localStorage.setItem(outboxKey, JSON.stringify(pendingEvents)); } catch { /* Keep the current session usable if storage is full. */ }
  }
  function showReviewDraft(event: NormUtterance, status = "识别完成，请修订转写后再送入看板。") {
    showReviewDrafts([event], event.text, status);
  }
  function withReviewedSpeaker(event: NormUtterance): NormUtterance {
    const displayName = speakerEdit.value.trim();
    if (!displayName || displayName === event.speaker.display_name) return event;
    return { ...event, speaker: {
      speaker_ref: `local:${meetingId}:speaker:${encodeURIComponent(displayName)}`,
      display_name: displayName,
      is_resolved: true,
    } };
  }
  function persistReviewDrafts(): void {
    try {
      localStorage.setItem(reviewKey, JSON.stringify({ meeting_id: meetingId, events: reviewDrafts,
        index: reviewIndex, reference_text: reviewReference }));
    } catch { /* Editing remains available if local storage is unavailable. */ }
  }
  function showReviewDrafts(events: NormUtterance[], referenceText: string,
                            status = "识别完成，请逐段修订转写后再送入看板。") {
    reviewDrafts = events.filter((event) => event.meeting_id === meetingId && event.text.trim());
    reviewIndex = Math.max(0, Math.min(reviewIndex, reviewDrafts.length - 1));
    reviewReference = referenceText;
    if (!reviewDrafts.length) return;
    const current = reviewDrafts[reviewIndex];
    transcript.textContent = referenceText ? `完整录音核对参考：${referenceText}` : current.text;
    transcript.hidden = !referenceText || referenceText === current.text;
    reviewProgress.textContent = reviewDrafts.length > 1
      ? `核对第 ${reviewIndex + 1}/${reviewDrafts.length} 段`
      : current.source === "local_streaming" ? "核对实时分段 1/1" : "核对完整转写";
    speakerEdit.value = current.speaker.display_name;
    speakerReview.hidden = false;
    transcriptEdit.value = current.text;
    transcriptEdit.hidden = false;
    confirmTranscript.hidden = false;
    confirmTranscript.textContent = reviewIndex + 1 < reviewDrafts.length
      ? "保存本段并继续" : "确认并整理进看板";
    persistReviewDrafts();
    setMessage(status);
    syncControls();
  }
  function enqueue(event: NormUtterance): void {
    if (pendingEvents.some((item) => item.utterance_id === event.utterance_id)) return;
    pendingEvents.push(event);
    persistOutbox();
    exportPending.hidden = false;
    if (pendingEvents.length === 1) transcript.textContent = event.text;
  }
  function removeCommitted(event: NormUtterance): void {
    pendingEvents = pendingEvents.filter((item) => item.utterance_id !== event.utterance_id);
    persistOutbox();
    exportPending.hidden = pendingEvents.length === 0;
  }
  function renderTimer(): void {
    timer.textContent = `${String(Math.floor(elapsedSeconds / 60)).padStart(2, "0")}:${String(elapsedSeconds % 60).padStart(2, "0")}`;
  }
  function stopClock(): void {
    if (clock !== null) window.clearInterval(clock);
    clock = null;
  }
  function syncControls(): void {
    const processing = busy || batchRetrying;
    record.disabled = !recorder.supported || recorderState !== "idle" || processing
      || pendingEvents.length > 0 || reviewDrafts.length > 0;
    pause.disabled = recorderState !== "recording" || processing;
    resume.disabled = recorderState !== "paused" || processing;
    stop.disabled = !["recording", "paused"].includes(recorderState) || processing;
    file.disabled = processing || recorderState !== "idle" || pendingEvents.length > 0 || reviewDrafts.length > 0;
    reviewBeforeSubmit.disabled = processing || recorderState !== "idle" || pendingEvents.length > 0 || reviewDrafts.length > 0;
    retry.disabled = processing;
    confirmTranscript.disabled = processing || reviewDrafts.length === 0;
    speakerEdit.disabled = processing || reviewDrafts.length === 0;
    if (retryBatches) retryBatches.disabled = busy || batchRetrying;
  }

  function scheduleStatusCheck(event: NormUtterance, delay = 1200): void {
    if (deliveryPoll !== null) window.clearTimeout(deliveryPoll);
    deliveryPoll = window.setTimeout(() => { void checkPendingStatus(event); }, delay);
  }

  async function checkPendingStatus(event: NormUtterance): Promise<void> {
    if (disposed || !pendingEvents.some((item) => item.utterance_id === event.utterance_id)) return;
    try {
      const status = await getUtteranceStatus(event.utterance_id, meetingId);
      if (status.state === "committed") {
        removeCommitted(event);
        retry.hidden = true;
        setMessage(committedMessage(status.board_effect));
        syncControls();
        if (pendingEvents.length) void submitPendingEvent();
      } else if (status.state === "failed") {
        retry.hidden = false;
        setMessage(status.last_error || "整理失败，原始分段已保留，可重试");
      } else {
        setMessage(status.state === "accepted" ? "已安全接收，等待整理…" : "正在整理进看板…");
        scheduleStatusCheck(event);
      }
    } catch {
      setMessage("暂时无法确认处理状态，原始分段已保留");
      scheduleStatusCheck(event, 2500);
    }
  }

  async function submitPendingEvent(checkStatusFirst = false): Promise<void> {
    const current = pendingEvents[0];
    if (!current || disposed) return;
    busy = true;
    retry.hidden = true;
    setMessage("正在发送已保存分段…");
    syncControls();
    try {
      if (checkStatusFirst) {
        try {
          const status = await getUtteranceStatus(current.utterance_id, meetingId);
          if (status.state === "committed") {
            removeCommitted(current);
            setMessage(committedMessage(status.board_effect));
            return;
          }
          if (status.state === "accepted" || status.state === "processing") {
            setMessage(status.state === "accepted" ? "已安全接收，等待整理…" : "正在整理进看板…");
            scheduleStatusCheck(current);
            return;
          }
        } catch { /* Status lookup can fail during an outage; retry the same idempotent event below. */ }
      }
      const response = await submitUtterance(current);
      if (disposed) return;
      if (response.state === "committed") {
        removeCommitted(current);
        setMessage(committedMessage(response.board_effect));
        if (pendingEvents.length) void submitPendingEvent();
      } else if (response.state === "failed") {
        setMessage(response.error || "整理失败，原始分段已保留，可重试");
        retry.hidden = false;
      } else {
        setMessage(response.state === "accepted" ? "已安全接收，等待整理…" : "正在整理进看板…");
        scheduleStatusCheck(current);
      }
    } catch (error) {
      if (!disposed) showError(error);
    } finally {
      busy = false;
      if (!disposed) syncControls();
    }
  }

  async function processAudio(blob: Blob): Promise<void> {
    if (disposed || busy || pendingEvents.length > 0) return;
    busy = true;
    retry.hidden = true;
    setMessage("正在识别语音…");
    syncControls();
    try {
      const result = await transcribeAudio(blob, meetingId, ++sequence);
      if (disposed) return;
      transcript.textContent = result.event.text;
      if (reviewBeforeSubmit.checked) showReviewDraft(result.event);
      else {
        enqueue(result.event);
        await submitPendingEvent();
      }
    } catch (error) {
      if (!disposed) showError(error);
    } finally {
      busy = false;
      if (!disposed) syncControls();
    }
  }

  function liveSegmentEvent(segment: LiveSegment): NormUtterance {
    return {
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
  }

  /** Keep each streamed utterance separate for either review or direct ingestion. */
  async function submitLiveSegment(segment: LiveSegment): Promise<void> {
    if (disposed || !segment.text.trim()) return;
    const event = liveSegmentEvent(segment);
    if (reviewBeforeSubmit.checked) {
      reviewDrafts.push(event);
      reviewIndex = 0;
      persistReviewDrafts();
      transcript.textContent = liveSegmentTexts.join("\n");
      transcript.hidden = false;
      reviewProgress.textContent = `已识别 ${reviewDrafts.length} 段，停止后逐段核对`;
      setMessage(`已识别 ${reviewDrafts.length} 个实时分段；停止后可逐段修订，确认后按原顺序入板。`);
      return;
    }
    enqueue(event);
    if (!busy) void submitPendingEvent();
  }

  /** 录音结束：无实时分段时自动兜底；有部分分段且连接失败时只展示整段核对稿，避免重复入板。 */
  async function handleComplete(blob: Blob): Promise<void> {
    const pending = liveFlush;
    if (pending) { try { await pending; } catch { /* 收尾失败不阻断整段兜底 */ } }
    if (disposed) return;
    if (liveSegments === 0) {
      await processAudio(blob);
      return;
    }
    setMessage(`正在生成完整录音核对稿（已处理 ${liveSegments} 个实时分段）…`);
    try {
      const result = await transcribeAudio(blob, meetingId, ++sequence);
      if (disposed) return;
      transcript.textContent = result.event.text;
      if (reviewBeforeSubmit.checked) {
        const gaps = uncoveredTranscriptSegments(result.event.text, liveSegmentTexts);
        const status = gaps === null
          ? `完整转写已生成；${liveSegments} 个实时分段保留为独立发言。请对照完整参考逐段修订，并检查是否有遗漏。`
          : gaps.length
            ? `完整转写发现 ${gaps.length} 处内容未匹配到实时分段；请对照下方参考，在逐段核对时补全遗漏。`
            : "完整转写未发现明显遗漏；请逐段核对后按原顺序入板。";
        showReviewDrafts(reviewDrafts.length ? reviewDrafts : [result.event], result.event.text, status);
        return;
      }
      const uncovered = uncoveredTranscriptSegments(result.event.text, liveSegmentTexts);
      if (uncovered === null) {
        setMessage("完整录音仅供核对；无法与实时分段可靠对齐，请核对全文后补录遗漏。");
      } else if (!uncovered.length) {
        setMessage(`完整录音核对完成；${liveSegments} 个实时分段未检出明显遗漏，请核对转写准确性。`);
      } else {
        setMessage(`完整录音发现实时分段可能遗漏，建议补录：${uncovered.join("；")}`);
      }
    } catch (error) {
      if (!disposed) {
        const reason = error instanceof Error ? error.message : "识别失败";
        if (reviewBeforeSubmit.checked && reviewDrafts.length) {
          showReviewDrafts(reviewDrafts, "", `已保留 ${liveSegments} 个实时分段；完整录音核对失败（${reason}），仍可逐段修订并确认入板。`);
        } else {
          setMessage(`已保留 ${liveSegments} 个实时分段；完整录音核对失败：${reason}`);
        }
      }
    }
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
        if (state === "idle" && pendingEvents.length) {
          setMessage(`录音已停止，还有 ${pendingEvents.length} 条分段待确认；可重试发送或导出文本`);
          retry.hidden = false;
          exportPending.hidden = false;
        }
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
      liveSegmentTexts = [];
      liveError = null;
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
          liveSegmentTexts.push(segment.text);
          transcript.textContent = segment.text;
          setMessage("已切出一段，正在送往会议看板…");
          void submitLiveSegment(segment);
        },
        onError: (error) => {
          if (!disposed && live === session) {
            liveError = error.message;
            setMessage(liveSegments === 0
              ? `${error.message}；停止后会识别完整录音`
              : `${error.message}；已切分的 ${liveSegments} 个分段会保留，整段核对稿不会自动送入看板，避免重复。`);
          }
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
      const final = status.state === "ready" ? `终稿识别已就绪${status.device ? ` · ${status.device}` : ""}`
        : status.state === "loading" ? "终稿模型加载中"
          : status.state === "error" ? `终稿不可用${status.error ? `：${status.error}` : ""}`
            : "终稿模型未加载";
      const streaming = status.streaming_state === "ready" ? "实时识别已就绪"
        : status.streaming_state === "loading" ? "实时识别模型加载中"
          : status.streaming_state === "error" ? `实时识别不可用${status.streaming_error ? `：${status.streaming_error}` : ""}`
            : "实时识别未加载";
      shouldPoll = status.state !== "ready" || status.streaming_state === "loading" || status.streaming_state === "not_loaded";
      if (!productAsrStatusReceived) modelStatus.textContent = `本地 ASR：${final} · ${streaming}`;
    } catch (error) {
      if (disposed) return;
      if (!productAsrStatusReceived) modelStatus.textContent = `本地 ASR 不可用：${error instanceof Error ? error.message : "连接失败"}`;
    } finally {
      if (!disposed && shouldPoll) modelPoll = window.setTimeout(() => { void refreshModelStatus(); }, 5000);
    }
  }
  void refreshModelStatus();
  if (pendingEvents.length) {
    retry.hidden = false;
    exportPending.hidden = false;
    transcript.textContent = pendingEvents[0].text;
    setMessage("发现尚未确认的分段，正在检查处理状态…");
    void checkPendingStatus(pendingEvents[0]);
  }
  if (reviewDrafts.length) showReviewDrafts(reviewDrafts, reviewReference,
    "已恢复未确认的转写草稿；可以继续逐段修订并确认入板。");
  void getProductStatus().then((status) => {
    if (disposed) return;
    productAsrStatusReceived = true;
    const model = status.llm_instance.model ? ` · ${status.llm_instance.model}` : "";
    llmMode.textContent = status.llm_mode === "mock"
      ? "看板分析：Mock 演示模式，不调用真实模型"
      : status.llm_mode === "real" ? `看板分析：真实模型实例${model}` : "看板分析：模型客户端状态未知";
    const asrState = (state: string) => ({
      ready: "识别已就绪", loading: "识别加载中", not_loaded: "未加载",
      error: "不可用", unavailable: "服务不可用",
    }[state] || "状态未知");
    const finalError = status.asr.final.error ? `（${status.asr.final.error}）` : "";
    const streamError = status.asr.streaming.error ? `（${status.asr.streaming.error}）` : "";
    const device = status.asr.final.device ? ` · ${status.asr.final.device}` : "";
    modelStatus.textContent = `ASR 状态：终稿${asrState(status.asr.final.state)}${device}${finalError} · 实时${asrState(status.asr.streaming.state)}${streamError}`;
    renderPersistenceStatus(status);
  }).catch(() => {
    if (disposed) return;
    llmMode.textContent = "看板分析：状态未知";
    if (productStatus) productStatus.textContent = "本地存储与重试状态未知";
  });

  listen(record, "click", () => {
    if (busy) return;
    reviewDrafts = [];
    reviewIndex = 0;
    reviewReference = "";
    elapsedSeconds = 0; renderTimer();
    void recorder.start();
  });
  listen(pause, "click", () => recorder.pause());
  listen(resume, "click", () => recorder.resume());
  listen(stop, "click", () => recorder.stop());
  listen(retry, "click", () => { void submitPendingEvent(true); });
  listen(transcriptEdit, "input", () => {
    if (!reviewDrafts.length) return;
    reviewDrafts[reviewIndex] = { ...reviewDrafts[reviewIndex], text: transcriptEdit.value };
    persistReviewDrafts();
  });
  listen(speakerEdit, "input", () => {
    if (!reviewDrafts.length) return;
    reviewDrafts[reviewIndex] = withReviewedSpeaker(reviewDrafts[reviewIndex]);
    persistReviewDrafts();
  });
  listen(confirmTranscript, "click", () => {
    if (!reviewDrafts.length) return;
    const corrected = transcriptEdit.value.trim();
    if (!corrected) {
      setMessage("转写内容不能为空；修订后再确认入板。");
      return;
    }
    reviewDrafts[reviewIndex] = { ...withReviewedSpeaker(reviewDrafts[reviewIndex]), text: corrected };
    if (reviewIndex + 1 < reviewDrafts.length) {
      reviewIndex += 1;
      const next = reviewDrafts[reviewIndex];
      persistReviewDrafts();
      transcriptEdit.value = next.text;
      speakerEdit.value = next.speaker.display_name;
      reviewProgress.textContent = `核对第 ${reviewIndex + 1}/${reviewDrafts.length} 段`;
      confirmTranscript.textContent = reviewIndex + 1 < reviewDrafts.length
        ? "保存本段并继续" : "确认并整理进看板";
      setMessage("本段已保存，请核对下一段；全部确认后会按原顺序入板。");
      syncControls();
      return;
    }
    const confirmedEvents = reviewDrafts;
    reviewDrafts = [];
    reviewIndex = 0;
    reviewReference = "";
    try { localStorage.removeItem(reviewKey); } catch { /* The outbox still preserves the confirmed event. */ }
    transcriptEdit.hidden = true;
    speakerReview.hidden = true;
    confirmTranscript.hidden = true;
    reviewProgress.textContent = "";
    transcript.hidden = false;
    transcript.textContent = confirmedEvents.map((item) => item.text).join("\n");
    for (const event of confirmedEvents) enqueue(event);
    syncControls();
    void submitPendingEvent();
  });
  if (retryBatches) listen(retryBatches, "click", () => {
    if (batchRetrying || pendingBatchCount === 0) return;
    batchRetrying = true;
    syncControls();
    setMessage(`正在恢复 ${pendingBatchCount} 个积压语音批次…`);
    void meetingsApi.retryPending(meetingId).then(async (result) => {
      if (disposed) return;
      setMessage(result.still_failing
        ? `${result.recovered} 个批次已整理，${result.still_failing} 个仍失败；语音原文已保留，可稍后重试。`
        : `已恢复 ${result.recovered} 个积压语音批次并整理进看板。`);
      try {
        renderPersistenceStatus(await getProductStatus());
      } catch {
        // Status refresh is secondary; keep the recovery result visible.
      }
    }).catch((error) => {
      if (!disposed) setMessage(error instanceof Error ? error.message : "恢复失败；语音原文仍保存在本机");
    }).finally(() => {
      batchRetrying = false;
      if (!disposed) syncControls();
    });
  });
  listen(exportPending, "click", () => {
    if (!pendingEvents.length) return;
    const text = pendingEvents.map((event, index) =>
      `${index + 1}. [${event.start_offset_ms}–${event.end_offset_ms} ms] ${event.text}`,
    ).join("\n");
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${meetingId}-pending-utterances.txt`;
    anchor.click();
    URL.revokeObjectURL(url);
  });
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
    if (deliveryPoll !== null) window.clearTimeout(deliveryPoll);
    live?.stop();
    live = null;
    recorder.dispose();
  } };
}
