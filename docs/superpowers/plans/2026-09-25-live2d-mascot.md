# Live2D 看板娘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the right-side SVG meeting secretary with the licensed Live2D whale-girl model while preserving existing state labels, meeting behavior, and a usable fallback.

**Architecture:** A focused `Live2DMascotView` owns Pixi renderer/model lifecycle, pointer gaze, and fallback UI. The existing `MascotController` remains the state and bubble facade and translates backend state strings to model motions. Model files and notices are kept separate from application code; Cubism Core is loaded according to Live2D's official distribution terms and is never copied into this repository.

**Tech Stack:** TypeScript, Vite, PixiJS 8.19.0, `untitled-pixi-live2d-engine` 1.3.5, Vitest, browser WebGL verification.

**Spec:** `docs/superpowers/specs/2026-09-25-live2d-mascot-design.md`

## Global Constraints

- Continue to use the existing right-side mascot panel, `mascot_state` WebSocket messages, and Chinese state labels.
- Use only actions and expressions declared by the selected model; unknown state strings use a safe idle motion and generic Chinese bubble.
- Do not include the DSH plugin host, plugin settings UI, costume menus, or drag/resize interactions.
- Do not copy Cubism Core into the repository, npm package, or production build.
- Record the CC BY-NC-SA 4.0 license, authorship chain (上善无形, ZipZipPipe, 氵六青), source URL, and whether assets were modified.
- If renderer/model/Core initialization fails, preserve the current state bubble and static fallback; recording and board interactions remain available.
- Preserve unrelated uncommitted workspace changes. Current `frontend/index.html` and `frontend/src/mascot/MascotController.ts` contain pre-existing user edits; integrate around them and do not revert them.

## Review Focus

- Core CDN blocked or unavailable → fallback is visible and state updates continue without an uncaught error.
- WebGL unavailable or context creation fails → no stuck loading state; static mascot and bubble remain.
- A backend state with no corresponding model motion → idle motion and generic/known Chinese bubble, no exception.
- State changes before model loading completes → latest state is applied once the model becomes ready.
- Repeated mount/dispose and pointer events → ticker and DOM listeners are detached and no second canvas is left behind.

---

### Task 1: Add model assets, renderer dependencies, and attribution

**Files:**
- Create: `frontend/public/mascot/live2d/**` (only required `ds-whale-girl` model and texture/motion/expression assets)
- Create: `frontend/public/mascot/fallback.svg`
- Create: `frontend/public/mascot/NOTICE.md`
- Modify: `frontend/package.json`, `frontend/package-lock.json`
- Test: `frontend/src/mascot/assets.test.ts`

**Interfaces:**
- Produces: `frontend/public/mascot/live2d/pet.json` plus the relative model files it references; the renderer loads this manifest by URL.
- Produces: `frontend/public/mascot/NOTICE.md` naming 上善无形, ZipZipPipe, 氵六青; linking the source repository and CC BY-NC-SA 4.0; stating whether model assets were modified and that Cubism Core is excluded.
- Keep model path segments ASCII as required by the source package.

- [ ] **Step 1: Write a failing asset manifest and notice test**

```ts
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("Live2D mascot assets", () => {
  it("ships an ASCII-path model manifest and attribution notice without Cubism Core", () => {
    const manifest = JSON.parse(readFileSync("public/mascot/live2d/pet.json", "utf8"));
    const notice = readFileSync("public/mascot/NOTICE.md", "utf8");
    expect(manifest.live2d.model).toMatch(/^[A-Za-z0-9._/-]+\.model3\.json$/);
    expect(notice).toContain("上善无形");
    expect(notice).toContain("ZipZipPipe");
    expect(notice).toContain("氵六青");
    expect(notice).toContain("CC BY-NC-SA 4.0");
    expect(notice).toMatch(/Cubism Core[\s\S]*(不包含|不分发|excluded)/i);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npm test -- --run src/mascot/assets.test.ts`
Expected: FAIL because model files and notice do not exist.

- [ ] **Step 3: Add only the licensed runtime assets and pinned renderer packages**

Copy the `ds-whale-girl` pet bundle from the public source repository, keeping its `pet.json` referenced paths intact; omit demos, DSH-only code, screenshots, source model packs, and any `live2dcubismcore.min.js`. Add exact dependency versions `pixi.js@8.19.0` and `untitled-pixi-live2d-engine@1.3.5` to the frontend runtime dependencies. Create `fallback.svg` as a small locally authored neutral whale mascot. Add `NOTICE.md` with the author chain, source link, [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/), attribution requirement, non-commercial restriction, share-alike obligation, modification statement, and explicit Cubism Core exclusion.

- [ ] **Step 4: Run the asset test and inspect the shipped file list**

Run: `cd frontend && npm test -- --run src/mascot/assets.test.ts`
Expected: PASS; all model paths resolve within `public/mascot/live2d`, and no Cubism Core binary is present.

- [ ] **Step 5: Commit the isolated asset/dependency change**

