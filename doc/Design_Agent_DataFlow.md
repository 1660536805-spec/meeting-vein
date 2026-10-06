# Agent 数据流图与 LangGraph 编排设计

> **文档状态**：草稿 v0.5 · 待评审
> **最后更新**：2026-09-18（v0.5：双 Agent 流水线顺序重排——分析 Agent 仅消费「输入信息」→ 会议纪要；规划 Agent 消费「会议纪要 + 当前关系图」→ `GraphUpdateOp`；关系图数据再流转至流程图展示模块。光标流改为窗口化 `mouse_action` 随 ASR 文本进入分析 Agent，不再独立第 (d) 路；**新增 §2.5 提示词拼接顺序的 KV 缓存 / sglang 树形优化原则**（stable→semi→variable 铁律）**）
> **关联文档**：`doc/Design.md`（§4.1 双通道、§4.2 触发策略、§4.4 字段级权限、§6.2 统一 LLM 接入）、`doc/Design_StructureGraph_Storage.md`（Store A 关系图 / Store B 元数据 / metadata_refs / lock/edit）、`doc/ASR_UnifiedSchema.md`（NormUtterance）；模块详细设计：`doc/Design_CursorCapture.md`（鼠标采集）、`doc/Design_InputProcessing.md`（双 Agent 处理）、`doc/Design_Mascot.md`（看板娘）、`doc/Design_FrontendBoard.md`（前端看板）；调研：`doc/Research_CursorIntent_Capture.md`、`doc/Research_KanbanMusume.md`
> **目标**：把「语音文本 → 关系图更新」的 Agent 流转画清楚，并直接映射为 LangGraph 的 `StateGraph`（State / Node / Edge / 条件路由），供后续编码编排。

---

## 1. 数据流总览（对应需求描述）

```
                 ┌─ ASR 流 (NormUtterance, 内嵌窗口化 mouse_action) ─▶ [1] 无意义语音过滤 ─▶ filtered_text ─┐
数据输入(双流注入) ┤                                                                                    │
                 └─ 光标流 (NormCursorEvent) ─▶ [1b] 光标过滤聚合 ─▶ filtered_focus（仅服务软保护/debug，不进 LLM）─┘
                                                                                    │
                                                                                    ▼
                                          [2a] 组装分析 Agent 提示词（仅输入信息：系统 + 过滤文本[含 mouse_action]）
                                                                                    ▼
                                          [3a] 分析 Agent：据输入信息整理 → 会议纪要（MeetingSummary）
                                               │  ★ 纯纪要职责：不加载 board_graph、不做图规划
                                                                                    ▼
                                          [2b] 组装规划 Agent 提示词（系统 + 当前关系图 board_graph + 会议纪要）
                                                                                    ▼
                                          [3b] 规划 Agent：据会议纪要规划 → 关系图数据（GraphUpdateOp）
                                                                                    ▼
                                          [4] 解析与校验 GraphUpdateOp
                                                                                    ▼
                                          [5] 更新关系图(Store A)（尊重 edit/lock + 手势软保护）
                                                                                    ▼
                                          [6] 关系图数据流转到流程图展示模块（Design_FrontendBoard.md）做展示
```

> 编排是**事件驱动**的，且**双 Agent 职责严格分离**：分析 Agent 只消费「输入信息」（ASR 过滤文本，已内嵌按语音段窗口化的 `mouse_action`）产出会议纪要，不接触 `board_graph`；规划 Agent 消费「会议纪要 + 当前关系图状态」产出 `GraphUpdateOp`，再经 [5] 落库、[6] 推送给流程图展示模块。`board_graph` 在 Store A 持久化，跨批次保留。

---

## 2. LangGraph 映射

### 2.1 State（跨节点共享状态）

