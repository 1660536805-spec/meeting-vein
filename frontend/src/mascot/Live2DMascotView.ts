export interface Live2DHandle {
  canvas: HTMLCanvasElement;
  supportedMotions: readonly string[];
  setMotion(name: string): void;
  lookAt(clientX: number, clientY: number): void;
  dispose(): void;
}

export interface Live2DRuntime {
  load(host: HTMLElement): Promise<Live2DHandle>;
}

const CORE_URL = "https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js";
const MANIFEST_URL = "/mascot/live2d/pet.json";
let coreLoading: Promise<void> | null = null;
let pluginRegistered = false;

function hasCubismCore(): boolean {
  return Boolean((window as Window & { Live2DCubismCore?: unknown }).Live2DCubismCore);
}

function ensureCubismCore(): Promise<void> {
  if (hasCubismCore()) return Promise.resolve();
  if (coreLoading) return coreLoading;
  coreLoading = new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = CORE_URL;
    script.async = true;
    script.dataset.live2dCore = "true";
    script.onload = () => hasCubismCore() ? resolve() : reject(new Error("Live2D Core did not initialize"));
    script.onerror = () => reject(new Error("Unable to load Live2D Core"));
    document.head.append(script);
  }).finally(() => { coreLoading = null; });
  return coreLoading;
}

class PixiLive2DRuntime implements Live2DRuntime {
  async load(host: HTMLElement): Promise<Live2DHandle> {
    let app: import("pixi.js").Application | undefined;
    let model: import("untitled-pixi-live2d-engine/cubism").Live2DModel | undefined;
    let observer: ResizeObserver | undefined;
    let modelAttached = false;
    try {
      await ensureCubismCore();
      const pixi = await import("pixi.js");
      const engine = await import("untitled-pixi-live2d-engine/cubism");
      if (!pluginRegistered) {
        pixi.extensions.add(engine.Live2DPlugin);
        engine.configureCubismSDK({ memorySizeMB: 64 });
        pluginRegistered = true;
      }

      const bounds = host.getBoundingClientRect();
      app = new pixi.Application();
      await app.init({
        width: Math.max(1, Math.round(bounds.width || 160)),
        height: Math.max(1, Math.round(bounds.height || 280)),
        backgroundAlpha: 0,
        antialias: true,
        autoDensity: true,
        resolution: Math.min(window.devicePixelRatio || 1, 2),
        preference: "webgl",
      });

      const manifestResponse = await fetch(MANIFEST_URL);
      if (!manifestResponse.ok) throw new Error(`Mascot manifest request failed: ${manifestResponse.status}`);
      const manifest = await manifestResponse.json() as { live2d?: { model?: string } };
      const modelPath = manifest.live2d?.model;
      if (typeof modelPath !== "string" || !/^[A-Za-z0-9._/-]+\.model3\.json$/.test(modelPath)
        || modelPath.split("/").includes("..")) {
        throw new Error("Mascot manifest has an invalid model path");
      }
      const modelUrl = `/mascot/live2d/${modelPath}`;
      const modelResponse = await fetch(modelUrl);
      if (!modelResponse.ok) throw new Error(`Mascot model request failed: ${modelResponse.status}`);
      const modelDefinition = await modelResponse.json() as {
        FileReferences?: { Motions?: Record<string, unknown[]> };
      };
      if (!modelDefinition.FileReferences) throw new Error("Mascot model has no file references");

      const loaded = await engine.Live2DModel.from(modelUrl, {
        autoUpdate: true,
        autoFocus: false,
        autoInteract: false,
        eyeBlink: true,
      });
      model = loaded;
      app.stage.addChild(loaded);
      modelAttached = true;
      loaded.anchor.set(0.5, 0.5);
      const sourceSize = {
        width: Math.max(1, loaded.internalModel.originalWidth || loaded.width),
        height: Math.max(1, loaded.internalModel.originalHeight || loaded.height),
      };

      const layout = () => {
        if (!app || !model) return;
        const width = Math.max(1, Math.round(host.clientWidth || 160));
        const height = Math.max(1, Math.round(host.clientHeight || 280));
        app.renderer.resize(width, height);
        const scale = Math.min(width / sourceSize.width, height / sourceSize.height) * 0.94;
        model.scale.set(scale);
        model.position.set(width / 2, height / 2);
      };
      observer = new ResizeObserver(layout);
      observer.observe(host);
      layout();

      const supportedMotions = Object.keys(modelDefinition.FileReferences.Motions ?? {});
      let motionGeneration = 0;
      const setMotion = (name: string) => {
        if (!supportedMotions.includes(name)) return;
        const generation = ++motionGeneration;
        const idle = name === "Idle";
        void loaded.motion(name, 0, idle ? engine.MotionPriority.IDLE : engine.MotionPriority.FORCE, {
          loop: idle,
          onFinish: () => {
            if (!idle && generation === motionGeneration && supportedMotions.includes("Idle")) setMotion("Idle");
          },
          onError: () => undefined,
        }).catch(() => undefined);
      };
      setMotion("Idle");

      const canvas = app.canvas;
      return {
        canvas,
        supportedMotions,
        setMotion,
        lookAt(clientX, clientY) {
          if (!model) return;
          const rect = canvas.getBoundingClientRect();
          const x = rect.width ? Math.max(-1, Math.min(1, ((clientX - rect.left) / rect.width) * 2 - 1)) : 0;
          const y = rect.height ? Math.max(-1, Math.min(1, 1 - ((clientY - rect.top) / rect.height) * 2)) : 0;
          model.internalModel.focusController.focus(x, y);
        },
        dispose() {
          observer?.disconnect();
          observer = undefined;
          if (app) {
            app.destroy({ removeView: true }, { children: true });
            app = undefined;
            model = undefined;
          }
        },
      };
    } catch (error) {
      observer?.disconnect();
      if (app) app.destroy({ removeView: true }, { children: true });
      else if (model && !modelAttached) model.destroy({ children: true });
      throw error;
    }
  }
}

