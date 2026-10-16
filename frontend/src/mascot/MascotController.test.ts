import { describe, expect, it, vi } from "vitest";
import { MascotController, ERROR_FALLBACK_MS, resolveMascotMotion, SUPPORTED_MASCOT_MOTIONS } from "./MascotController";

const supported = SUPPORTED_MASCOT_MOTIONS;

describe("MascotController", () => {
  it("maps meeting states only to supported portrait animations", () => {
    for (const state of ["idle", "listening", "filtering", "loading_board", "assembling", "analyzing", "syncing", "success", "error"]) {
      const motion = resolveMascotMotion(state, supported);
      expect(motion === undefined || supported.includes(motion)).toBe(true);
    }
    expect(resolveMascotMotion("listening", supported)).toBe("Idle");
    expect(resolveMascotMotion("success", supported)).toBe("Celebrate");
    expect(resolveMascotMotion("error", supported)).toBe("Concern");
  });

  it("uses idle and generic Chinese text for unknown backend states", () => {
    expect(resolveMascotMotion("unknown-backend-value", supported))
      .toBe(supported.includes("Idle") ? "Idle" : supported[0]);
    const visual = document.createElement("div");
    const bubble = document.createElement("div");
    const onMotion = vi.fn();
    const controller = new MascotController(visual, bubble, onMotion, supported);

    controller.setState("listening");
    expect(bubble.textContent).toBe("聆听转写…");
    expect(onMotion).toHaveBeenLastCalledWith("Idle");
    controller.setState("unknown-backend-value");
    expect(bubble.textContent).toBe("处理中…");
    expect(bubble.textContent).not.toContain("unknown-backend-value");
    expect(onMotion).toHaveBeenLastCalledWith("Idle");
  });

  it("auto-falls-back to idle after error so the warning does not stick forever", () => {
    vi.useFakeTimers();
    try {
      const visual = document.createElement("div");
      const bubble = document.createElement("div");
      const onMotion = vi.fn();
      const controller = new MascotController(visual, bubble, onMotion, supported);

      controller.setState("error");
      expect(bubble.textContent).toBe("出错了，已降级");
      expect(visual.style.opacity).toBe("0.6");
      vi.advanceTimersByTime(ERROR_FALLBACK_MS);
      expect(bubble.textContent).toBe("待命中");
      expect(visual.style.opacity).toBe("1");
      expect(onMotion).toHaveBeenLastCalledWith("Idle");
    } finally {
      vi.useRealTimers();
    }
  });

  it("cancels the error fallback once a newer state arrives", () => {
    vi.useFakeTimers();
    try {
      const visual = document.createElement("div");
      const bubble = document.createElement("div");
      const controller = new MascotController(visual, bubble, vi.fn(), supported);

      controller.setState("error");
      controller.setState("success");
      vi.advanceTimersByTime(ERROR_FALLBACK_MS + 1);
      expect(bubble.textContent).toBe("已更新看板 ✓");
    } finally {
      vi.useRealTimers();
    }
  });
});
