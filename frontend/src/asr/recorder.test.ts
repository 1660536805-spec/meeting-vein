import { describe, expect, it, vi } from "vitest";
import { RecorderController, type RecorderState } from "./recorder";

class FakeMediaRecorder extends EventTarget {
  static last: FakeMediaRecorder;
  static isTypeSupported(type: string): boolean { return type === "audio/webm"; }

  readonly mimeType = "audio/webm";
  state: RecordingState = "inactive";
  startCount = 0;
  stopCount = 0;
  pauseCount = 0;
  resumeCount = 0;
  audio = new Blob(["audio"], { type: "audio/webm" });

  constructor(_stream: MediaStream) {
    super();
    FakeMediaRecorder.last = this;
  }

  start(): void { this.state = "recording"; this.startCount += 1; }
  pause(): void { this.state = "paused"; this.pauseCount += 1; }
  resume(): void { this.state = "recording"; this.resumeCount += 1; }
  stop(): void {
    this.state = "inactive";
    this.stopCount += 1;
    this.dispatchEvent(Object.assign(new Event("dataavailable"), { data: this.audio }));
    this.dispatchEvent(new Event("stop"));
  }
}

function setup(getUserMediaError?: Error) {
  const stopTrack = vi.fn();
  const stream = { getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream;
  const getUserMedia = vi.fn(async () => {
    if (getUserMediaError) throw getUserMediaError;
    return stream;
  }) as unknown as typeof navigator.mediaDevices.getUserMedia;
  const states: RecorderState[] = [];
  const onComplete = vi.fn();
  const onError = vi.fn();
  const recorder = new RecorderController(
    { onState: (state) => states.push(state), onComplete, onError },
    getUserMedia,
    FakeMediaRecorder as unknown as typeof MediaRecorder,
  );
  return { recorder, states, onComplete, onError, stopTrack, getUserMedia };
}

describe("RecorderController", () => {
  it("shares the same microphone stream with live transcription", async () => {
    const track = { stop: vi.fn() };
    const stream = { getTracks: () => [track] } as unknown as MediaStream;
    const onStream = vi.fn();
    const getUserMedia = vi.fn(async () => stream) as unknown as typeof navigator.mediaDevices.getUserMedia;
    const recorder = new RecorderController(
      { onState: vi.fn(), onStream, onComplete: vi.fn(), onError: vi.fn() },
      getUserMedia,
      FakeMediaRecorder as unknown as typeof MediaRecorder,
    );
    await recorder.start();
    expect(onStream).toHaveBeenCalledWith(stream);
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    recorder.dispose();
  });

  it("records once and releases microphone tracks on stop", async () => {
    const { recorder, states, onComplete, stopTrack } = setup();
    await recorder.start();
    recorder.pause();
    recorder.resume();
    recorder.stop();
    recorder.stop();

    expect(states).toEqual(["recording", "paused", "recording", "stopping", "idle"]);
    expect(FakeMediaRecorder.last.startCount).toBe(1);
    expect(FakeMediaRecorder.last.pauseCount).toBe(1);
    expect(FakeMediaRecorder.last.resumeCount).toBe(1);
    expect(FakeMediaRecorder.last.stopCount).toBe(1);
    expect(stopTrack).toHaveBeenCalledTimes(1);
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete.mock.calls[0][0].size).toBeGreaterThan(0);
  });

  it("does not start another recorder while already recording", async () => {
    const { recorder, getUserMedia } = setup();
    await recorder.start();
    await recorder.start();
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    recorder.dispose();
  });

  it("does not request the microphone twice while permission is pending", async () => {
    const stopTrack = vi.fn();
    const stream = { getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream;
    let grantPermission!: (stream: MediaStream) => void;
    const getUserMedia = vi.fn(() => new Promise<MediaStream>((resolve) => {
      grantPermission = resolve;
    })) as unknown as typeof navigator.mediaDevices.getUserMedia;
    const recorder = new RecorderController(
      { onState: vi.fn(), onComplete: vi.fn(), onError: vi.fn() },
      getUserMedia,
      FakeMediaRecorder as unknown as typeof MediaRecorder,
    );
    const first = recorder.start();
    const second = recorder.start();
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    grantPermission(stream);
    await Promise.all([first, second]);
    recorder.dispose();
    expect(stopTrack).toHaveBeenCalledTimes(1);
  });

  it("releases a stream granted after the controller was disposed", async () => {
    const stopTrack = vi.fn();
    const stream = { getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream;
    let grantPermission!: (stream: MediaStream) => void;
    const getUserMedia = vi.fn(() => new Promise<MediaStream>((resolve) => {
      grantPermission = resolve;
    })) as unknown as typeof navigator.mediaDevices.getUserMedia;
    const onComplete = vi.fn();
    const recorder = new RecorderController(
      { onState: vi.fn(), onComplete, onError: vi.fn() },
      getUserMedia,
      FakeMediaRecorder as unknown as typeof MediaRecorder,
    );
    const pending = recorder.start();
    recorder.dispose();
    grantPermission(stream);
    await pending;
    expect(stopTrack).toHaveBeenCalledTimes(1);
    expect(onComplete).not.toHaveBeenCalled();
    expect(recorder.currentState).toBe("idle");
  });

  it("reports an empty recording instead of submitting it", async () => {
    const { recorder, onComplete, onError, stopTrack } = setup();
    await recorder.start();
    FakeMediaRecorder.last.audio = new Blob([], { type: "audio/webm" });
    recorder.stop();
    expect(onComplete).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(stopTrack).toHaveBeenCalledTimes(1);
  });

  it("does not submit audio when disposed during recording", async () => {
    const { recorder, onComplete, stopTrack } = setup();
    await recorder.start();
    recorder.dispose();
    expect(stopTrack).toHaveBeenCalledTimes(1);
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("keeps the app usable after microphone permission is denied", async () => {
    const { recorder, onError, stopTrack } = setup(new Error("permission denied"));
    await recorder.start();
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: "permission denied" }));
    expect(recorder.currentState).toBe("idle");
    expect(stopTrack).not.toHaveBeenCalled();
  });

  it("reports an unsupported browser without requesting a microphone", async () => {
    const { onError, getUserMedia } = setup();
    const unsupported = new RecorderController(
      { onState: vi.fn(), onComplete: vi.fn(), onError },
      getUserMedia,
      undefined,
    );
    expect(unsupported.supported).toBe(false);
    await unsupported.start();
    expect(onError).toHaveBeenCalledWith(expect.any(Error));
    expect(getUserMedia).not.toHaveBeenCalled();
  });
});
