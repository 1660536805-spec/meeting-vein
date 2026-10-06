import { afterEach, describe, expect, it, vi } from "vitest";
import { Live2DMascotView, type Live2DHandle, type Live2DRuntime } from "./Live2DMascotView";

function fixture() {
  const canvas = document.createElement("canvas");
  const handle: Live2DHandle = {
    canvas,
    supportedMotions: ["Idle", "SprayWater"],
    setMotion: vi.fn(),
    lookAt: vi.fn(),
    dispose: vi.fn(),
  };
  const runtime: Live2DRuntime = { load: vi.fn().mockResolvedValue(handle) };
  const host = document.createElement("div");
  return { canvas, handle, runtime, host };
}

afterEach(() => vi.restoreAllMocks());

describe("Live2DMascotView", () => {
  it("mounts one canvas and forwards pointer gaze", async () => {
    const { canvas, handle, runtime, host } = fixture();
    const view = new Live2DMascotView(runtime);
    await view.mount(host);
    document.dispatchEvent(new MouseEvent("pointermove", { clientX: 12, clientY: 34 }));
    expect(host.querySelectorAll("canvas")).toHaveLength(1);
    expect(host.contains(canvas)).toBe(true);
    expect(handle.lookAt).toHaveBeenCalledWith(12, 34);
    view.dispose();
  });

  it("shows the static fallback when model loading rejects", async () => {
    const { runtime, host } = fixture();
    vi.mocked(runtime.load).mockRejectedValueOnce(new Error("Core unavailable"));
    const view = new Live2DMascotView(runtime);
    await expect(view.mount(host)).resolves.toBeUndefined();
    expect(host.querySelector<HTMLImageElement>("img")?.getAttribute("src"))
      .toBe("/mascot/fallback.svg");
    expect(host.querySelector<HTMLImageElement>("img")?.hidden).toBe(false);
  });

  it("applies the latest requested motion after asynchronous loading", async () => {
    const { handle, runtime, host } = fixture();
    let resolveLoad!: (value: Live2DHandle) => void;
    vi.mocked(runtime.load).mockReturnValueOnce(new Promise((resolve) => { resolveLoad = resolve; }));
    const view = new Live2DMascotView(runtime);
    const mounted = view.mount(host);
    view.setMotion("SprayWater");
    resolveLoad(handle);
    await mounted;
    expect(handle.setMotion).toHaveBeenCalledWith("SprayWater");
    view.dispose();
  });

  it("detaches pointer handling and destroys the renderer once", async () => {
    const { handle, host } = fixture();
    const view = new Live2DMascotView({ load: async () => handle });
    await view.mount(host);
    view.dispose();
    view.dispose();
    document.dispatchEvent(new MouseEvent("pointermove", { clientX: 1, clientY: 2 }));
    expect(handle.dispose).toHaveBeenCalledTimes(1);
    expect(handle.lookAt).toHaveBeenCalledTimes(0);
    expect(host.querySelector("canvas")).toBeNull();
    expect(host.querySelector<HTMLImageElement>("img")?.hidden).toBe(false);
  });

  it("follows page-wide pointer movement and resets gaze when the pointer leaves the page", async () => {
    const { handle, host } = fixture();
    const view = new Live2DMascotView({ load: async () => handle });
    await view.mount(host);
    document.dispatchEvent(new MouseEvent("pointermove", { clientX: 90, clientY: 120 }));
    expect(handle.lookAt).toHaveBeenLastCalledWith(90, 120);
    document.dispatchEvent(new MouseEvent("pointerleave"));
    expect(handle.lookAt).toHaveBeenLastCalledWith(0, 0);
    view.dispose();
  });

  it("mounts a fresh renderer after disposal", async () => {
    const { handle: first, runtime, host } = fixture();
    const second: Live2DHandle = {
      canvas: document.createElement("canvas"),
      supportedMotions: ["Idle"],
      setMotion: vi.fn(),
      lookAt: vi.fn(),
      dispose: vi.fn(),
    };
    vi.mocked(runtime.load).mockResolvedValueOnce(first).mockResolvedValueOnce(second);
    const view = new Live2DMascotView(runtime);
    await view.mount(host);
    view.dispose();
    await view.mount(host);
    expect(runtime.load).toHaveBeenCalledTimes(2);
    expect(host.querySelectorAll("canvas")).toHaveLength(1);
    expect(host.contains(second.canvas)).toBe(true);
    view.dispose();
  });

  it("discards an old pending renderer if disposed and remounted", async () => {
    const { runtime, host } = fixture();
    const stale: Live2DHandle = {
      canvas: document.createElement("canvas"), supportedMotions: ["Idle"],
      setMotion: vi.fn(), lookAt: vi.fn(), dispose: vi.fn(),
    };
    const current: Live2DHandle = {
      canvas: document.createElement("canvas"), supportedMotions: ["Idle"],
      setMotion: vi.fn(), lookAt: vi.fn(), dispose: vi.fn(),
    };
    let resolveFirst!: (handle: Live2DHandle) => void;
    let resolveSecond!: (handle: Live2DHandle) => void;
    vi.mocked(runtime.load)
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve; }))
      .mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve; }));
    const view = new Live2DMascotView(runtime);
    const firstMount = view.mount(host);
    view.dispose();
    const secondMount = view.mount(host);
    resolveFirst(stale);
    await firstMount;
    expect(stale.dispose).toHaveBeenCalledOnce();
    expect(host.contains(stale.canvas)).toBe(false);
    resolveSecond(current);
    await secondMount;
    expect(runtime.load).toHaveBeenCalledTimes(2);
    expect(host.querySelectorAll("canvas")).toHaveLength(1);
    expect(host.contains(current.canvas)).toBe(true);
    view.dispose();
  });
});
