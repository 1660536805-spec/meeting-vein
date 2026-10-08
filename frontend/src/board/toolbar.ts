import type { Graph } from "@antv/x6";
import { beginAutoAdjust, clearUserZoomFactor, endAutoAdjust, noteUserZoom } from "./view-scale";

/** 底部液态玻璃控制条（缩放：−/比例/＋），看板页与历史页共用。
 * 用户手动缩放（按钮/滚轮）会上报为缩放系数，供 Agent 自动调整视口时
 * 做「基准 × 用户系数」组合（见 view-scale.ts）。删除由 main.ts 统一处理：
 * 看板页选中后按 Delete 或点击「删除选中」按钮（历史页只读，无删除）。 */

/** 缩放范围，避免按钮连点导致无限放大/缩小（滚轮缩放由 X6 自身夹紧）。 */
const ZOOM_MIN = 0.25;
const ZOOM_MAX = 3;
const ZOOM_STEP = 1.2;

/** 当前缩放比例（graph.zoom() 是相对接口且返回 this，读比例须用 graph.scale()）。 */
export function currentZoom(graph: Graph): number {
  return graph.scale().sx;
}

function clamp(v: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, v));
}

/** 按倍率缩放（相对当前比例）。X6 的 graph.zoom(n) 是「在当前比例上加 n」，
 * 所以这里显式换算成目标绝对值，避免缩放步长随当前比例漂移。 */
export function zoomBy(graph: Graph, factor: number): void {
  graph.zoom(clamp(currentZoom(graph) * factor), { absolute: true });
}

/** 挂载控制条：绑定按钮、回读缩放百分比。返回解绑函数（页面卸载时调用，避免监听泄漏）。
 * onRelayout 提供「重排」按钮的行为（重新排版，见 main.ts）；未提供时该按钮不绑定
 * （历史页只读，不传此回调 → 按钮不出现/不生效）。 */
export function mountBoardToolbar(graph: Graph, onRelayout?: () => void): () => void {
  const zoomValue = document.getElementById("board-zoom-value");
  const sync = (): void => {
    if (zoomValue) zoomValue.textContent = `${Math.round(currentZoom(graph) * 100)}%`;
  };

  const cleanups: Array<() => void> = [];
  const bind = (id: string, action: () => void): void => {
    const btn = document.getElementById(id);
    if (!btn) return;                       // 控件缺失（降级/测试）时静默跳过
    const handler = (): void => { action(); sync(); };
    btn.addEventListener("click", handler);
    cleanups.push(() => btn.removeEventListener("click", handler));
  };
  bind("tb-zoom-in", () => zoomBy(graph, ZOOM_STEP));
  bind("tb-zoom-out", () => zoomBy(graph, 1 / ZOOM_STEP));
  bind("tb-fit", () => {
    clearUserZoomFactor();
    beginAutoAdjust();
    try { graph.zoomToFit({ padding: 48, maxScale: 1 }); }
    finally { endAutoAdjust(); }
  });
  if (onRelayout) bind("tb-relayout", onRelayout);

  const onScale = (): void => {
    noteUserZoom(currentZoom(graph));      // 手动缩放（按钮/滚轮）上报系数；自动调整期间内部自动忽略
    sync();
  };
  graph.on("scale", onScale);
  cleanups.push(() => (graph as any).off?.("scale", onScale));   // mock 环境无 off，仅在解绑时按需调用
  sync();

  return () => { for (const fn of cleanups) fn(); };
}
