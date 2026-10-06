import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { MascotController, ERROR_FALLBACK_MS, resolveMascotMotion, SUPPORTED_MASCOT_MOTIONS } from "./MascotController";

const model3 = JSON.parse(readFileSync("public/mascot/live2d/c_0120.model3.json", "utf8"));
const supported = Object.keys(model3.FileReferences.Motions) as string[];

describe("MascotController", () => {
  it("keeps its supported motion list synchronized with the bundled model", () => {
    expect(SUPPORTED_MASCOT_MOTIONS).toEqual(supported);
  });

  it("maps meeting states only to motion groups declared by the bundled model", () => {
    for (const state of ["idle", "listening", "filtering", "loading_board", "assembling", "analyzing", "syncing", "success", "error"]) {
      const motion = resolveMascotMotion(state, supported);
      expect(motion === undefined || supported.includes(motion)).toBe(true);
    }
    expect(resolveMascotMotion("listening", supported)).toBe("Idle");
    expect(resolveMascotMotion("success", supported)).toBe("BubbleGum");
    expect(resolveMascotMotion("error", supported)).toBe("SprayWater");
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
