# 鼠标交互意图采集功能设计方案调研

> **文档状态**：调研 v0.1 · 待评审
> **最后更新**：2026-09-16
> **关联文档**：
> - `doc/ASR_UnifiedSchema.md`（ASR 统一格式 `NormUtterance`，本设计的同形参照）
> - `doc/Design_Agent_DataFlow.md`（Agent 数据流与 LangGraph 编排；本设计取代其 v0.2 §2.4 的「运行态副信号」模型）
> - `doc/Research_X6_MindMap.md`（AntV X6 交互事件来源）
> - `doc/Design_StructureGraph_Storage.md`（Store A 关系图 / Store B 元数据 / `data.edit.*` 软保护）
> **调研目标**：把「用户鼠标焦点 / 左键手势」从运行态副信号，提升为**与 ASR 流同等级的一等输入流**，并用与 `NormUtterance` 同形的归一化事件格式传入 Agent，使其与语音流在同一总线、同一套流处理机制（seq / 去重 / 乱序 / 触发）下融合。

---

## 0. 结论（TL;DR）

1. **同级别定位**：鼠标交互不再是 `assemble` 的副注记（v0.2 §2.4 的 `interaction_signal` 运行态缓冲），而是与 ASR 并列的**对等输入流**——前端 `CursorEventAdapter` 产出 `NormCursorEvent`，走「归一化事件 → 统一总线 → Agent」的同构管线。
2. **同形格式**：`NormCursorEvent` 字段与 `NormUtterance` **逐字段对齐**（id / meeting_id / session_id / seq / actor / 时间偏移 / phase / source / raw_ref），差异仅在于「内容载体」——ASR 是 `text`，光标是 `gesture_type + target`，见 §2.1 对照表。
3. **流处理对称**：`filter_cursor_node` 与 `filter_node` 平行，复用 partial→final 聚合（高频 `mousemove` 聚合成一条 `drag_node` 事件，类比 ASR 2pass 的 online→offline 段）。
4. **存储对称**：原始光标事件与原始 ASR utterance 一样是**瞬态输入**，不落 Store A（关系图）、不落 Store B（元数据）；只有 Agent 派生的关系图持久化。

---

## 1. 背景与定位：为什么与 ASR 同级别

现有 `Design_Agent_DataFlow.md` 仅把 ASR 当作唯一输入流，鼠标交互在 v0.2 被设计为 `assemble` 前的「运行态焦点缓冲」（`capture_focus_node` 快照 `interaction_signal`）。该模型有两个结构性缺陷：

- **无法复用 ASR 的流处理机制**：ASR 有 `seq` 排序、`utterance_id` 去重、乱序检测、`is_final` 触发下游；鼠标流若只是缓冲里的最新值，就丢失了「事件序列」语义（用户先 hover A、拖 B、双击 C，是有序意图链，不是单点快照）。
- **与 ASR 不对等，难以多模态融合**：当设计演进到「用户在说 X 的同一秒悬停了 Y 节点」这类时空对齐需求时，副信号模型无法在同一 `seq` / 同一时间基准上对齐。

**结论**：把鼠标交互提升为**对等输入流**，与 ASR 共享「归一化事件 → 适配层 → 统一总线 → Agent」范式。这恰好镜像 `ASR_UnifiedSchema.md` 的架构（两套后端各一个 Adapter，Agent 只认中间格式）。

---

## 2. 核心设计：`NormCursorEvent`（与 `NormUtterance` 同形）

Agent 入口消费的最小单元是「一个光标交互事件」，与 ASR 的「一句话」同级。

### 2.1 字段逐一对照表

| `NormUtterance`（ASR） | `NormCursorEvent`（光标） | 差异性质 |
|---|---|---|
| `utterance_id` | `event_id` | 命名不同，角色相同（全局唯一 UUID，Adapter 生成） |
| `meeting_id` | `meeting_id` | 完全相同（会议级主键） |
| `session_id` | `session_id` | 完全相同（子会议/看板会话 ID） |
| `seq` | `seq` | **同会议内跨两流共享单调序号**（见 §8.1），用于时空对齐 |
| `speaker{speaker_ref,source_id,display_name,is_resolved}` | `actor{user_ref,device,is_resolved}` | 操作者=本机用户，多为已登录真人（`ent:`/`local:`），`is_resolved` 通常 true（不像 ASR 匿名 `spk`） |
| `text` | （无自由文本）→ 由 `gesture_type` + `target` 承载 | **内容载体不同**：ASR 是语言文本，光标是结构化手势 |
| `language` | （无） | N/A |
| `start_offset_ms` | `start_offset_ms` | **同基准**：相对会议/看板会话起点的毫秒（与 ASR 对齐，便于时空合并） |
| `end_offset_ms` | `end_offset_ms` | 拖拽=mouseup 时刻；hover/单击=等于 `start_offset_ms` |
| `received_at_ms` | `received_at_ms` | 完全相同（Agent 收到地上时钟，时延核算） |
| `is_final` | `is_final` | 手势完成（drag 的 mouseup / 单击）= true；进行中=false |
| `is_partial` | `is_partial` | 拖拽进行中的 `mousemove` = true（类比 ASR 2pass online 段） |
| `translation` | （无） | N/A |
| `source` | `source` | 取值 `"x6_canvas"` / `"pointer_api"`（可观测/兜底） |
| `raw_ref` | `raw_ref` | 指向原始 X6 事件（事件名 + 原始坐标），调试/对账用 |

