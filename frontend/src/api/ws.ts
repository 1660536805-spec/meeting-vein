/** 前端 WS 客户端（Design_Agent_DataFlow 传输层 / Design_CursorCapture §4）。
 * 上行：cursor.event（光标采集，200ms 节流后，信封含 meeting_id）；
 * 下行：mascot_state / board.update。
 *
 * 健壮性：断线指数退避重连（1s→10s 封顶）、消息 JSON 解析保护、
 * 未连接时上行入队并在重连成功后补发（上限 50 条，避免内存无界增长）。 */
export type WsStatus = "connecting" | "open" | "closed";

/** board.update / board.rollback 的附带信息（供变更高亮与回滚提示使用）。 */
export interface BoardMsgInfo {
  type: string;
  change_set?: any;
  cell_id?: string;
  version?: number;
}

export interface WsHandlers {
  onBoard: (cells: any[], graphId: string, info?: BoardMsgInfo) => void;
  onMascot: (state: string, label: string) => void;
  onStatus?: (status: WsStatus) => void;
}

const MAX_QUEUE = 50;
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 10000;

export class BoardWS {
  private ws: WebSocket | null = null;
  private retry = 0;
  private closedByUser = false;
  private queue: string[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(private url: string, private handlers: WsHandlers, private meetingId: string) {}

  connect(): void {
    this.closedByUser = false;
    this.handlers.onStatus?.("connecting");
    let sock: WebSocket;
    try {
      sock = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();          // 立即失败（如非法 URL）也走退避
      return;
    }
    this.ws = sock;
    sock.onopen = () => {
      this.retry = 0;
      this.handlers.onStatus?.("open");
      this.flushQueue();
    };
    sock.onmessage = (ev) => this.dispatch(ev.data);
    sock.onerror = () => { /* 统一交由 onclose 处理重连 */ };
    sock.onclose = () => {
      this.handlers.onStatus?.("closed");
      if (!this.closedByUser) this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.timer !== null) return;
    const delay = Math.min(BASE_DELAY_MS * 2 ** this.retry, MAX_DELAY_MS);
    this.retry += 1;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.connect();
    }, delay);
  }

  private flushQueue(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const pending = this.queue;
    this.queue = [];
    for (const payload of pending) {
      try {
        this.ws.send(payload);
      } catch { /* 单条失败不影响其余 */ }
    }
  }

  private dispatch(raw: unknown): void {
    let msg: any;
    try {
      msg = JSON.parse(String(raw));
    } catch {
      console.warn("[BoardWS] 忽略无法解析的消息", raw);
      return;
    }
    if (!msg || typeof msg !== "object") return;
    if (msg.graph_id && msg.graph_id !== this.meetingId) return;
    if (msg.meeting_id && msg.meeting_id !== this.meetingId) return;
    try {
      if (msg.type === "board.update" || msg.type === "board.rollback")
        this.handlers.onBoard(msg.cells, msg.graph_id ?? msg.meeting_id ?? this.meetingId, {
          type: msg.type, change_set: msg.change_set, cell_id: msg.cell_id, version: msg.version,
        });
      else if (msg.type === "mascot_state") this.handlers.onMascot(msg.state, msg.label);
    } catch (e) {
      console.warn("[BoardWS] handler 执行出错", e);
    }
  }

  sendCursor(raw: any): void {
    const payload = JSON.stringify({
      event: "cursor.event", version: "1.0", meeting_id: this.meetingId, events: [raw],
    });
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(payload);
    } else if (this.queue.length < MAX_QUEUE) {
      this.queue.push(payload);          // 未连接时入队，onopen 后补发
    }
  }

  close(): void {
    this.closedByUser = true;
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.ws?.close();
    this.ws = null;
  }
}