export class Live2DMascotView {
  private host: HTMLElement | null = null;
  private fallback: HTMLImageElement | null = null;
  private handle: Live2DHandle | null = null;
  private pendingMotion = "Idle";
  private disposed = false;
  private mountPromise: Promise<void> | null = null;
  private mountGeneration = 0;

  constructor(private readonly runtime: Live2DRuntime = new PixiLive2DRuntime()) {}

  mount(host: HTMLElement): Promise<void> {
    if (this.mountPromise && !this.disposed) return this.mountPromise;
    this.host = host;
    this.disposed = false;
    const generation = ++this.mountGeneration;
    this.fallback = host.querySelector<HTMLImageElement>("img") ?? document.createElement("img");
    this.fallback.src = host.dataset.fallbackSrc || "/mascot/fallback.svg";
    this.fallback.alt = "看板娘静态替代图";
    this.fallback.className = "mascot-fallback";
    this.fallback.hidden = false;
    if (!this.fallback.parentElement) host.append(this.fallback);
    document.addEventListener("pointermove", this.onPointerMove);
    document.addEventListener("pointerleave", this.onPointerLeave);
    this.mountPromise = this.load(host, generation);
    return this.mountPromise;
  }

  setMotion(name: string): void {
    this.pendingMotion = name;
    this.applyPendingMotion();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.mountGeneration += 1;
    this.mountPromise = null;
    document.removeEventListener("pointermove", this.onPointerMove);
    document.removeEventListener("pointerleave", this.onPointerLeave);
    this.handle?.dispose();
    this.handle?.canvas.remove();
    this.handle = null;
    if (this.fallback) this.fallback.hidden = false;
  }

  private async load(host: HTMLElement, generation: number): Promise<void> {
    try {
      const handle = await this.runtime.load(host);
      if (this.disposed || generation !== this.mountGeneration) { handle.dispose(); return; }
      this.handle = handle;
      handle.canvas.classList.add("mascot-live2d-canvas");
      handle.canvas.setAttribute("role", "img");
      handle.canvas.setAttribute("aria-label", "Live2D 会议看板娘");
      host.append(handle.canvas);
      if (this.fallback) this.fallback.hidden = true;
      this.applyPendingMotion();
    } catch {
      if (!this.disposed && generation === this.mountGeneration && this.fallback) this.fallback.hidden = false;
    }
  }

  private applyPendingMotion(): void {
    if (this.handle?.supportedMotions.includes(this.pendingMotion)) {
      this.handle.setMotion(this.pendingMotion);
    }
  }

  private readonly onPointerMove = (event: PointerEvent) => {
    this.handle?.lookAt(event.clientX, event.clientY);
  };

  private readonly onPointerLeave = () => {
    if (!this.host || this.disposed) return;
    const rect = this.host.getBoundingClientRect();
    this.handle?.lookAt(rect.left + rect.width / 2, rect.top + rect.height / 2);
  };
}
