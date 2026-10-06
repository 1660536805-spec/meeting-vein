# 鼠标信号采集模块 · 详细设计

> **文档状态**：设计 v0.1 · 待评审
> **最后更新**：2026-09-18
> **关联文档**：
> - `doc/Research_CursorIntent_Capture.md`（调研：NormCursorEvent 同形格式、双流对等定位）
> - `doc/Design_Agent_DataFlow.md`（Agent 数据流与 LangGraph 编排；本模块对应 `input_cursor_node` / `filter_cursor_node` 两节点）
> - `doc/Research_X6_MindMap.md`（AntV X6 交互事件来源）
> - `doc/Design_StructureGraph_Storage.md`（§7 `data.edit.*` 软保护，拖拽中跳过写回）
> **模块定位**：把「用户鼠标焦点 / 左键手势」从原始 X6 事件，经过**采集 → 适配 → 传输 → 后端摄入**四段，变成与 ASR 同级别的 `NormCursorEvent` 一等输入流，按语音时间段窗口拼入对应 utterance 的 `mouse_action`、并参与 `update_node` 软保护（`filtered_focus`）。**本模块不负责语义理解**（那是输入处理模块的事），只负责「把原始鼠标行为变成干净、有序、带了 is_final 的标准事件」。

---

## 1. 模块边界与职责

```
┌─────────────────────────────────────────────────────────────────────┐
│ 前端（浏览器）                                                         │
│  ┌──────────────────┐   ┌────────────────────┐   ┌────────────────┐  │
│  │ ① X6 事件采集层   │──▶│ ② CursorEventAdapter│──▶│ ③ WS/SSE 传输   │  │
│  │ (原始事件监听+节流)│   │ (原始→NormCursorEvent)│   │ (光标流总线)    │  │
│  └──────────────────┘   └────────────────────┘   └────────────────┘  │
└───────────────────────────────────┬─────────────────────────────────┘
                                     │  NormCursorEvent[]（is_final 触发）
                                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 后端（Agent 服务）                                                     │
│  ┌────────────────────┐   ┌──────────────────────┐                   │
│  │ ④ input_cursor_node │──▶│ ⑤ filter_cursor_node   │──▶ assemble 第(d)路 │
│  │ (接收批次入 State)   │   │ (去抖+partial→final聚合)│      + update 软保护 │
│  └────────────────────┘   └──────────────────────┘                   │
└─────────────────────────────────────────────────────────────────────┘
```

| 段 | 职责 | 不负责 |
|---|---|---|
| ① 采集层 | 监听 X6 事件、节流降频、维护拖拽 partial 状态 | 不产出标准事件语义 |
| ② Adapter | 把原始 X6 事件 + 拖拽状态 → `NormCursorEvent` | 不做语义推断 |
| ③ 传输 | 把事件可靠送到后端总线（带 `seq`/重连） | 不做聚合 |
| ④ input_cursor_node | 接收批次写入 `AgentState.raw_cursor_events` | 不聚合 |
| ⑤ filter_cursor_node | 去抖、partial→final 聚合、产出 `filtered_focus` | 不调 LLM |

---

## 2. 前端采集层（①）

### 2.1 监听的事件与映射（对齐 `Research_X6_MindMap.md` §6）

| X6 事件 | 原始手势 | Adapter 产出 `gesture_type` | 阶段 |
|---|---|---|---|
| `node:mouseenter` | 进入节点 | `hover` | final（停留即定） |
| `node:mouseleave` | 离开节点 | `hover`（`target=null`） | final |
| `node:click` | 单击节点 | `click` | final |
| `node:dblclick` | 双击节点 | `dblclick`（可选 `intent_hint="查看论据"`） | final |
| `node:mousedown`（左键 `button=0`） | 节点上按下 | `drag_node`（起始） | **partial** |
| `node:mousemove`（按住中） | 拖拽中 | `drag_node` | **partial**（多次） |
| `node:mouseup` | 释放 | `drag_node`（结束，结算 duration） | **final** |
| `node:collapse` | 子树折叠 | `collapse` | final |
| `blank:mousedown` / `blank:mousemove` | 空白按住/框选 | `canvas_hold` | partial→final |