```bash
git add frontend/public/mascot frontend/package.json frontend/package-lock.json frontend/src/mascot/assets.test.ts
git commit -m "feat: add licensed Live2D mascot assets"
```

### Task 2: Build the Live2D renderer with a safe fallback

**Files:**
- Create: `frontend/src/mascot/Live2DMascotView.ts`
- Create: `frontend/src/mascot/Live2DMascotView.test.ts`
- Modify: `frontend/index.html`
- Modify: `frontend/src/main.ts`

**Interfaces:**
- Produces: `Live2DMascotView` with `mount(host: HTMLElement): Promise<void>`, `setMotion(name: string): void`, and `dispose(): void`.
- `Live2DHandle` is `{ canvas: HTMLCanvasElement; supportedMotions: readonly string[]; setMotion(name: string): void; lookAt(clientX: number, clientY: number): void; dispose(): void }`.
- `Live2DRuntime` is `{ load(): Promise<Live2DHandle> }`. `Live2DMascotView` accepts this as an injectable constructor dependency; its production implementation creates the Pixi application and Live2D model.
- `mount` creates exactly one transparent canvas in the supplied host, loads `/mascot/live2d/pet.json`, and requests the named model; it must catch runtime failures and expose fallback state.
- `dispose` is idempotent and removes ticker callbacks, pointer listeners, Pixi application, and canvas.

- [ ] **Step 1: Write lifecycle, gaze, and fallback tests**

Use this test scaffold, with the five cases checking successful mount, gaze forwarding, failure fallback, latest pending motion, and idempotent disposal:

```ts
import { describe, expect, it, vi } from "vitest";
import { Live2DMascotView, type Live2DHandle, type Live2DRuntime } from "./Live2DMascotView";

function fixture() {
  const canvas = document.createElement("canvas");
  const handle: Live2DHandle = {
    canvas, supportedMotions: ["Idle", "Listen"],
    setMotion: vi.fn(), lookAt: vi.fn(), dispose: vi.fn(),
  };
  const runtime: Live2DRuntime = { load: vi.fn().mockResolvedValue(handle) };
  const host = document.createElement("div");
  return { canvas, handle, runtime, host };
}

describe("Live2DMascotView", () => {
  it("mounts one canvas and forwards gaze", async () => {
    const { canvas, handle, runtime, host } = fixture();
    const view = new Live2DMascotView(runtime);
    await view.mount(host);
    host.dispatchEvent(new MouseEvent("pointermove", { clientX: 12, clientY: 34 }));
    expect(host.querySelectorAll("canvas")).toHaveLength(1);
    expect(host.contains(canvas)).toBe(true);
    expect(handle.lookAt).toHaveBeenCalledWith(12, 34);
    view.dispose();
  });

  it("keeps fallback visible when model loading rejects", async () => {
    const { runtime, host } = fixture();
    vi.mocked(runtime.load).mockRejectedValueOnce(new Error("Core unavailable"));
    const view = new Live2DMascotView(runtime);
    await expect(view.mount(host)).resolves.toBeUndefined();
    expect(host.querySelector<HTMLImageElement>("img")?.getAttribute("src"))
      .toBe("/mascot/fallback.svg");
  });

  it("applies the newest requested motion after loading", async () => {
    const { handle, runtime, host } = fixture();
    let resolveLoad!: (value: Live2DHandle) => void;
    vi.mocked(runtime.load).mockReturnValueOnce(new Promise((resolve) => { resolveLoad = resolve; }));
    const view = new Live2DMascotView(runtime);
    const mounted = view.mount(host);
    view.setMotion("Listen");
    resolveLoad(handle);
    await mounted;
    expect(handle.setMotion).toHaveBeenCalledWith("Listen");
    view.dispose();
  });

  it("detaches gaze handling and destroys the model once", async () => {
    const { handle, host } = fixture();
    const view = new Live2DMascotView({ load: async () => handle });
    await view.mount(host);
    view.dispose();
    view.dispose();
    host.dispatchEvent(new MouseEvent("pointermove", { clientX: 1, clientY: 2 }));
    expect(handle.dispose).toHaveBeenCalledTimes(1);
    expect(handle.lookAt).not.toHaveBeenCalled();
    expect(host.querySelector("canvas")).toBeNull();
  });
});
```

- [ ] **Step 2: Run the focused test to verify failure**

Run: `cd frontend && npm test -- --run src/mascot/Live2DMascotView.test.ts`
Expected: FAIL because `Live2DMascotView` does not exist.

- [ ] **Step 3: Implement the view and replace the SVG host**

Create a transparent Pixi application sized to its host, load the model manifest and referenced model through `untitled-pixi-live2d-engine`, attach gaze handling to the mascot panel, and retain a pending motion name until model readiness. On any renderer, Core, or model load error, hide the canvas and show `/mascot/fallback.svg`; do not throw from `mount`. `dispose` removes all listeners and destroys Pixi resources. Replace only the inline mascot SVG in `index.html` with an accessible Live2D host plus fallback image; retain panel name/bubble semantics and responsive CSS. In `main.ts`, construct and mount the view and connect page teardown to `dispose`.

