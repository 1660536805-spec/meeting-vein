/** BoardSDK（Design_FrontendBoard §4.3）：统一封装 WS 订阅 + REST 调用。
 * 前端其余模块只依赖此 facade，不直接触碰 fetch / WebSocket。 */
import {
  rest,
  type BoardGraph, type GraphChangeSet, type MetadataRecord, type RepairReceipt, type UserOp,
  type CursorConfig, type MeetingSummaryInfo, type MeetingsResponse,
} from "./rest";
import { BoardChannel, CursorChannel, MascotChannel, type BoardEvent } from "./events";
import type { MascotState } from "../mascot/MascotController";

export type {
  BoardEvent, BoardGraph, GraphChangeSet, MetadataRecord, RepairReceipt, UserOp,
  CursorConfig, MeetingSummaryInfo, MeetingsResponse,
};

export class BoardSDK {
  private boardCh: BoardChannel;
  private mascotCh: MascotChannel;
  private cursorCh: CursorChannel;

  constructor(private meetingId: string) {
    this.boardCh = new BoardChannel(meetingId);
    this.mascotCh = new MascotChannel(meetingId);
    this.cursorCh = new CursorChannel(meetingId);
  }

  /** GET /api/board/:graph_id */
  load(graphId: string = this.meetingId): Promise<BoardGraph> {
    return rest.loadBoard(graphId);
  }

  /** WS /ws/board/:meeting_id（返回退订函数）。 */
  subscribeBoard(cb: (ev: BoardEvent) => void): () => void {
    return this.boardCh.on(cb);
  }

  /** WS /ws/mascot/:meeting_id（返回退订函数）。 */
  onMascot(cb: (s: MascotState) => void): () => void {
    return this.mascotCh.on((ev) => cb(ev.state as MascotState));
  }

  /** WS /ws/cursor：上行手势事件。 */
  emitCursor(ev: any): void {
    this.cursorCh.send(ev);
  }

  /** POST /api/node/:node_id/op */
  emitUserOp(op: UserOp): Promise<any> {
    return rest.emitUserOp(op);
  }

  /** GET /api/metadata?ids=... */
  fetchMetadata(ids: string[]): Promise<MetadataRecord[]> {
    return rest.metadata(ids).then((r) => r.records);
  }

  /** GET /api/cursor/config：拉取光标采集参数（throttle/settle 等，Cursor §9）。 */
  getConfig(): Promise<CursorConfig> {
    return rest.cursorConfig();
  }

  /** GET /api/meetings：历史会议列表（侧边历史面板数据源）。 */
  listMeetings(): Promise<MeetingsResponse> {
    return rest.listMeetings();
  }

  /** GET /api/board/:graph_id/outline */
  outline(graphId: string = this.meetingId): Promise<string> {
    return rest.outline(graphId).then((r) => r.outline);
  }

  /** POST /api/node/:node_id/lock */
  lock(nodeId: string, locked: boolean, graphId: string = this.meetingId): Promise<any> {
    return rest.lock(nodeId, graphId, locked);
  }

  /** POST /api/node/:node_id/importance */
  setImportance(nodeId: string, level: string, graphId: string = this.meetingId): Promise<any> {
    return rest.setImportance(nodeId, graphId, level);
  }

  /** POST /api/board/:graph_id/rollback */
  rollback(cellId: string, version: number, graphId: string = this.meetingId): Promise<any> {
    return rest.rollback(graphId, cellId, version);
  }

  /** GET /api/board/:graph_id/history */
  history(cellId?: string, graphId: string = this.meetingId): Promise<any[]> {
    return rest.history(graphId, cellId).then((r) => r.records);
  }

  /** POST /api/board/:graph_id/snapshot → 只读分享链。 */
  snapshot(graphId: string = this.meetingId): Promise<{ token: string; url: string; version: number }> {
    return rest.snapshot(graphId);
  }

  close(): void {
    this.boardCh.close();
    this.mascotCh.close();
    this.cursorCh.close();
  }
}