### 2.2 节流（降频，不丢语义）

`mousemove` 高频（60–120Hz）。采集层 **throttle ≈ 200ms**（可配，见 §9）后把坐标推给 Adapter，仅降网络/计算压力；**语义聚合在后端 `filter_cursor_node` 做**（与 ASR 管线分工一致：降频在端、聚合在服务）。

```typescript
const THROTTLE_MS = 200;
let lastEmit = 0;
graph.on('node:mousemove', (e) => {
  const now = performance.now();
  if (now - lastEmit < THROTTLE_MS) return;   // 仅节流，不丢最后状态
  lastEmit = now;
  cursorBus.rawMove(e);                        // 交给 Adapter 维护拖拽 partial 状态
});
```

### 2.3 拖拽 partial→final 跟踪状态机（关键）

前端 Adapter 必须维护一个**拖拽会话状态**，把一连串 `mousedown→mousemove(partial)→mouseup` 收口成一条 `is_final=true` 的 `drag_node` 事件（类比 ASR 2pass online→offline 段）。

```typescript
interface DragSession {
  nodeId: string;
  startX: number; startY: number;
  startOffsetMs: number;
  lastX: number; lastY: number;
  moved: boolean;
}

class CursorEventAdapter {
  private drag: DragSession | null = null;

  onMousedown(nodeId: string, e: PointerEvent) {
    this.drag = { nodeId, startX: e.offsetX, startY: e.offsetY,
                  startOffsetMs: nowOffsetMs(), lastX: e.offsetX, lastY: e.offsetY, moved: false };
  }
  onMousemove(e: PointerEvent) {           // throttle 后调用
    if (!this.drag) return;
    const dx = e.offsetX - this.drag.lastX, dy = e.offsetY - this.drag.lastY;
    if (Math.hypot(dx, dy) > DRAG_PX_THRESHOLD) this.drag.moved = true;
    this.drag.lastX = e.offsetX; this.drag.lastY = e.offsetY;
    // 拖拽进行中：emit partial（仅用于可选实时高亮，不进 LLM）
    this.emit({ gesture_type: 'drag_node', target: { node_id: this.drag.nodeId },
                is_partial: true, is_final: false, pointer: {x:e.offsetX,y:e.offsetY} });
  }
  onMouseup(e: PointerEvent) {
    if (!this.drag) return;
    const moved = this.drag.moved;
    const ev = {
      gesture_type: 'drag_node', target: { node_id: this.drag.nodeId },
      pointer: { x: e.offsetX, y: e.offsetY },
      is_partial: false, is_final: true,
      start_offset_ms: this.drag.startOffsetMs, end_offset_ms: nowOffsetMs(),
    };
    this.drag = null;
    if (!moved) return;                     // 位移过小视为 click，丢弃 drag（降采样）
    this.emit(ev);                          // ★ final 事件，进 filtered_focus → 可触发 assemble
  }
}
```

> 关键：只有 `is_final=true` 的事件才进入后端 `filtered_focus` 与触发下游；进行中 `is_partial` 事件仅用于（可选）前端实时高亮，**不进 LLM 上下文**。

### 2.4 与 X6 `interacting` 编辑/查看模式联动

- **编辑模式**（复盘/手动整理）：全量采集，用户真实操作即意图。
- **查看模式**（会议进行中防误触）：可降采样（`VIEW_MODE_SAMPLE_RATE`）或停采 hover/click，仅保留 `drag_node`/`dblclick` 等强意图手势，避免无意识划过产生噪声焦点。
- 软保护始终生效（见 §6）。

### 2.5 采集驱动原则（事件驱动，不空采）

- **纯事件驱动**：所有 `NormCursorEvent` 仅在真实 X6 事件发生时产生；鼠标静止/无事件时**不启动任何定时器或轮询**，不产生任何采样数据。
- **降频不补采**：`mousemove` 仅在前端 `THROTTLE_MS`（§9）节流后推送 partial，拖拽收口才出 final（§2.3）；无事件即无数据。
- **入 LLM 受控**：前端采样数据**不全量进入 LLM 上下文**（见 §7）——只有拼入语音时间段的窗口化 `mouse_action` 才会进入提示词，其余原始/partial 事件止于服务端（软保护或 debug 日志）。

