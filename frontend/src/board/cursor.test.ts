import { afterEach, describe, expect, it, vi } from "vitest";
import type { Graph } from "@antv/x6";
import { CursorEventAdapter } from "./cursor";

function makeGraph() {
  const handlers = new Map<string, (event: unknown) => void>();
  const graph = { on: (event: string, handler: (event: unknown) => void) => handlers.set(event, handler) } as unknown as Graph;
  return { handlers, graph };
}

describe("CursorEventAdapter", () => {
  afterEach(() => vi.useRealTimers());

  it("reports a hovered node only after it settles for hover_settle_ms", () => {
    vi.useFakeTimers();
    const { handlers, graph } = makeGraph();
    const emit = vi.fn();
    new CursorEventAdapter(graph, emit, { hover_settle_ms: 250 });
    handlers.get("node:mouseenter")!({ node: { id: "node_1", getData: () => ({ type: "point" }) } });
    expect(emit).not.toHaveBeenCalled();                 // 未达停留阈值不上报
    vi.advanceTimersByTime(250);
    expect(emit).toHaveBeenCalledWith({ gesture_type: "hover", target: { node_id: "node_1", node_type: "point" }, is_final: true });
  });

  it("drops a hover that leaves before the settle threshold", () => {
    vi.useFakeTimers();
    const { handlers, graph } = makeGraph();
    const emit = vi.fn();
    new CursorEventAdapter(graph, emit, { hover_settle_ms: 250 });
    handlers.get("node:mouseenter")!({ node: { id: "node_1", getData: () => ({ type: "point" }) } });
    handlers.get("node:mouseleave")!({});
    vi.advanceTimersByTime(1000);
    expect(emit).not.toHaveBeenCalled();                 // 划过即走：enter 与 leave 都不发
  });

  it("emits a paired leave after a reported hover ends", () => {
    vi.useFakeTimers();
    const { handlers, graph } = makeGraph();
    const emit = vi.fn();
    new CursorEventAdapter(graph, emit, { hover_settle_ms: 250 });
    handlers.get("node:mouseenter")!({ node: { id: "node_1", getData: () => ({ type: "point" }) } });
    vi.advanceTimersByTime(250);
    handlers.get("node:mouseleave")!({});
    expect(emit).toHaveBeenLastCalledWith({ gesture_type: "hover", target: { node_id: null }, is_final: true });
  });
});
