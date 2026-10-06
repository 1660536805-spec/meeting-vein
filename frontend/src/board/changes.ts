/** 变更集可视化（Design_FrontendBoard §4.4）。
 * 收到 AI 批次 GraphChangeSet 后：added/modified 呼吸闪烁高亮（最近一次对话关联的节点），
 * skipped（用户已锁/编辑）标红虚框。
 * 高亮为纯 overlay（改 attrs），可定时清除 / 手动关闭，不写回数据。 */
import type { Graph } from "@antv/x6";
import type { GraphChangeSet } from "../api/rest";

const AI_COLOR = "#faad14";    // antd 金：AI 变更
const SKIP_COLOR = "#ff4d4f";  // antd 红：因 lock/edit 跳过
const FLASH_MS = 5200;         // 呼吸动画 1.5s × 3 ≈ 4.5s，留余量后清除

export interface ChangeSummary { added: number; modified: number; removed: number; skipped: number; }

interface Original { key: string; attrs: any; }

export class ChangeHighlighter {
  private timer: number | null = null;
  private originals = new Map<string, Original>();

  constructor(private graph: Graph) {}

  /** 应用一批变更集：added/modified 呼吸闪烁，标红 skipped。返回摘要供工具栏显示。 */
  apply(cs: GraphChangeSet, flash = true): ChangeSummary {
    this.clear();
    for (const d of cs.added ?? []) this.paint(d.cell_id, AI_COLOR, false, true);
    for (const d of cs.modified ?? []) this.paint(d.cell_id, AI_COLOR, false, true);
    for (const d of cs.skipped ?? []) this.paint(d.cell_id, SKIP_COLOR, true, false);
    if (flash) this.timer = window.setTimeout(() => this.clear(), FLASH_MS);
    return {
      added: (cs.added ?? []).length,
      modified: (cs.modified ?? []).length,
      removed: (cs.removed ?? []).length,
      skipped: (cs.skipped ?? []).length,
    };
  }

  /** 恢复全部被高亮的 cell 到原始样式。 */
  clear(): void {
    if (this.timer !== null) { window.clearTimeout(this.timer); this.timer = null; }
    for (const [cellId, rec] of this.originals) {
      const cell = this.graph.getCellById(cellId);
      // setAttrs 深合并：显式置 null 才能移除我们加的 strokeDasharray / class
      if (cell) cell.setAttrs({ [rec.key]: { ...rec.attrs, strokeDasharray: null, class: null } });
    }
    this.originals.clear();
  }

  private paint(cellId: string, color: string, dashed: boolean, breathing: boolean): void {
    const cell = this.graph.getCellById(cellId);
    if (!cell) return;
    const key = cell.isEdge() ? "line" : "body";
    if (!this.originals.has(cellId)) {
      this.originals.set(cellId, { key, attrs: JSON.parse(JSON.stringify(cell.getAttrs()?.[key] ?? {})) });
    }
    const patch: any = { stroke: color, strokeWidth: 2.5 };
    if (dashed) patch.strokeDasharray = "4 3";
    if (breathing && !cell.isEdge()) patch.class = "amo-breathing"; // index.html 全局 keyframes
    cell.setAttrs({ [key]: patch });
  }
}