---

## 3. 适配层（②）：原始事件 → `NormCursorEvent`

复用 `Research_CursorIntent_Capture.md` §2.2 的 `NormCursorEvent` 结构。`event_id` 由 Adapter 生成 UUID；`seq` 由**统一入口分配**（与 ASR 共享单调序号，见调研 Q1，默认共享）；`actor` 取本机登录用户 `user_ref`（`ent:`/`local:`）。

```json
{
  "event_id": "c_9b2e1f...",
  "meeting_id": "mtg_20260916_q3growth",
  "session_id": null,
  "seq": 1042,
  "actor": { "user_ref": "ent:user_zhang", "device": "desktop_win", "is_resolved": true },
  "gesture_type": "drag_node",
  "target": { "node_id": "n_issue_q3", "node_type": "issue", "edge_id": null },
  "pointer": { "x": 412, "y": 268 },
  "intent_hint": null,
  "start_offset_ms": 305120, "end_offset_ms": 306340, "received_at_ms": 1726483506123,
  "is_final": true, "is_partial": false,
  "source": "x6_canvas", "raw_ref": "x6:node:mousedown#n_issue_q3@412,268"
}
```

**边界**：基础版**不依赖** `intent_hint` 语义推断（调研 Q5，已闭合：默认关闭）。`intent_hint` 字段保留可选、默认 `null`，`filter_cursor_node` 始终不填充，只传结构化 `gesture_type + target`，避免前端过度推断污染 LLM。**后续若启用**：其语义映射必须以**代码形式固定流程**实现——给定前端事件 → 确定性的意图枚举（如 `dblclick`→`查看论据`、拖至空白→`新建游离子图`），为硬编码/查表逻辑，**不经由 LLM 推断**，杜绝 LLM 误判注入。

### 3.1 `actor` 标识与多端协作规则（闭合 Q2）

`actor` 块三字段：`user_ref`（统一操作者主键）、`device`（操作终端）、`is_resolved`（是否已映射到企业真人）。前缀与判定逻辑沿用 `ASR_UnifiedSchema.md` §4，结合光标流「本机用户、通常已解析」的特点收敛为下列最终规则。

**`user_ref` 前缀判定树**

| 场景 | `user_ref` | `is_resolved` | 说明 |
|---|---|---|---|
| 已登录且关联企业账号 | `ent:<userid>` | true | 最稳，可跨会议追踪（FR-POST-05 跨会议归因） |
| 未登录但本机可识别 | `local:<machine_guid>` | false | **默认未登录走此**；`<machine_guid>` 取装机 UUID / OS 用户，本机内稳定 |
| 无任何本机标识可用 | `anon:` | false | 极罕见兜底（如隐私模式且拒绝任何本地标识），光标流意义不大 |

> 约定：**未登录一律 `local:<machine_guid>` 而非裸 `local:` 或 `anon:`**。`local:` 保留「本机可识别」语义，使软保护（§6「别覆盖此人正在拖的节点」）与焦点归因仍能在会话内正确绑定到具体机器用户；`anon:` 仅当连本机标识都取不到时退化使用。这与 `Research_CursorIntent_Capture.md` §2.3「极少数 `anon:`」的描述一致，也把本设计原 Q2 的 `local:`/`anon:` 二选一歧义收口为：`local:` 常态、`anon:` 兜底。

**`device` 命名与多端唯一性**

- 命名格式：`<os>_<arch>` 枚举，如 `desktop_win` / `desktop_mac` / `web_chrome` / `mobile_ios` / `mobile_android`。
- **未登录场景（`local:`/`anon:`）必须追加装机 UUID 后缀**：`desktop_win#<machine_guid>`。原因：两台未登录机器 `user_ref` 都是 `local:`，`device` 成为唯一区分依据，裸 `<os>_<arch>` 会撞身份。
- 已登录（`ent:`）场景 `device` 可只用 `<os>_<arch>`（企业账号已能区分人，`device` 仅作终端类型展示）。

