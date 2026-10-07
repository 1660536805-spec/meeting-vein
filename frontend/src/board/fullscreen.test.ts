import { afterEach, describe, expect, it, vi } from "vitest";
import { mountFullscreenToggle } from "./fullscreen";

afterEach(() => {
  delete (document as Document & { fullscreenElement?: Element | null }).fullscreenElement;
});

describe("mountFullscreenToggle", () => {
  it("enters and exits fullscreen while reflecting browser state", async () => {
    const button = document.createElement("button");
    const target = document.createElement("section");
    const request = vi.fn().mockResolvedValue(undefined);
    const exit = vi.fn().mockResolvedValue(undefined);
    target.requestFullscreen = request;
    document.exitFullscreen = exit;
    const dispose = mountFullscreenToggle(button, target, document);

    button.click();
    expect(request).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, "fullscreenElement", { configurable: true, value: target });
    document.dispatchEvent(new Event("fullscreenchange"));
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(button.textContent).toBe("返回看板");
    button.click();
    expect(exit).toHaveBeenCalledTimes(1);
    dispose();
  });

  it("uses a reversible in-page fullscreen layout when the browser API is unavailable", () => {
    const doc = document.implementation.createHTMLDocument();
    const button = doc.createElement("button");
    const target = doc.createElement("section");
    const dispose = mountFullscreenToggle(button, target, doc);

    button.click();
    expect(target.hasAttribute("data-fullscreen-fallback")).toBe(true);
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(button.textContent).toBe("返回看板");

    button.click();
    expect(target.hasAttribute("data-fullscreen-fallback")).toBe(false);
    expect(button.getAttribute("aria-pressed")).toBe("false");
    dispose();
  });

  it("falls back when native fullscreen is rejected", async () => {
    const button = document.createElement("button");
    const target = document.createElement("section");
    target.requestFullscreen = vi.fn().mockRejectedValue(new Error("Not allowed"));
    const dispose = mountFullscreenToggle(button, target, document);

    button.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(target.hasAttribute("data-fullscreen-fallback")).toBe(true);
    expect(button.getAttribute("aria-pressed")).toBe("true");
    dispose();
  });
});