> 关键洞察：**两套格式的差异只在「内容字段」（text vs gesture/target）和「主体字段」（speaker vs actor），其余 8 个字段完全同构**。这意味着 Agent 的下游消费（排序、去重、时间对齐、触发）可写一套通用逻辑同时吃两流。

### 2.2 `NormCursorEvent` 结构定义

```json
{
  "event_id": "c_9b2e1f...",            // 全局唯一 UUID（Adapter 生成）
  "meeting_id": "meeting_abc123",       // 与 NormUtterance 同主键
  "session_id": null,                   // 看板会话 ID（同会议多看板时为子会话）
  "seq": 1042,                          // 与 ASR 共享的会议内单调序号

  "actor": {
    "user_ref": "ent:user_zhang",       // ★ 统一操作者主键（本机登录用户，多已解析为真人）
    "device": " desktop_win",           // 操作设备（多端协同时区分）
    "is_resolved": true                 // 是否已映射到企业真人（光标流通常 true）
  },

  "gesture_type": "drag_node",          // hover | click | dblclick | drag_node | hold_node | canvas_hold | resize | collapse
  "target": {                           // 手势作用的对象
    "node_id": "n_issue_q3",            // 命中节点 id（空白手势为 null）
    "node_type": "issue",              // 节点类型（便于 LLM 快速判断）
    "edge_id": null                     // 命中边 id（极少）
  },
  "pointer": { "x": 412, "y": 268 },    // 相对画布坐标（落点）
  "intent_hint": null,                  // 可选：前端附加语义（如 dblclick→"查看论据"），基础版不依赖

  "start_offset_ms": 305120,            // ★ 相对会议/看板起点的毫秒（与 ASR 同基准）
  "end_offset_ms": 306340,
  "received_at_ms": 1726483506123,

  "is_final": true,                     // 手势完成（drag 的 mouseup）→ 触发下游
  "is_partial": false,                  // 拖拽进行中 mousemove → true

  "source": "x6_canvas",
  "raw_ref": "x6:node:mousedown#n_issue_q3@412,268"
}
```

### 2.3 关键语义差异与处理

- **操作者多为已解析真人**：光标流 `actor.is_resolved` 通常 `true`（本机登录用户），不像 ASR 的 `spk:` 匿名。因此 `user_ref` 可直接用于「该用户正在操作」的归因，无需 Resolver 声纹库。前缀规则沿用 `ASR_UnifiedSchema.md` §4：`ent:`（企业真人）/ `local:`（本机未登录）/ 极少数 `anon:`。
- **无自然语言文本**：内容由 `gesture_type + target` 结构化表达。基础版**不依赖**任何语义推断；若未来要「用户把节点拖到空白=想新建游离子图」这类高级意图，由前端写 `intent_hint`（可选字段），Agent 保守忽略未知 hint。
- **时间基准与 ASR 对齐**：`start_offset_ms/end_offset_ms` 相对同一会议起点，使 `seq` 与 `offset` 可在提示词里和转写句对齐（例：「用户 hover `n_issue_q3` 的同时，张三正说到『成本』」）。
- **瞬时性（与 ASR 对称）**：原始光标事件与原始 ASR utterance 一样不落库；只有 Agent 派生的关系图（Store A）持久化。光标事件本身可选进 debug 采样日志（独立 collection，不进主链路）。

---

## 3. 与 ASR 同级别的数据流编排

### 3.1 双对等输入流

```
                         ┌─ ASR 流 (NormUtterance) ─▶ [filter] ──────▶ filtered_text + meta_ids ─┐
[统一入口 / 双流注入] ────┤                                                                          │
                         └─ 光标流 (NormCursorEvent) ▶ [filter_cursor] ▶ filtered_focus ──────────┤
                                                                                                  ▼
                                                                             [assemble 四路合并] → reason → parse → update(Store A)
                                                                                  ↑ 事件触发（ASR 批次 / 光标 is_final 均可触发）
```

