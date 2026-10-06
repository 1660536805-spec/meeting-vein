import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  beginAutoAdjust, clearUserZoomFactor, composeAutoFit, endAutoAdjust, noteUserZoom,
} from "./view-scale";

/** 构造可追踪 zoomToFit/scale/zoom 的最小 graph mock。 */
function makeGraph(fitBaseScale: number) {
  return {
    zoomToFit: vi.fn(),
    scale: vi.fn(() => ({ sx: fitBaseScale, sy: fitBaseScale })),
    zoom: vi.fn(),
  };
}

beforeEach(() => {
  clearUserZoomFactor();          // 模块级系数状态，测试间隔离
});

describe("view-scale", () => {
  it("composes auto-fit base with the user's zoom factor", () => {
    noteUserZoom(1.5);            // 用户手动放大到 150%
    const graph = makeGraph(0.8); // 自动适配基准 80%
    composeAutoFit(graph, { padding: 16, maxScale: 1 });
    expect(graph.zoomToFit).toHaveBeenCalledWith({ padding: 16, maxScale: 1 });
    expect(graph.zoom).toHaveBeenCalledWith(1.2, { absolute: true });   // 0.8 × 1.5
  });

  it("keeps the pure fit baseline when the user never zoomed", () => {
    const graph = makeGraph(0.8);
    composeAutoFit(graph, { padding: 16, maxScale: 1 });
    expect(graph.zoomToFit).toHaveBeenCalledTimes(1);
    expect(graph.scale).not.toHaveBeenCalled();
    expect(graph.zoom).not.toHaveBeenCalled();
  });

  it("ignores scale changes made by the program itself", () => {
    beginAutoAdjust();
    noteUserZoom(2.5);            // composeAutoFit/fitBoard 内部缩放不算用户操作
    endAutoAdjust();
    const graph = makeGraph(0.8);
    composeAutoFit(graph, { padding: 16, maxScale: 1 });
    expect(graph.zoom).not.toHaveBeenCalled();
  });

  it("clamps the composed scale into the zoom range", () => {
    noteUserZoom(5);
    const graph = makeGraph(1);
    composeAutoFit(graph, { padding: 40, maxScale: 1 });
    expect(graph.zoom).toHaveBeenCalledWith(3, { absolute: true });     // 上限 300%
  });
});
