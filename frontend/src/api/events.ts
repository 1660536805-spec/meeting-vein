/** WS 通道客户端（Design_FrontendBoard §4.2）。
 * 三条独立通道：/ws/board（下行 diff/回滚）、/ws/mascot（下行表情）、/ws/cursor（上行手势）。
 * 统一指数退避重连；消息以回调派发，不在此层做业务处理。 */

/** board 通道下行事件（board.update / board.rollback）。 */
export interface BoardEvent {
  type: "board.update" | "board.rollback";
  graph_id: string;
  version: number;
  cells: any[];
  repair_receipt?: any;
  change_set?: any;
  cell_id?: string;
  version_doc?: number;
}

export interface MascotEvent {
  type: "mascot_state";
  state: string;
  label: string;
}

function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

/** 带指数退避重连的 WebSocket 包装（1s→2s→…→10s 封顶）。 */
class ReconnectingWS {
  private ws: WebSocket | null = null;
  private closed = false;
  private retry = 0;

  constructor(private url: string, private onMessage: (msg: any) => void) {}

  connect(): void {
    if (this.closed) return;
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => { this.retry = 0; };
    ws.onmessage = (ev) => {
      try { this.onMessage(JSON.parse(ev.data)); } catch { /* 忽略畸形帧 */ }
    };
    ws.onclose = () => { if (!this.closed) this.scheduleReconnect(); };
    ws.onerror = () => { ws.close(); };
  }

  private scheduleReconnect(): void {
    const delay = Math.min(1000 * 2 ** this.retry, 10000);
    this.retry += 1;
    window.setTimeout(() => this.connect(), delay);
  }

  send(data: any): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
      return true;
    }
    return false;
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
  }
}

/** 看板通道：订阅 board.update / board.rollback。 */
export class BoardChannel {
  private ws: ReconnectingWS;
  private handlers: Array<(ev: BoardEvent) => void> = [];

  constructor(meetingId: string) {
    this.ws = new ReconnectingWS(wsUrl(`/ws/board/${encodeURIComponent(meetingId)}`), (msg) => {
      if (msg?.type === "board.update" || msg?.type === "board.rollback") {
        for (const h of this.handlers) h(msg as BoardEvent);
      }
    });
    this.ws.connect();
  }

  on(cb: (ev: BoardEvent) => void): () => void {
    this.handlers.push(cb);
    return () => { this.handlers = this.handlers.filter((h) => h !== cb); };
  }

  close(): void {
    this.ws.close();
    this.handlers = [];
  }
}

/** 看板娘通道：订阅 mascot_state。 */
export class MascotChannel {
  private ws: ReconnectingWS;
  private handlers: Array<(ev: MascotEvent) => void> = [];

  constructor(meetingId: string) {
    this.ws = new ReconnectingWS(wsUrl(`/ws/mascot/${encodeURIComponent(meetingId)}`), (msg) => {
      if (msg?.type === "mascot_state") {
        for (const h of this.handlers) h(msg as MascotEvent);
      }
    });
    this.ws.connect();
  }

  on(cb: (ev: MascotEvent) => void): () => void {
    this.handlers.push(cb);
    return () => { this.handlers = this.handlers.filter((h) => h !== cb); };
  }

  close(): void {
    this.ws.close();
    this.handlers = [];
  }
}

/** 光标通道：仅上行 cursor.event 信封（Design_CursorCapture §4）。 */
export class CursorChannel {
  private ws: ReconnectingWS;

  constructor(private meetingId: string) {
    this.ws = new ReconnectingWS(wsUrl("/ws/cursor"), () => { /* 上行通道无下行 */ });
    this.ws.connect();
  }

  send(raw: any): void {
    this.ws.send({
      event: "cursor.event", version: "1.0", meeting_id: this.meetingId, events: [raw],
    });
  }

  close(): void { this.ws.close(); }
}