**多端冲突仲裁**：同节点多人操作时，以 `(user_ref, device)` 组合的最后一条 `is_final` 为准（与 §8 一致）；`device` 后缀的 `machine_guid` 保证未登录下该组合在会议内唯一。

---

## 4. 传输层（③）：WS/SSE 总线

光标流与 ASR 流**共用统一入口总线**，消息包封同形（对齐 `ASR_UnifiedSchema.md` §2.2 信封）：

```json
{
  "event": "cursor.event",
  "version": "1.0",
  "meeting_id": "mtg_20260916_q3growth",
  "emitted_at_ms": 1726483506100,
  "events": [ /* NormCursorEvent ... */ ]
}
```

| 项 | 约定 |
|---|---|
| 通道 | 复用 ASR 的 WS（同连接多 `event` 类型）或独立 `ws/cursor`；前端按 `event` 字段路由 |
| `seq` 分配 | 由后端统一入口在落库前分配（与 ASR 共享单调 `seq`，便于时空对齐） |
| 重连 | WS 断线后前端补发未确认 `is_final` 事件（按 `event_id` 幂等） |
| 背压 | 高频 partial 不落总线（仅 final + 可选 highlight）；节流在端已降频 |

---

## 5. 后端摄入（④⑤）

### 5.1 `input_cursor_node`

接收 `cursor.event` 信封，按 `event_id` 去重、按 `seq` 排序，写入 `AgentState.raw_cursor_events`。无副作用、不触发下游。

### 5.2 `filter_cursor_node`（去抖 + partial→final 聚合）

```python
def filter_cursor_node(state):
    raw = state["raw_cursor_events"]
    # 1) 去抖：hover 在节点间快速划过，只保留停留 >= HOVER_SETTLE_MS 的最终节点
    settled = debounce_hover(raw, settle_ms=HOVER_SETTLE_MS)
    # 2) partial→final：聚合拖拽会话（前端已收口，服务端兜底合并残留 partial）
    merged = coalesce_drag(settled)
    # 3) 降采样：纯 mouseup 无位移 / 空白 click 无后续 → 丢弃（保留进 debug 日志）
    focus = [e for e in merged if e["is_final"] and keep(e)]
    return {"filtered_focus": focus}
```

| 处理 | 参数 | 默认值（可调，见 §9） |
|---|---|---|
| `HOVER_SETTLE_MS` | hover 停留多久才算有效焦点 | 250ms |
| `DRAG_PX_THRESHOLD` | 位移多少 px 才算 drag 而非 click | 8px |
| `VIEW_MODE_SAMPLE_RATE` | 查看模式下降采样比例 | 0.3（仅保留强意图） |

### 5.3 触发策略

图推理**仅由 ASR `is_final` 批次触发**；光标流不再独立触发（调研 Q4，已闭合，见 §12）。鼠标动作随语音时间段窗口拼入对应 utterance（§7 的 `mouse_action`），作为该句的附加上下文，而非独立驱动一次推理。这样避免「用户随手划过」也触发昂贵的图推理。

---

## 6. 与 `update_node` 软保护集成

当 `filtered_focus` 中某事件的 `gesture_type ∈ {drag_node, hold_node}` 且 `target.node_id` 命中某节点，`update_node` 在应用 `GraphUpdateOp` 时**跳过该节点的位置/类型/文本写回**（运行时临时软保护，与 `Design_StructureGraph_Storage.md` §7 `data.edit.*` 同源）：

```python
def update_node(state):
    protected = {e["target"]["node_id"] for e in state["filtered_focus"]
                 if e["gesture_type"] in ("drag_node", "hold_node")}
    for op in state["update_patch"]["operations"]:
        if op.get("node") in protected and op["op"] in ("replace",):
            op["skip_fields"] = ["label", "type", "position"]   # 不破坏用户正在操作的节点
    apply_patch(state["board_graph"], state["update_patch"])
```

> 手势软保护是**临时的、由交互触发**；用户显式 `data.lock` 是**持久的**。冲突仲裁见 `Design_FrontendBoard.md` §7。

---

## 7. 双流拼接：鼠标动作按语音时间段窗口拼入 utterance（替代原 (d) 路全局焦点）