```python
from typing import TypedDict, List, Optional

class AgentState(TypedDict):
    meeting_id: str
    meeting_title: str

    # —— 输入（双对等流）——
    raw_utterances: List[NormUtterance]      # ASR 流（ASR_UnifiedSchema）
    raw_cursor_events: List[NormCursorEvent] # ★ 光标流（与 ASR 同级，见 Research_CursorIntent_Capture）
    filtered_text: List[str]                 # [1] 过滤后文本
    filtered_meta_ids: List[str]             # 过滤句对应的 meta_id（→ metadata_refs 绑定）
    filtered_focus: List[NormCursorEvent]    # [1b] 过滤聚合后的光标焦点（去抖/partial→final）

    # —— 关系图 ——
    board_loaded: bool                       # 是否已有预载入关系图
    board_graph: Optional["BoardGraph"]      # Store A cells（预载入或初始生成）

    # —— 提示词与推理（双 Agent）——
    system_prompt: str                       # 绘图需求 + 输出 schema 规则
    llm_messages_analyze: List[dict]         # [2a] 组装_分析 消息
    meeting_summary: Optional["MeetingSummary"]  # ★ 分析 Agent 输出（双 Agent 交接物）
    llm_messages_sync: List[dict]            # [2b] 组装_同步 消息
    llm_output: Optional["GraphUpdateOp"]    # [3b] 规划 Agent 结构化输出
    parse_ok: bool                           # [4] 校验结果
    retry_count: int

    # —— 输出 ——
    update_patch: Optional[dict]             # 应用到 Store A 的增量

```

### 2.2 Nodes（图节点 = 处理函数）

| 节点 | 职责 | 输入←State | 输出→State |
|---|---|---|---|
| `input_node` | 接收 ASR 批次 | `raw_utterances` | — |
| `input_cursor_node` | 接收光标批次 | `raw_cursor_events` | — |
| `filter_node` | 过滤无意义语音（规则/低档 LLM） | `raw_utterances` | `filtered_text`, `filtered_meta_ids` |
| `filter_cursor_node` | 去抖 + 手势聚合（partial→final） | `raw_cursor_events` | `filtered_focus` |
| `load_board_node` | 从 Store A 载入关系图 | `meeting_id` | `board_graph`, `board_loaded=True` |
| `gen_initial_node` | 无预载入时按会议标题生成初始 issue 节点 | `meeting_title` | `board_graph`(初始), `board_loaded=True` |
| `assemble_analyze_node` | 组装分析 Agent 提示词（**仅输入信息**：系统 + 过滤文本[内嵌窗口化 mouse_action]；**不加载 board_graph**） | `system_prompt`,`filtered_text` | `llm_messages_analyze` |
| `analyze_node` | **分析 Agent**：仅据输入信息整理会议纪要（论点/论据/关联/归因）→ `MeetingSummary`（★ 不加载 board_graph、不做图规划） | `llm_messages_analyze` | `meeting_summary` |
| `assemble_sync_node` | 组装规划 Agent 提示词（系统 + 当前 board_graph + 会议纪要 MeetingSummary；光标仅作软保护上下文） | `system_prompt`,`board_graph`,`meeting_summary` | `llm_messages_sync` |
| `sync_node` | **规划 Agent**：据会议纪要 + 当前图规划 → 关系图数据 `GraphUpdateOp` | `llm_messages_sync` | `llm_output` |
| `parse_node` | 校验 `GraphUpdateOp` schema | `llm_output` | `parse_ok`,`update_patch` |
| `update_node` | 应用增量到 Store A（尊重 edit/lock + 光标软保护，追加 metadata_refs） | `update_patch`,`filtered_focus` | 持久化 |
| `present_node` | 将关系图数据（board_graph 快照/增量）流转到**流程图展示模块**（`Design_FrontendBoard.md`）做展示 | `board_graph`,`update_patch` | — |

### 2.3 Edges 与条件路由

