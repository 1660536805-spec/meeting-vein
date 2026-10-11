import { afterEach, describe, expect, it, vi } from "vitest";
import type { Graph } from "@antv/x6";
import { currentZoom, mountBoardToolbar, zoomBy } from "./toolbar";
import { clearUserZoomFactor, getUserZoomFactor, noteUserZoom } from "./view-scale";

type Handler = (...args: any[]) => void;
type ZoomOptions = { absolute?: boolean };

/** 可变缩放比例的 mock graph：scale 是唯一真值，zoom 按 X6 语义更新它
 * （不带 absolute 为加法，带 absolute 为覆盖），便于断言行为的真实效果。 */
function makeGraph(sx = 1) {
  let scale = sx;
  const handlers: Record<string, Handler[]> = {};
  const graph = {
    scale: vi.fn(() => ({ sx: scale, sy: scale })),
    zoom: vi.fn((n: number, opts?: ZoomOptions) => {
      scale = opts?.absolute ? n : scale + n;
      return graph;
    }),
    zoomToFit: vi.fn(),
    on: vi.fn((event: string, cb: Handler) => {
      (handlers[event] ||= []).push(cb);
      return graph;
    }),
    getCellById: vi.fn(() => null),
    removeCell: vi.fn(),
    emit(event: string, arg: unknown) {
      (handlers[event] || []).forEach((cb) => cb(arg));
    },
  };
  return graph as unknown as Graph & typeof graph;
}

function setupToolbarDom(): void {
  document.body.innerHTML = `
    <button id="tb-zoom-out"></button>
    <span id="board-zoom-value"></span>
    <button id="tb-zoom-in"></button>`;
}

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
  clearUserZoomFactor();
});

describe("zoom helpers", () => {
  it("reads the current scale from graph.scale()", () => {
    expect(currentZoom(makeGraph(1.5))).toBe(1.5);
  });

  it("zooms to an absolute target rather than adding to the current scale", () => {
    const graph = makeGraph(1);
    zoomBy(graph, 1.2);
    expect(graph.zoom).toHaveBeenCalledWith(1.2, { absolute: true });
  });

  it("clamps zoom-in at the maximum", () => {
    const graph = makeGraph(3);
    zoomBy(graph, 1.2);
    expect(graph.zoom).toHaveBeenCalledWith(3, { absolute: true });
  });

  it("clamps zoom-out at the minimum", () => {
    const graph = makeGraph(0.25);
    zoomBy(graph, 1 / 1.2);
    expect(graph.zoom).toHaveBeenCalledWith(0.25, { absolute: true });
  });
});

describe("mountBoardToolbar", () => {
  it("fits the current graph and releases the previous zoom preference", () => {
    setupToolbarDom();
    document.body.insertAdjacentHTML("beforeend", '<button id="tb-fit"></button>');
    const graph = makeGraph(2);
    noteUserZoom(2);
    const dispose = mountBoardToolbar(graph);
    document.getElementById("tb-fit")!.click();
    expect(graph.zoomToFit).toHaveBeenCalledWith({ padding: 48, maxScale: 1 });
    expect(getUserZoomFactor()).toBeNull();
    dispose();
    graph.zoomToFit.mockClear();
    document.getElementById("tb-fit")!.click();
    expect(graph.zoomToFit).not.toHaveBeenCalled();
  });
  it("shows the initial zoom percentage and updates it on click", () => {
    setupToolbarDom();
    const graph = makeGraph(1);
    mountBoardToolbar(graph);

    const label = document.getElementById("board-zoom-value")!;
    expect(label.textContent).toBe("100%");

    document.getElementById("tb-zoom-in")!.click();
    expect(label.textContent).toBe("120%");

    document.getElementById("tb-zoom-out")!.click();
    expect(label.textContent).toBe("100%");
  });

  it("reflects scale changes coming from the graph itself", () => {
    setupToolbarDom();
    const graph = makeGraph(1);
    mountBoardToolbar(graph);

    graph.zoom(2, { absolute: true });
    graph.emit("scale", { sx: 2, sy: 2 });
    expect(document.getElementById("board-zoom-value")!.textContent).toBe("200%");
  });
});
