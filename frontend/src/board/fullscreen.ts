export function mountFullscreenToggle(
  button: HTMLButtonElement,
  target: HTMLElement,
  doc: Document = document,
): () => void {
  let disposed = false;
  let pending = false;
  let previousOverflow = "";
  const sync = () => {
    const active = doc.fullscreenElement === target || target.hasAttribute("data-fullscreen-fallback");
    button.setAttribute("aria-pressed", String(active));
    button.textContent = active ? "退出全屏" : "画布全屏";
    button.setAttribute("aria-label", active ? "退出画布全屏" : "画布全屏");
  };
  const exitFallback = () => {
    if (!target.hasAttribute("data-fullscreen-fallback")) return;
    target.removeAttribute("data-fullscreen-fallback");
    doc.body.style.overflow = previousOverflow;
    sync();
    button.focus();
  };
  const enterFallback = () => {
    if (disposed) return;
    previousOverflow = doc.body.style.overflow;
    doc.body.style.overflow = "hidden";
    target.setAttribute("data-fullscreen-fallback", "");
    sync();
    button.focus();
  };
  const onKeyDown = (event: KeyboardEvent) => {
    if (!target.hasAttribute("data-fullscreen-fallback")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      exitFallback();
    } else if (event.key === "Tab") {
      const focusable = [...target.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href], [tabindex="0"]')]
        .filter(element => !element.hasAttribute("disabled") && element.getClientRects().length > 0);
      const next = event.shiftKey ? focusable.at(-1) : focusable[0];
      if (!target.contains(doc.activeElement) || doc.activeElement === (event.shiftKey ? focusable[0] : focusable.at(-1))) {
        event.preventDefault();
        next?.focus();
      }
    }
  };
  const onClick = () => {
    if (target.hasAttribute("data-fullscreen-fallback")) {
      exitFallback();
      return;
    }
    if (doc.fullscreenElement === target) {
      void doc.exitFullscreen?.().catch(() => undefined);
      return;
    }
    if (pending) return;
    const requestFullscreen = target.requestFullscreen;
    if (typeof requestFullscreen !== "function") {
      enterFallback();
      return;
    }
    try {
      // Invoke synchronously in the click handler to preserve the browser's user activation.
      pending = true;
      void requestFullscreen.call(target).catch(enterFallback).finally(() => { pending = false; });
    } catch {
      pending = false;
      enterFallback();
    }
  };
  button.addEventListener("click", onClick);
  doc.addEventListener("fullscreenchange", sync);
  doc.addEventListener("keydown", onKeyDown, true);
  sync();
  return () => {
    disposed = true;
    exitFallback();
    button.removeEventListener("click", onClick);
    doc.removeEventListener("fullscreenchange", sync);
    doc.removeEventListener("keydown", onKeyDown, true);
  };
}