```python
from langgraph.graph import StateGraph, END

g = StateGraph(AgentState)
g.add_node("input",           input_node)
g.add_node("input_cursor",    input_cursor_node)   # ★ 光标流入口（与 ASR 同级）
g.add_node("filter",          filter_node)
g.add_node("filter_cursor",   filter_cursor_node)  # ★ 光标过滤聚合
g.add_node("load_board",      load_board_node)
g.add_node("gen_initial",     gen_initial_node)
g.add_node("assemble_analyze", assemble_analyze_node)
g.add_node("analyze",          analyze_node)        # ★ 分析 Agent
g.add_node("assemble_sync",    assemble_sync_node)
g.add_node("sync",             sync_node)           # ★ 规划 Agent
g.add_node("parse",           parse_node)
g.add_node("update",          update_node)
g.add_node("present",         present_node)        # 关系图数据 → 流程图展示模块

g.set_entry_point("input")
g.add_edge("input", "filter")
g.add_edge("input_cursor", "filter_cursor")        # 双流平行，互不阻塞

# 条件分支：有预载入关系图？→ 载入；否则生成初始节点
def route_init(state):
    return "load_board" if state["board_loaded"] else "gen_initial"
g.add_conditional_edges("filter", route_init,
                        {"load_board": "load_board", "gen_initial": "gen_initial"})

g.add_edge("load_board", "assemble_analyze")
g.add_edge("gen_initial", "assemble_analyze")
# （光标流不再汇入 assemble：窗口化 mouse_action 已内嵌于 ASR 文本；filtered_focus 仅服务 update_node 软保护）
g.add_edge("assemble_analyze", "analyze")
g.add_edge("analyze", "assemble_sync")              # ★ 双 Agent 交接：会议纪要传入规划 Agent
g.add_edge("assemble_sync", "sync")
g.add_edge("sync", "parse")

# 条件分支：校验通过→更新；失败→重试（限次）回 analyze 重抽，或降级丢弃
def route_parse(state):
    if state["parse_ok"]:
        return "update"
    return "assemble_analyze" if state["retry_count"] < MAX_RETRY else "update"  # 降级：仅更新已可靠部分
g.add_conditional_edges("parse", route_parse,
                        {"update": "update", "assemble_analyze": "assemble_analyze"})
g.add_edge("update", "present")                     # 落库后推送给流程图展示模块
g.add_edge("present", END)   # 等待下一批次再次 entry
```

> `update → present → END` 表示单次批次处理结束：先落库 Store A，再把关系图数据推送给流程图展示模块；下一次触发**仅来自 `input`（ASR `is_final` 批次）**，`board_graph` 优先从 checkpointer 取（见 §5 Q2 已闭合：per-meeting `thread_id` 热缓存，仅冷启动/新会议从 Store A 载入）。光标流（`input_cursor`）到达只刷新 `filtered_focus` 供 `update_node` 软保护，不独立触发推理。这与 Design §4.2「事件触发」一致，避免无意义的轮询。

---

### 2.4 鼠标交互意图采集（与 ASR 同级别的对等输入流）

> **v0.3 修订**：本模块不再以「运行态焦点缓冲 + `capture_focus_node` 快照」实现，而是升级为**与 ASR 同等级的一等输入流**。完整设计见 `Research_CursorIntent_Capture.md`，以下为编排层要点。

**核心变更**（对比 v0.2）：
- 鼠标交互以归一化事件 `NormCursorEvent` 流入，与 `NormUtterance` **字段同形**（id / meeting_id / session_id / seq / actor / 时间偏移 / phase / source / raw_ref），仅「内容载体」不同（ASR=text，光标=gesture_type+target）。
- 前端 `CursorEventAdapter`（类比 ASR Adapter）产出 `NormCursorEvent` → 统一总线 → `input_cursor_node` → `filter_cursor_node`（去抖 + partial→final 聚合，复用 ASR 范式）→ 仅服务 `update_node` 软保护（不注入 LLM 提示词；窗口化 `mouse_action` 已随 ASR 文本进入分析 Agent）。
- 触发策略：**仅 ASR `is_final` 批次触发**一次图推理（见 `Design_CursorCapture.md` §7/§9，`CURSOR_INDEPENDENT_TRIGGER=false`）；光标流到达只刷新 `filtered_focus` 供 `update_node` 软保护，不独立触发推理。
- 瞬态对称：原始 `NormCursorEvent` 与原始 ASR utterance 一样不落 Store A/B，只有派生关系图持久化。

**软保护（与 Storage §7 同源）**：当 `gesture_type ∈ {drag_node, hold_node}` 命中某节点，`update_node` 跳过该节点位置/类型/文本写回（运行时临时）。**仲裁（§5 Q6 已闭合）**：手势软保护以用户手动加锁/解锁为准——用户显式 `data.lock` 为权威源，手势软保护仅作用于**未锁**节点、永不覆盖 lock；冲突时 lock 优先。

