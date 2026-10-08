import { afterEach, expect, it, vi } from "vitest";
import type { Graph } from "@antv/x6";
import { observeGraphSize } from "./viewport";

afterEach(() => vi.unstubAllGlobals());

it("keeps the visible node inside the canvas when a side panel reduces its width", () => {
  let notify!: () => void;
  const disconnect = vi.fn();
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: () => void) { notify = callback; }
    observe() {}
    disconnect = disconnect;
  });
  let width = 1048;
  let height = 600;
  const container = document.createElement("div");
  Object.defineProperties(container, {
    clientWidth: { get: () => width }, clientHeight: { get: () => height },
  });
  let translation = { tx: 320, ty: 197 };
  const resize = vi.fn();
  const graph = {
    container, resize, scale: () => ({ sx: .75, sy: .75 }),
    translate: (tx?: number, ty?: number) => {
      if (tx === undefined) return translation;
      translation = { tx, ty: ty! };
    },
    getNodes: () => [{ getBBox: () => ({ x: 40, y: 80, width: 253, height: 106 }) }],
  } as unknown as Graph;
  const dispose = observeGraphSize(graph);
  width = 530; height = 513;
  notify();
  expect(40 * .75 + translation.tx).toBeGreaterThanOrEqual(16);
  expect((40 + 253) * .75 + translation.tx).toBeLessThanOrEqual(width - 16);
  expect(graph.scale()).toEqual({ sx: .75, sy: .75 });
  const calls = resize.mock.calls.length;
  notify();
  expect(resize).toHaveBeenCalledTimes(calls);
  width = 0; notify();
  expect(resize).toHaveBeenCalledTimes(calls);
  dispose();
  expect(disconnect).toHaveBeenCalledOnce();
});