两条流**对等**：都从归一化事件进入、都过各自的 filter、都汇入 `assemble`。触发策略（Design §4.2）扩展为「ASR `is_final` 批次 **或** 光标 `is_final` 手势」任一到达即触发一次图推理。

### 3.2 LangGraph State 扩展

```python
class AgentState(TypedDict):
    meeting_id: str
    meeting_title: str

    # —— 输入（双对等流）——
    raw_utterances: List[NormUtterance]      # ASR 流（ASR_UnifiedSchema）
    raw_cursor_events: List[NormCursorEvent] # ★ 光标流（本文档），与 raw_utterances 同级
    filtered_text: List[str]                 # [filter] 输出
    filtered_meta_ids: List[str]             # [filter] 输出 → metadata_refs
    filtered_focus: List[NormCursorEvent]    # [filter_cursor] 输出（已去抖/聚合）

    # —— 关系图 / 提示词 / 推理 / 输出 —— （其余字段不变）
    board_loaded: bool
    board_graph: Optional["BoardGraph"]
    system_prompt: str
    llm_messages: List[dict]
    llm_output: Optional["GraphUpdateOp"]
    parse_ok: bool
    retry_count: int
    update_patch: Optional[dict]
```

> **废弃 v0.2 的 `interaction_signal` / `capture_focus_node`**：鼠标流不再经运行态缓冲快照，而是作为 `raw_cursor_events` 进入 State，与 `raw_utterances` 同地位。

### 3.3 Nodes（与 `filter_node` 平行）

| 节点 | 职责 | 输入←State | 输出→State |
|---|---|---|---|
| `input_node` | 接收 ASR 批次 | `raw_utterances` | — |
| `input_cursor_node` | 接收光标批次 | `raw_cursor_events` | — |
| `filter_node` | 过滤无意义语音 | `raw_utterances` | `filtered_text`, `filtered_meta_ids` |
| `filter_cursor_node` | 去抖 + 聚合手势（见 §3.4） | `raw_cursor_events` | `filtered_focus` |
| `load_board_node` / `gen_initial_node` | （不变） | — | — |
| `assemble_node` | 四路合并（(d) 源改为 `filtered_focus`） | `system_prompt`,`board_graph`,`filtered_text`,`filtered_focus` | `llm_messages` |
| `reason_node` / `parse_node` / `update_node` | （不变） | — | — |

### 3.4 `filter_cursor_node` 的流处理（复用 ASR partial→final 范式）

- **去抖（debounce）**：hover 在节点间快速划过（`mouseenter/leave` 抖动）只保留最终停留节点，避免噪声刷屏。
- **partial→final 聚合**：拖拽过程中的连续 `mousemove` 标记为 `is_partial=true`；`mouseup` 产生一条 `is_final=true` 的 `drag_node` 事件（起点=按下坐标，终点=释放坐标，时长=`end-start`）。这与 ASR 2pass 的 online→offline 段完全同构。
- **丢弃无关注释**：纯 `mouseup` 无位移、或 `click` 在空白处无后续动作，可降采样（不进 `filtered_focus`，但保留进 debug 日志）。
- **依赖 `is_final` 触发**：只有 `is_final` 的光标事件参与 `assemble` 与下游触发，进行中事件仅用于（可选）实时高亮，不进入 LLM 上下文。

---

## 4. 前端采集层：`CursorEventAdapter`（类比 ASR Adapter）

与 `ASR_UnifiedSchema.md` §3 的 `TencentASRAdapter` / `FunASRAdapter` 同构：监听原始 X6 事件 → 填 `NormCursorEvent` → 发往统一总线。

### 4.1 X6 事件 → `NormCursorEvent` 映射

| X6 事件 | `gesture_type` | `target.node_id` | `is_partial` / `is_final` |
|---|---|---|---|
| `node:mouseenter` | `hover` | 该节点 id | final（停留即定） |
| `node:mouseleave` | `hover` | null（离开） | final |
| `node:click` | `click` | 该节点 id | final |
| `node:dblclick` | `dblclick` | 该节点 id | final（可带 `intent_hint="查看论据"`） |
| `node:mousedown`（左键） | `drag_node`（起始） | 该节点 id | partial |
| `node:mousemove`（按住中） | `drag_node` | 该节点 id | partial（多次） |
| `node:mouseup` | `drag_node`（结束） | 该节点 id | final（结算 duration） |
| `node:collapse`（子树折叠） | `collapse` | Group 节点 id | final |
| `blank:mousedown` / `blank:mousemove` | `canvas_hold` | null | partial→final |

