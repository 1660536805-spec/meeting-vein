/** Lightweight animated portrait; no model downloads or WebGL required. */
export class MascotPortraitView {
  private host: HTMLElement | null = null;
  private motion = "Idle";
  private readonly reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)");

  mount(host: HTMLElement): void {
    this.dispose();
    this.host = host;
    host.dataset.motion = this.motion;
    document.addEventListener("pointermove", this.onPointerMove, { passive: true });
    document.addEventListener("pointerleave", this.resetPose);
    window.addEventListener("blur", this.resetPose);
    this.reducedMotion?.addEventListener("change", this.resetPose);
  }

  setMotion(name: string): void {
    this.motion = ["Celebrate", "Concern"].includes(name) ? name : "Idle";
    if (this.host) this.host.dataset.motion = this.motion;
  }

  dispose(): void {
    document.removeEventListener("pointermove", this.onPointerMove);
    document.removeEventListener("pointerleave", this.resetPose);
    window.removeEventListener("blur", this.resetPose);
    this.reducedMotion?.removeEventListener("change", this.resetPose);
    this.resetPose();
    if (this.host) delete this.host.dataset.motion;
    this.host = null;
  }

  private readonly resetPose = () => {
    this.host?.style.removeProperty("--mascot-x");
    this.host?.style.removeProperty("--mascot-tilt");
  };

  private readonly onPointerMove = (event: PointerEvent) => {
    if (!this.host || this.reducedMotion?.matches || event.pointerType === "touch") return;
    const bounds = this.host.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    const x = Math.max(-1, Math.min(1, (event.clientX - bounds.left - bounds.width / 2) / (window.innerWidth / 2)));
    this.host.style.setProperty("--mascot-x", `${(x * 3).toFixed(2)}px`);
    this.host.style.setProperty("--mascot-tilt", `${(x * 1.5).toFixed(2)}deg`);
  };
}