**提示词注入**：光标流不再以独立「第 (d) 路全局焦点」注入 LLM。窗口化 `mouse_action` 已随 ASR 文本按语音段拼入（见 `Design_CursorCapture.md` §7），作为分析 Agent 的「输入信息」一部分；`filtered_focus` 仅用于 `update_node` 软保护（拖拽中跳过写回），不进 LLM。

**前端事件源**（X6，详见 `Research_X6_MindMap.md` / 调研文档 §4.1）：`node:mouseenter/leave`→hover、`node:mousedown`(左键)→drag 起点、`node:mousemove`(按住)→partial、`node:mouseup`→final、`node:dblclick`→查看论据、`blank:mousedown/move`→canvas_hold。采集端 throttle≈50–100ms 降频，partial→final 聚合并入 `filter_cursor_node`。

---

### 2.5 提示词拼接顺序的 KV 缓存 / sglang 树形优化原则

> **底层依据**：本流水线所有 `assemble_*` 节点的段顺序不是按「人类阅读习惯」排的，而是为 **KV cache 前缀复用** 与 **sglang RadixAttention 树形 KV 共享** 设计。顺序一旦破坏，前缀缓存命中率为零，双 Agent 的延迟与费用直接翻倍。实现层必须按本节铁律拼接。

**原理（sglang RadixAttention）**
- sglang 用**基数树（radix tree）**按 token 序列精确管理 KV cache；多条请求共享前缀时，前缀 KV **只算一次**，后续请求直接复用。
- 前缀必须**逐 token 完全一致**才命中——前缀区任意一个 token 变化都会让整段前缀失效（后续所有稳定段一并失效）。
- 缓存按 LRU 淘汰；**前缀越长、命中率越高**，节省越多。

**拼接顺序铁律：`stable → semi-stable → variable`**

| 段位 | 内容 | 变化频率 | 位置 |
|---|---|---|---|
| 最稳定段 | 系统指令 + 输出 schema 规则 + 固定 few-shot | 跨批次/跨会议不变 | 永远最前（全局长前缀，必命中） |
| 半稳定段 | 仅低频变化的内容（规划 Agent 的 `board_graph`：仅落库 `update` 后变一次） | 低 | 中段 |
| 最善变段 | 本批次新输入（`filtered_text`+`mouse_action` / `MeetingSummary`） | 每批次全新 | 永远最后（失效后缀压到最短） |

**本流水线落点**
- **分析 Agent（`assemble_analyze`）**：`[1] system_prompt_analyze` → `[2] filtered_text[+mouse_action]`。
  - ★ **不加载 `board_graph` 是 KV 双赢**：若载入，prompt 随图增长无限膨胀、前缀失效、缓存收益归零；现在变量段被限定为「当前批次文本」，系统段稳定可全量缓存，跨批次零重算。
- **规划 Agent（`assemble_sync`）**：`[1] system_prompt_sync` → `[2] board_graph` → `[3] meeting_summary`。
  - ★ **顺序不可乱**：`board_graph` 必须在 `meeting_summary` 之前。若把 summary 插到 system 与 board_graph 之间，每次 summary 不同会使 board_graph 段因前缀断裂而失效，中段缓存复用归零。
  - system+schema 前缀跨批次稳定可缓存；board_graph 段变化频率低（仅每批次 `update_node` 落库后下次才变）；summary 每批次新，放最后。

**树形优化的延伸（sglang tree-structured generation）**
- `MeetingSummary` 是确定性、自包含的中间产物（§3.3 v2），天然适合作为树的分支点：同一纪要 + 不同初始 board / 不同策略 → 从同一 summary 分支，复用「system+summary」前缀，仅末段分叉。
- 多候选 `GraphUpdateOp`：规划 Agent 用 sglang branching / constrained decoding 在末尾生成多个候选 op，前缀（system+board+summary）共享、仅末段分叉——契合树形 KV 复用（详见 `Design_InputProcessing.md` §3.4）。

