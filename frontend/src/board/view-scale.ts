/** 用户缩放偏好与「自动适配 × 用户缩放」组合（看板页/历史页共用）。
 *
 * 需求：用户手动设置缩放比例（+/−/滚轮/1:1）后，Agent 自动调整视口
 * （首屏/历史打开的窄板自动 fit、全图适配）不得覆盖用户设置，
 * 而是「自动基准 × 用户系数」乘法组合：final = fitBase × userFactor。
 * userFactor 记录为用户操作时的绝对 scale（1:1 → 1，即与适配基准同倍率组合）。
 *
 * 约定：
 *  - 程序自动调整（composeAutoFit）期间置 autoAdjusting，其触发的 scale 事件
 *    不计入用户系数；
 *  - 用户点「全图」是明确的适配意图 → 清除系数，此后自动 fit 回到纯基准；
 *  - factor 为 null（用户从未干预）时 composeAutoFit 不读 scale——兼容无 scale()
 *    mock 的单测环境与老调用方，行为与原版一致。 */

const ZOOM_MIN = 0.25;
const ZOOM_MAX = 3;

let userFactor: number | null = null;
let autoAdjusting = false;

/** 用户手动缩放后的绝对 scale（由 toolbar 的 scale 事件/按钮回调上报）。 */
export function noteUserZoom(scale: number): void {
  if (autoAdjusting || !Number.isFinite(scale) || scale <= 0) return;
  userFactor = scale;
}

/** 用户主动「全图」适配：视为放弃个性化缩放。 */
export function clearUserZoomFactor(): void {
  userFactor = null;
}

export function getUserZoomFactor(): number | null {
  return userFactor;
}

/** 程序自动调整视口开始/结束（期间 scale 事件不计入用户系数）。 */
export function beginAutoAdjust(): void {
  autoAdjusting = true;
}

export function endAutoAdjust(): void {
  autoAdjusting = false;
}

/** Agent 自动适配：zoomToFit 后按「基准 × 用户系数」组合缩放（factor 为 null 则保持纯基准）。 */
export function composeAutoFit(graph: any, options: { padding: number; maxScale?: number }): void {
  beginAutoAdjust();
  try {
    graph.zoomToFit(options);
  } finally {
    endAutoAdjust();
  }
  if (userFactor == null) return;
  const base = typeof graph.scale === "function" ? graph.scale().sx : NaN;
  if (!Number.isFinite(base)) return;
  // 收敛浮点尾差（0.8×1.5=1.2000000000000002），保留 4 位小数足够细控
  const target = Math.round(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, base * userFactor)) * 10000) / 10000;
  graph.zoom(target, { absolute: true });
}
