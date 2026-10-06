/** 看板娘状态机（Design_Mascot.md）。
 * MascotState 精确映射 LangGraph 节点；WS mascot_state 事件驱动表情 + 气泡。 */
export type MascotState =
  | "idle" | "listening" | "filtering" | "loading_board" | "assembling"
  | "analyzing" | "syncing" | "success" | "error";

const LABELS: Record<MascotState, string> = {
  idle: "待命中",
  listening: "聆听转写…",
  filtering: "过滤闲聊…",
  loading_board: "载入看板…",
  assembling: "组装上下文中…",
  analyzing: "AI 分析论点…",
  syncing: "同步结构图…",
  success: "已更新看板 ✓",
  error: "出错了，已降级",
};

const FALLBACK_LABEL = "处理中…";
/** error 警示停留时长：超过后自动回落待命中（失败批次由后端 pending 轮询静默重试，
 * 恢复成功时会广播 success 再点亮「已更新看板 ✓」，error 不应永久挂住）。 */
export const ERROR_FALLBACK_MS = 6000;
// Kept in sync with c_0120.model3.json; MascotController.test.ts verifies this list.
export const SUPPORTED_MASCOT_MOTIONS = [
  "Idle", "Hammer", "BubbleGum", "SprayWater", "OpenCase", "Selfie", "SelfieQuick", "Ketchup",
] as const;

const MOTION_BY_STATE: Partial<Record<MascotState, string>> = {
  idle: "Idle",
  listening: "Idle",
  filtering: "Idle",
  loading_board: "Idle",
  assembling: "Idle",
  analyzing: "Idle",
  syncing: "Idle",
  success: "BubbleGum",
  error: "SprayWater",
};

export function resolveMascotMotion(state: string, supportedGroups: readonly string[]): string | undefined {
  const requested = MOTION_BY_STATE[state as MascotState];
  if (requested && supportedGroups.includes(requested)) return requested;
  if (supportedGroups.includes("Idle")) return "Idle";
  return supportedGroups[0];
}

export class MascotController {
  constructor(
    private visual: HTMLElement,
    private bubble: HTMLElement,
    private onMotion: (name: string) => void = () => undefined,
    private supportedGroups: readonly string[] = [],
  ) {}

  private errorTimer: ReturnType<typeof setTimeout> | null = null;

  /** 接受任意字符串状态（WS 直传后端值）；未知状态回退中文提示，不显示英文原文。 */
  setState(state: string): void {
    if (this.errorTimer !== null) {
      clearTimeout(this.errorTimer);
      this.errorTimer = null;
    }
    const known = Object.prototype.hasOwnProperty.call(LABELS, state);
    this.bubble.textContent = known ? LABELS[state as MascotState] : FALLBACK_LABEL;
    // 表情：MVP 用气泡文案表达；Live2D 路线通过 expressionManager 切换（Design_Mascot §3）
    this.visual.style.opacity = state === "error" ? "0.6" : "1";
    const motion = resolveMascotMotion(state, this.supportedGroups);
    if (motion) this.onMotion(motion);
    if (state === "error") {
      this.errorTimer = setTimeout(() => {
        this.errorTimer = null;
        this.setState("idle");
      }, ERROR_FALLBACK_MS);
    }
  }
}
