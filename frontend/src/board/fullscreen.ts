export function mountFullscreenToggle(
  button: HTMLButtonElement,
  target: HTMLElement,
  doc: Document = document,
): () => void {
  const sync = () => {
    const active = doc.fullscreenElement === target || target.hasAttribute("data-fullscreen-fallback");
    button.setAttribute("aria-pressed", String(active));
    button.textContent = active ? "返回看板" : "图谱全屏";
    button.setAttribute("aria-label", active ? "退出图谱全屏" : "图谱全屏打开");
  };
  const enterFallback = () => {
    target.setAttribute("data-fullscreen-fallback", "");
    sync();
  };
  const onClick = () => {
    if (target.hasAttribute("data-fullscreen-fallback")) {
      target.removeAttribute("data-fullscreen-fallback");
      sync();
      return;
    }
    if (doc.fullscreenElement === target) {
      void doc.exitFullscreen?.().catch(() => undefined);
      return;
    }
    const requestFullscreen = target.requestFullscreen;
    if (typeof requestFullscreen !== "function") {
      enterFallback();
      return;
    }
    try {
      // Invoke synchronously in the click handler to preserve the browser's user activation.
      void requestFullscreen.call(target).catch(enterFallback);
    } catch {
      enterFallback();
    }
  };
  button.addEventListener("click", onClick);
  doc.addEventListener("fullscreenchange", sync);
  sync();
  return () => {
    button.removeEventListener("click", onClick);
    doc.removeEventListener("fullscreenchange", sync);
  };
}