### 4.2 节流 vs 聚合位置

- **采集端（前端）**：`mousemove` 高频，前端 **throttle ≈ 50–100ms** 降频后再推 WS/SSE（仅降网络/计算压力，不丢语义）。
- **聚合端（`filter_cursor_node`）**：负责 partial→final 合并（语义层），与 ASR Adapter 把 2pass 段聚合成 `is_final` 句子的位置一致。**原则**：降频在端、聚合在服务端，与 ASR 管线分工对齐。

### 4.3 与 X6 `interacting` 编辑/查看模式联动

`Research_X6_MindMap.md` 记录了 X6 的 `interacting` 开关（编辑/查看模式）：
- **编辑模式**（复盘/手动整理）：光标事件全量采集，用户真实操作即意图。
- **查看模式**（会议中进行中防误触）：光标事件可降采样或停采（避免无意识划过产生噪声焦点）。
- 软保护仍生效：当 `gesture_type ∈ {drag_node, hold_node}` 命中某节点，`update_node` 跳过该节点位置/类型/文本写回（同源 `Design_StructureGraph_Storage.md` §7 `data.edit.*`）。

---

## 5. 瞬时性与存储边界（与 ASR 对称）

| 数据 | 是否落库 | 说明 |
|---|---|---|
| 原始 `NormCursorEvent` | 否（瞬态输入） | 与原始 ASR utterance 同地位；可选进 debug 采样日志（独立 collection） |
| `filtered_focus`（聚合后） | 否 | 仅用于当次 `assemble` 注入 |
| 关系图（Store A） | 是 | 唯一真相源，光标流只影响「挂接位置/软保护」，不入库原始事件 |
| 元数据（Store B） | 否 | 仅存语音文本论据，`metadata_refs` 不挂光标事件 id |

> 对称论证：ASR 原始 utterance 也不直接落 Store B——Store B 存的是「精炼后的论据文本」，光标原始事件同理。两者都是「喂给 Agent 的瞬态输入」，落库的是 Agent 的**派生产物**（关系图）。

---

## 6. 提示词注入格式（`serializeForCursor`）

类比 `Design_StructureGraph_Storage.md` §6.2 的 `serializeForLLM`，把 `filtered_focus` 压成紧凑焦点注记注入 `assemble` 第 (d) 路：

```
[用户当前焦点 / 光标交互]
- 悬停节点：n_issue_q3（议题「Q3 渠道策略」）@ 305.1s
- 手势：左键拖拽该节点 drag_node，持续 1.2s @ 305.1–306.3s
建议：新要点优先挂接至 n_issue_q3 附近；请勿移动或改写用户正在操作的节点。
```

无 `filtered_focus`（用户无操作）时第 (d) 路空白，提示词退化为三路，行为不变。

---

## 7. 与既有文档一致性

| 本设计 | 关联文档 |
|---|---|
| `NormCursorEvent` 与 `NormUtterance` 同形 | `ASR_UnifiedSchema.md` §2 |
| `CursorEventAdapter` 同构于 ASR Adapter | `ASR_UnifiedSchema.md` §3 |
| 取代 v0.2 §2.4 运行态副信号模型 | `Design_Agent_DataFlow.md`（升级到 v0.3 对等流） |
| X6 事件来源 / `interacting` 模式 | `Research_X6_MindMap.md` |
| 拖拽中软保护（不写回） | `Design_StructureGraph_Storage.md` §7 `data.edit.*` |
| 四路提示词注入 | `Design_Agent_DataFlow.md` §2 / Storage §6.2 |

---

## 8. 待确认

- Q1：`seq` 跨 ASR 与光标两流是否**共享单一单调序号**？共享则时空对齐最简单（但需双生产者协调）；分流域则各自 `seq` 但靠 `offset_ms` 对齐。建议共享，由统一入口分配。
- Q2：`actor.user_ref` 前缀规则——本机未登录时用什么（`local:` / `anon:`）？多端协作时 `device` 如何区分？
- Q3：`filter_cursor_node` 的**去抖窗口 / 聚合阈值**取值（hover 停留多少 ms 才算有效焦点？拖拽位移多少 px 才算 drag 而非 click？）。
- Q4：是否允许**光标 `is_final` 独立触发**一次图推理（用户拖完节点立即重排/挂接），还是仅作为 ASR 批次的附加上下文（不独立触发）？前者延迟更低但推理更频繁。
- Q5：`intent_hint` 是否启用——基础版建议关闭，仅保留结构化 `gesture_type + target`，避免前端过度推断意图污染 LLM。