光标流**不独立注入一条全局焦点注记**，而是按「语音转文字的时间段」窗口化拼接到**对应 utterance 字典**中，作为该句话的附加上下文。核心规则：

- **窗口**：以 utterance 的 `[start_offset_ms, end_offset_ms]` 为基准，**前后各扩展 `CURSOR_FUSION_WINDOW_MS`（默认 1000ms）**，即 `[start-1000, end+1000]`。
- **来源**：窗口内的 `filtered_focus`（已去抖/聚合的 hover / click / dblclick / drag_node 等 final 事件，见 §5.2）。
- **落点**：在 utterance 字典上增加 `mouse_action` 字段（列表），不入独立 (d) 路。

### 7.1 `mouse_action` 字段结构

```json
{
  "utterance_id": "u_8f3a2c...",
  "text": "这个项目下周要出第一版原型。",
  "start_offset_ms": 305000, "end_offset_ms": 306300,
  "mouse_action": [
    { "ts": 304120, "gesture": "hover",     "node": "n_issue_q3" },
    { "ts": 305080, "gesture": "drag_node", "node": "n_issue_q3", "from": [412,268], "to": [430,255] },
    { "ts": 306300, "gesture": "dblclick",  "node": "n_evidence_2" }
  ]
}
```

- `ts`：相对会议起点的毫秒偏移（与 `start_offset_ms` 同基准，可直接比较是否在窗口内）。
- `gesture` + `node`：结构化手势与命中节点（空白手势 `node: null`）；`drag_node` 附带 `from`/`to` 落点坐标。
- 仅窗口内事件入列；相邻 utterance 窗口可能重叠，同一光标事件可出现在两条 utterance 的 `mouse_action` 中（有界重复，可接受）。
- 该字段已挂在 ASR 统一格式的 utterance 对象上（见 `ASR_UnifiedSchema.md` §2.1 `mouse_action` 可选槽位），本设计在 `assemble` 拼装时填入即可。

### 7.2 提示词拼装（替换原 (d) 路）

`serializeForCursor` 不再产出全局焦点块，改为**逐句内联**：

```
[00:05.0–00:06.3 | 张三] 这个项目下周要出第一版原型。
  鼠标动作（语音段 ±1s）：
   · @304.1s hover n_issue_q3（议题「Q3 渠道策略」）
   · @305.1s drag_node n_issue_q3 (412,268)→(430,255)
   · @306.3s dblclick n_evidence_2
```

无语音伴随（静默）或窗口内无光标事件时，该 utterance 无 `mouse_action` 字段（或空列表），提示词退化为纯文本，行为不变。

> 边界：`filtered_focus` 仍同时服务于 §6 软保护（服务端实时跳过写回，**不进 LLM**）；进入 LLM 的仅是 §7 窗口化 `mouse_action`。两者职责分离——软保护用全量 final 事件，LLM 上下文用窗口化切片。

---

## 8. 异常与边界

| 场景 | 处理 |
|---|---|
| 鼠标移出窗口 | 前端停采；不产 `hover` final 事件（避免噪声） |
| 多端协作 | `actor.device` 区分（命名/唯一性见 §3.1）；同节点多人操作以 `(user_ref, device)` 最后 `is_final` 为准 |
| 拖拽跨越节点 | 以 `mousedown` 的 `target.node_id` 为手势主体（拖出后仍记为原节点操作） |
| WS 断线重连 | 补发未确认 `is_final` 事件，按 `event_id` 幂等去重 |
| 乱序到达 | 按 `seq` 重排；partial 先于 final 到达则暂存等待收口 |

---

## 9. 配置参数表

