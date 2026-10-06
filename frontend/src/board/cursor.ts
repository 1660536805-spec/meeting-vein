import type { Graph } from "@antv/x6";

/** 光标采集适配器（Design_CursorCapture.md §2.2 / §2.3）。
 * mousemove 节流；hover 需停留达阈值才计一次焦点；拖拽维护 partial→final 状态机，仅发 is_final 事件进总线。
 * X6 v2 事件回调是单参数对象 ({ e, x, y, node, view })，x/y 为图内坐标——
 * 统一用它而非 e.offsetX（offset 随事件目标元素漂移，跨元素拖拽时位移计算会失真）。
 * 纯前端采集，不进 LangGraph、不进存储（与看板娘眼睛追踪共享鼠标事件源但用途隔离）。 */

/** 采集参数（由 GET /api/cursor/config 下发，见 Design_CursorCapture §9），避免前后端常量漂移。
 * view_mode_sample_rate：查看模式（graph.interacting=false）下 hover/click 的降采样比例，
 * 后端当前未下发该字段，故前端内置默认 0.3（Design §2.4 防误触降噪）。 */
export interface CursorConfig {
  throttle_ms: number;
  hover_settle_ms: number;
  drag_px_threshold: number;
  view_mode_sample_rate: number;
}

const DEFAULT_CONFIG: CursorConfig = { throttle_ms: 200, hover_settle_ms: 250, drag_px_threshold: 8, view_mode_sample_rate: 0.3 };

interface DragSession { startX: number; startY: number; lastX: number; lastY: number; moved: boolean; }

export class CursorEventAdapter {
  private last = 0;
  private drag: DragSession | null = null;
  private hoverTimer: ReturnType<typeof setTimeout> | null = null;
  private hoveredNodeId: string | null = null;
  private holding = false;   // blank 按住中（canvas_hold partial → final）
  private cfg: CursorConfig;
  private bound: Array<{ event: string; handler: (e: any) => void }> = [];

  constructor(private graph: Graph, private emit: (raw: any) => void, cfg?: Partial<CursorConfig>) {
    this.cfg = { ...DEFAULT_CONFIG, ...cfg };
    this.bind();
  }

  /** 后端下发配置后就地更新采集参数（无需重建适配器/重绑事件）。 */
  configure(cfg: Partial<CursorConfig>): void {
    this.cfg = { ...this.cfg, ...cfg };
  }

  /** 解绑全部事件监听并清理定时器（页面卸载时调用，避免监听泄漏）。 */
  dispose(): void {
    this.clearHoverTimer();
    const off = (this.graph as any).off;
    if (typeof off === "function") {           // mock 环境无 off，静默跳过
      for (const b of this.bound) off.call(this.graph, b.event, b.handler);
    }
    this.bound = [];
    this.drag = null;
    this.holding = false;
    this.hoveredNodeId = null;
  }

  /** 查看模式（只读画布）判定：interacting=false 即历史快照/会议中防误触。 */
  private isViewMode(): boolean {
    return (this.graph as any).options?.interacting === false;
  }

  /** 查看模式下按 view_mode_sample_rate 降采样 hover/click（drag/dblclick 等强意图除外）。 */
  private sampledOut(): boolean {
    return this.isViewMode() && Math.random() > this.cfg.view_mode_sample_rate;
  }

  private register(event: string, handler: (e: any) => void): void {
    (this.graph as any).on(event, handler);
    this.bound.push({ event, handler });
  }

  private bind(): void {
    this.register("node:mousedown", ({ e, x, y }: any) => {
      if (e.button !== 0) return;       // 仅左键
      this.drag = { startX: x, startY: y, lastX: x, lastY: y, moved: false };
    });
    this.register("node:mousemove", ({ x, y }: any) => this.onMove(x, y));
    this.register("node:mouseup", ({ node, x, y }: any) => this.onUp(node.id, x, y));
    this.register("node:mouseenter", ({ node }: any) => this.onEnter(node));
    this.register("node:mouseleave", () => this.onLeave());
    this.register("node:click", ({ node }: any) => {
      if (this.sampledOut()) return;
      this.emit({ gesture_type: "click", target: { node_id: node.id, node_type: node.getData?.()?.type }, is_final: true });
    });
    this.register("node:dblclick", ({ node }: any) => {
      this.emit({ gesture_type: "dblclick", target: { node_id: node.id, node_type: node.getData?.()?.type }, is_final: true });
    });
    // 空白按住/框选 → canvas_hold（partial 多次，释放时 final）
    this.register("blank:mousedown", ({ e, x, y }: any) => {
      if (e.button !== 0) return;
      this.holding = true;
      this.onHoldMove(x, y, false);
    });
    this.register("blank:mousemove", ({ x, y }: any) => this.onHoldMove(x, y, false));
    this.register("blank:mouseup", ({ x, y }: any) => this.onHoldMove(x, y, true));
  }

  /** hover 停留达 hover_settle_ms 才上报焦点：划过即走不算，避免高频触发整轮图推理。 */
  private onEnter(node: any): void {
    this.clearHoverTimer();
    if (this.sampledOut()) return;           // 查看模式降采样：跳过这段 hover（leave 亦不再配对）
    this.hoverTimer = setTimeout(() => {
      this.hoverTimer = null;
      this.hoveredNodeId = node.id;
      this.emit({
        gesture_type: "hover", target: { node_id: node.id, node_type: node.getData()?.type },
        is_final: true,
      });
    }, this.cfg.hover_settle_ms);
  }

  private onLeave(): void {
    this.clearHoverTimer();                  // 未达停留阈值即离开 → 整段丢弃
    if (this.hoveredNodeId === null) return; // 未上报过 enter，则不发配对 leave
    this.hoveredNodeId = null;
    this.emit({ gesture_type: "hover", target: { node_id: null }, is_final: true });
  }

  private clearHoverTimer(): void {
    if (this.hoverTimer !== null) {
      clearTimeout(this.hoverTimer);
      this.hoverTimer = null;
    }
  }

  private onMove(x: number, y: number): void {
    const now = performance.now();
    if (now - this.last < this.cfg.throttle_ms) return;   // 仅节流，不丢最后状态
    this.last = now;
    if (!this.drag) return;
    const dx = x - this.drag.lastX, dy = y - this.drag.lastY;
    if (Math.hypot(dx, dy) > this.cfg.drag_px_threshold) this.drag.moved = true;
    this.drag.lastX = x; this.drag.lastY = y;
  }

  private onUp(nodeId: string, x: number, y: number): void {
    if (!this.drag) return;
    // 末次位移可能落在节流窗口内被丢弃，故释放时以「起点→终点」总位移兜底结算
    const moved = this.drag.moved
      || Math.hypot(x - this.drag.startX, y - this.drag.startY) > this.cfg.drag_px_threshold;
    this.drag = null;
    if (!moved) return;                            // 位移过小视为 click，丢弃（降采样）
    this.emit({
      gesture_type: "drag_node",
      target: { node_id: nodeId, node_type: undefined },
      x, y, is_final: true,
      start_offset_ms: 0, end_offset_ms: 0,
    });
  }

  /** 空白按住/框选：节流推 partial，释放时收口 final（未按住则忽略）。 */
  private onHoldMove(x: number, y: number, isFinal: boolean): void {
    if (!this.holding) return;
    const now = performance.now();
    if (!isFinal && now - this.last < this.cfg.throttle_ms) return;
    this.last = now;
    if (isFinal) this.holding = false;
    this.emit({
      gesture_type: "canvas_hold",
      target: { node_id: null },
      x, y,
      is_partial: !isFinal,
      is_final: isFinal,
    });
  }
}