**反模式（必须避免）**
- 在前缀区插入 batch 序号 / 时间戳 / 随机 few-shot → 直接废掉前缀缓存。
- 变量内容（当前文本）插在稳定段之间（夹心结构）→ 后续所有稳定段因前缀断裂全部失效。
- 分析 Agent 加载 `board_graph` / 规划 Agent 把 `summary` 置于 `board_graph` 之前 → 破坏中段缓存复用。
- `board_graph` 序列化形态不稳定（节点乱序、field 顺序浮动）→ 同语义不同 token，命中率下降。

---

## 3. LLM 输出 Schema（GraphUpdateOp）

`sync_node`（规划 Agent）经统一 LLM 接入接口（Design §6.2）请求**结构化输出**，LLM 必须返回如下 JSON，供 `parse_node` 校验、`update_node` 执行。该 `GraphUpdateOp` 的输入是 `analyze_node` 产出的会议纪要 `MeetingSummary`（见 `Design_InputProcessing.md` §3/§4）。

```json
{
  "operations": [
    {
      "op": "add_node",
      "node": { "type": "point", "label": "渠道成本比去年涨30%", "speaker_ref": "ent:user_zhang" },
      "parent": "n_issue_q3",
      "meta_ids": ["utt_0012"]
    },
    {
      "op": "link",
      "source": "n_point_cost", "target": "n_issue_q3", "relation": "subordinate"
    },
    {
      "op": "merge_as_duplicate",
      "node": "n_point_cost",
      "meta_ids": ["utt_0099"]
    },
    {
      "op": "replace",
      "old": "n_concl_old", "new": { "type": "conclusion", "label": "Q3 主攻存量复购" },
      "meta_ids": ["utt_0077"]
    },
    {
      "op": "set_importance", "node": "n_point_cost", "level": "high"
    }
  ],
  "thought": "简短推理说明（满足可解释性，Design §4.3）"
}
```

| `op` | 含义 | 落库动作（Store A） |
|---|---|---|
| `add_node` | 新建节点并挂到 `parent` | `addNode` + `addEdge`(subordinate) + 写 `metadata_refs` |
| `link` | 建关系边 | `addEdge`(relation) |
| `merge_as_duplicate` | 判定为重复提及 | 向 `metadata_refs` 追加去重 + `mention_count+1`，不新建 |
| `replace` | 新结论取代旧 | 建 `replace` 边保留演进痕迹（Design §0.1） |
| `set_importance` | 调重要性 | 写 `importance.level`（受 `manual_override` 约束） |

> `meta_ids` 直接来自 `filtered_meta_ids`，保证每个节点可溯源到 Store B 原始论据（见 Storage 设计 §5）。

---

## 4. 与既有文档的关联

| 本设计环节 | 关联文档 |
|---|---|
| 数据输入 = NormUtterance 流 | `ASR_UnifiedSchema.md` |
| 数据输入 = NormCursorEvent 流（与 ASR 同级） | `Research_CursorIntent_Capture.md` |
| 无意义过滤 / 光标过滤聚合 | Design §4.2 触发策略（仅 ASR `is_final` 触发；光标流仅服务软保护，见 `Design_CursorCapture.md` §9） |
| 关系图状态 = Store A | `Design_StructureGraph_Storage.md` §3 |
| 预载入为空→会议标题生成初始节点 | Design §3.1 FR-PRE-01（议程→顶层 issue） |
| 新过滤文本 + 关系图注入提示词 | Storage §6.2 `serializeForLLM` 大纲 |
| 窗口化 mouse_action 随输入信息进分析 Agent（光标流不再独立注入） | `Design_CursorCapture.md` §7（替代原 (d) 路全局焦点） |
| 结构化输出 / 统一 LLM 接口 | Design §6.2 |
| 更新时尊重 edit/lock + 追加 metadata_refs | Storage §5 / §7 |
| 鼠标交互事件监听（节点/画布） | `Research_X6_MindMap.md`（X6 交互事件） |
| 用户手势临时软保护（拖拽中不写回） | Storage §7 `data.edit.*`（运行时同源） |
| **双 Agent 拆分（分析 Agent → 会议纪要 MeetingSummary；规划 Agent → GraphUpdateOp；再流转流程图展示模块）** | `Design_InputProcessing.md` |
| 鼠标信号采集（前端→Adapter→WS→后端摄入） | `Design_CursorCapture.md` |
| 看板娘表情状态机 / 眼睛追踪 | `Design_Mascot.md` |
| 前端看板显示 / 用户操作 / Agent 工具接口 / 服务 | `Design_FrontendBoard.md` |
| 事件触发、不轮询 | Design §4.2 |