| 参数 | 含义 | 默认 | 调优方向 |
|---|---|---|---|
| `THROTTLE_MS` | 采集端 mousemove 节流 | 200ms | 降网络压力；过小则浪费带宽 |
| `HOVER_SETTLE_MS` | hover 有效停留阈值 | 250ms | 过小则划过即噪声焦点 |
| `DRAG_PX_THRESHOLD` | drag/click 位移阈值 | 8px | 过小则点击变拖拽 |
| `VIEW_MODE_SAMPLE_RATE` | 查看模式降采样 | 0.3 | 会议中防误触，降推理频率 |
| `CURSOR_FUSION_WINDOW_MS` | 鼠标动作拼入语音段的前后窗口 | 1000ms | 语音段前后各扩展 1s；过大则噪声增多、过小则漏上下文 |
| `CURSOR_INDEPENDENT_TRIGGER` | 光标是否独立触发图推理 | false | 已闭合（§12 Q4）：光标随语音窗口拼入，不独立触发 |
| `CURSOR_DEBUG_SAMPLE` | debug 采样日志常态采集 | false | 已闭合（§12 Q6）：默认仅降采样 final 入库、partial 丢弃；需排障/灰度时开启 |

---

## 10. 服务/接口清单

| 接口 | 方向 | 说明 |
|---|---|---|
| `WS /ws/cursor`（或复用 ASR WS） | 前端→后端 | 推送 `cursor.event` 信封 |
| `POST /api/cursor/debug-sample` | 前端→后端 | 可选：推送被降采样的 partial/噪声事件进 debug 日志 |
| `GET /api/cursor/config` | 后端→前端 | 下发 `HOVER_SETTLE_MS` 等运行参数（A/B 调优） |

---

## 11. 与既有文档一致性

| 本模块 | 关联文档 |
|---|---|
| `NormCursorEvent` 同形格式 | `Research_CursorIntent_Capture.md` §2 |
| `CursorEventAdapter` 同构 ASR Adapter | `ASR_UnifiedSchema.md` §3 |
| 对应 `input_cursor_node` / `filter_cursor_node` | `Design_Agent_DataFlow.md` §2.2–§2.4 |
| X6 事件源 / `interacting` 模式 | `Research_X6_MindMap.md` §6 |
| 拖拽软保护 | `Design_StructureGraph_Storage.md` §7 |
| 鼠标动作按语音段窗口拼入 utterance（`mouse_action`） | 本设计 §7（替代原 `Research_CursorIntent_Capture.md` §6 的全局 (d) 路焦点注记） |

---

## 12. 待确认 / 开放项

- Q1（已闭合，见正文默认）：`seq` 跨 ASR/光标**共享单一单调序号**，由统一入口分配（§3 示例 `seq` 已混编），乱序按 `seq` 重排（见 §8）。
- Q2（已闭合，见 §3.1）：`actor.user_ref` 前缀判定树（`ent:`/`local:<machine_guid>`/`anon:`）与 `device` 多端命名/唯一性规则已写入 §3.1，原 `local:`/`anon:` 二选一歧义收口为 `local:` 常态、`anon:` 兜底。
- Q3（已闭合，数值待 M1 回填）：`HOVER_SETTLE_MS`=250ms / `DRAG_PX_THRESHOLD`=8px 为默认起点；M1 用 debug 采样统计 hover 停留分布与 drag 位移幅度，按「误划过分位之上、有意图分布之下」与「点击微抖分位之上、真实小拖拽之下」校准（流程见 §5.2），回填最终数值。
- Q4（已闭合，见 §7/§9）：光标**不独立触发**图推理，改为按语音时间段窗口拼入对应 utterance 的 `mouse_action`（前后各 1s）。原 (d) 路全局焦点注记废弃。
  - 残留子问题（已闭合）：静默中无语音伴随的纯手势（如用户拖拽节点但没说话）**不触发图推理**，节点移动由前端 X6 直接落图；默认**不保留**「无语音强手势触发」开关（保持主链路纯净），将来若需 Agent 主动响应此类手势再单独评估、不影响当前设计。
- Q5（已闭合，见 §3）：`intent_hint` 默认关闭、字段保留 `null` 且 `filter_cursor_node` 不填充；后续启用须以**代码固定映射流程**实现（事件→确定性意图枚举），不走 LLM。
- Q6（已闭合，见 §9/§10）：debug 采样日志**默认不常态全量采集**——仅降采样后的 final 事件入库，被降采样的 partial/噪声事件默认丢弃；`POST /api/cursor/debug-sample`（§10）保留为**按需开关**（关联 Q3 阈值校准、§6 软保护排障），由 `CURSOR_DEBUG_SAMPLE` 控制，默认 false。
