import { afterEach, describe, expect, it, vi } from "vitest";
import { MascotPortraitView } from "./MascotPortraitView";

const views: MascotPortraitView[] = [];
afterEach(() => {
  views.forEach(view => view.dispose());
  views.length = 0;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function fixture(reduced = false) {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({
    matches: reduced, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }));
  const host = document.createElement("div");
  vi.spyOn(host, "getBoundingClientRect").mockReturnValue({
    x: 100, y: 0, left: 100, top: 0, width: 118, height: 172, right: 218, bottom: 172, toJSON() {},
  });
  const view = new MascotPortraitView();
  views.push(view);
  view.mount(host);
  return { host, view };
}

describe("MascotPortraitView", () => {
  it("responds to page-wide movement and resets when the pointer leaves", () => {
    const { host } = fixture();
    document.dispatchEvent(new MouseEvent("pointermove", { clientX: 1000 }));
    expect(host.style.getPropertyValue("--mascot-x")).not.toBe("");
    expect(Math.abs(parseFloat(host.style.getPropertyValue("--mascot-x")))).toBeLessThanOrEqual(3);
    document.dispatchEvent(new MouseEvent("pointerleave"));
    expect(host.style.getPropertyValue("--mascot-x")).toBe("");
  });

  it("honors reduced motion preferences", () => {
    const { host } = fixture(true);
    document.dispatchEvent(new MouseEvent("pointermove", { clientX: 1000 }));
    expect(host.style.getPropertyValue("--mascot-x")).toBe("");
  });

  it("cleans up pointer listeners and supports remounting", () => {
    const { host, view } = fixture();
    view.dispose();
    view.dispose();
    document.dispatchEvent(new MouseEvent("pointermove", { clientX: 1000 }));
    expect(host.style.getPropertyValue("--mascot-x")).toBe("");
    expect(host.dataset.motion).toBeUndefined();
    view.mount(host);
    view.setMotion("Celebrate");
    expect(host.dataset.motion).toBe("Celebrate");
    view.setMotion("unknown");
    expect(host.dataset.motion).toBe("Idle");
  });
});