---

## 5. 待确认

- Q1：**已闭合**——`filter_node` 采用**纯规则**（关键词黑名单 + 停顿时长阈值），**不调用 LLM**（见 Design Q7）。理由：过滤是高频、低语义动作，纯规则延迟最低、零 LLM 成本；误杀率靠规则阈值校准（类比 `Design_CursorCapture.md` Q3 灰度方法），不进主链路引入 LLM。仅语义边界 case（闲聊 vs 实质发言难判）才考虑降级，但默认纯规则已覆盖绝大多数无意义语音（口头禅 / 短停顿 / 非会议内容）。
- Q2：**已闭合**——`board_graph` 跨批次采用 **LangGraph checkpointer 缓存**（per-meeting `thread_id` 级状态），**不每次从 Store A 重读**。策略：① 单场会议内，`update_node` 落库后 `board_graph` 写入 checkpointer，下一批次直接取，零 Store A 往返；② `route_init` 增加「checkpointer 中已有 `board_graph` 则跳过 `load_board_node`」短路分支，仅冷启动/新会议（`thread_id` 未见）才从 Store A `load_board_node`/`gen_initial_node` 载入；③ 并发多场：每场独立 `thread_id` → checkpointer 天然隔离，互不串图；④ Store A 仍为 durability 层（跨会话/崩溃恢复），checkpointer 为热缓存。（`Design_InputProcessing.md` §6.3 同款路由须镜像该短路分支）
- Q3（已闭合）：`gen_initial_node` 生成的初始节点**写** `metadata_refs`，指向议程 `agd_*`（与后续节点一致，保证议程项可溯源、可反查；呼应 `Storage` §5）。
- Q4（已闭合）：`MAX_RETRY=2`；校验失败降级时**不部分更新**，整体回退保留上一版 last-good 图（呼应 §3.4 校验门），并向 LLM 回传 skipped 清单；连续 2 次失败则停止本次自动更新、看板仍可读（降级不阻断会议，呼应 `Design.md` §9 风险应对）。
- Q5（光标流，已在 `Design_CursorCapture.md` 收口）：seq 跨流共享单一单调序号（Q1 已闭合）、`is_final` 不独立触发图推理（Q4 已闭合，仅随语音段窗口拼入 `mouse_action`）；`filter_cursor_node` 去抖/聚合阈值（Q3）仍待 M1 灰度实测回填。本设计据此：光标流仅服务 `update_node` 软保护，不独立触发推理。
- Q6：**已闭合**——鼠标手势软保护**以用户手动加锁/解锁为准**（见 Storage §7 `data.lock`）。仲裁规则：① 用户显式 `data.lock.locked=true` 的节点 → **lock 为权威源**，无论当前手势如何，AI 均不写回（位置/类型/文本），手势软保护让位于 lock；② 用户未加锁、正拖拽/按住某节点的运行时 → 手势软保护临时生效（`update_node`/`Syncer` 标记 `skip_fields`），但用户一旦手动加锁立即升级为 lock 保护、手动解锁则失效；③ 手势软保护是 **lock 之下的运行时补充层**，永不覆盖、永不替代显式 lock——冲突时 lock 优先。统一以用户手动操作为最高优先级（`Design_InputProcessing.md` §4.3 软保护逻辑须镜像此仲裁）。
- Q7：`actor.user_ref` 前缀规则（本机未登录 `local:<machine_guid>` / 极罕见 `anon:`，多端 `device` 命名与唯一性见 `Design_CursorCapture.md` §3.1，已闭合 Q2），本设计直接引用。