- [ ] **Step 4: Run the focused lifecycle tests**

Run: `cd frontend && npm test -- --run src/mascot/Live2DMascotView.test.ts`
Expected: PASS for success, load failure, pending state, pointer gaze, and idempotent disposal.

- [ ] **Step 5: Commit the renderer integration**

```bash
git add frontend/index.html frontend/src/main.ts frontend/src/mascot/Live2DMascotView.ts frontend/src/mascot/Live2DMascotView.test.ts
git commit -m "feat: render mascot with Live2D"
```

### Task 3: Map meeting states to available model motions

**Files:**
- Modify: `frontend/src/mascot/MascotController.ts`
- Create: `frontend/src/mascot/MascotController.test.ts`
- Modify: `frontend/src/main.ts`

**Interfaces:**
- Export `resolveMascotMotion(state: string, supportedGroups: readonly string[]): string | undefined` from `MascotController.ts`; it returns the mapped motion group if supported, otherwise the supported idle group, otherwise `undefined`.
- `MascotController` accepts the existing bubble, a `(name: string) => void` motion callback, and the actual supported motion group names read from the model. `setState(state: string): void` keeps existing label behavior and invokes only a supported name; unknown state uses the idle group and generic Chinese text.

- [ ] **Step 1: Write state mapping tests from the bundled model manifest**

Add a controller unit test using the model's real motion group names (read `FileReferences.Motions` from the referenced `.model3.json`) and this contract:

```ts
const supported = Object.keys(model3.FileReferences.Motions);
for (const state of ["idle", "listening", "filtering", "loading_board", "assembling",
  "analyzing", "syncing", "success", "error"]) {
  const motion = resolveMascotMotion(state, supported);
  expect(motion === undefined || supported.includes(motion)).toBe(true);
}
expect(resolveMascotMotion("unknown-backend-value", supported))
  .toBe(supported.includes("Idle") ? "Idle" : supported[0]);

controller.setState("listening");
expect(bubble.textContent).toBe("聆听转写…");
controller.setState("unknown-backend-value");
expect(bubble.textContent).toBe("处理中…");
expect(bubble.textContent).not.toContain("unknown-backend-value");
```

Also assert the mapping table's listening/success/error entries resolve to a supported group when the manifest has at least one motion group.

- [ ] **Step 2: Run the controller test to verify failure**

Run: `cd frontend && npm test -- --run src/mascot/MascotController.test.ts`
Expected: FAIL because the current controller only writes bubble text and opacity.

- [ ] **Step 3: Add the state-to-motion adapter**

Inspect the actual bundled model's motion group names and define a typed mapping for available groups. Keep multiple backend states mapped to idle when the model has no semantically suitable action. Preserve existing Chinese labels and unknown-state fallback; pass each mapped name to the injected Live2D view callback.

- [ ] **Step 4: Run focused controller tests and the frontend suite**

Run: `cd frontend && npm test -- --run src/mascot/MascotController.test.ts && npm test -- --run`
Expected: focused mapping tests and all existing frontend tests PASS; pre-existing user changes remain intact.

- [ ] **Step 5: Commit the state mapping**

```bash
git add frontend/src/mascot/MascotController.ts frontend/src/mascot/MascotController.test.ts frontend/src/main.ts
git commit -m "feat: map meeting states to mascot motions"
```

### Task 4: Verify browser behavior, production output, and licensing docs

**Files:**
- Modify: `README.md`
- Verify: `frontend/dist/**` generated output (do not commit build output unless the repository already tracks it)
- Verify: running app at `http://127.0.0.1:5173/`

**Interfaces:**
- README documents the source/credit, CC BY-NC-SA 4.0 non-commercial and share-alike conditions, Cubism Core's external official-runtime requirement, model asset size, first-load behavior, and fallback behavior.

- [ ] **Step 1: Build production assets and check licensing exclusions**

Run: `cd frontend && npm run build`
Expected: TypeScript and Vite build succeed; inspect `dist` to ensure the model and fallback are included while Cubism Core is absent.

- [ ] **Step 2: Run full frontend tests and whitespace checks**

Run: `cd frontend && npm test -- --run && cd .. && git diff --check`
Expected: all frontend tests pass and `git diff --check` reports no issues.

- [ ] **Step 3: Perform browser verification**

With the local app open, verify the canvas draws the model, gaze follows the pointer, states change the bubble and a valid model motion, and recorder/board controls remain operable. Simulate or block the Core URL and verify fallback plus state bubble. Repeat at a viewport narrower than 700px and confirm the mascot panel remains hidden as before. Inspect browser console for uncaught errors.

- [ ] **Step 4: Document runtime and attribution**

Update the root README with asset credits and license restrictions, Cubism Core non-redistribution/loading requirement, local model asset footprint, and troubleshooting for the fallback state. Keep full notices in `frontend/public/mascot/NOTICE.md`.

- [ ] **Step 5: Commit documentation and final verification evidence**

```bash
git add README.md
git commit -m "docs: document Live2D mascot licensing and runtime"
```
