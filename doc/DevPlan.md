# 开发流程计划（Dev Plan）

> **文档状态**：v0.1 · 开发执行中（基于 dev 分支）
> **最后更新**：2026-09-20
> **分支策略**：所有开发在 `dev` 分支；`master` 仅接收经评审的合并。
> **上游设计**（必须对齐）：
> - `doc/Design.md`（v0.4 总体需求）
> - `doc/Design_Agent_DataFlow.md`（v0.4 双 Agent 编排总图）
> - `doc/Design_StructureGraph_Storage.md`（双存储 + X6 友好 + metadata_refs）
> - `doc/Design_CursorCapture.md`（光标采集 200ms 节流 + partial→final）
> - `doc/Design_InputProcessing.md`（分析 / 图同步 双 Agent + MeetingSummary）
> - `doc/Design_FrontendBoard.md`（前端看板 + Agent 工具接口层）
> - `doc/Design_Mascot.md`（看板娘状态机 + 眼睛追踪）
> - `doc/ASR_UnifiedSchema.md`（NormUtterance 同形格式）
> - `doc/Research_CursorIntent_Capture.md` / `Research_KanbanMusume.md` / `Research_X6_MindMap.md`
> - `design-system/Linear.md` + `Miro.md`（前端视觉 token 参考，暗色以 Linear 为主）

---

## 1. 目标与里程碑

把设计文档转化为可运行代码。优先打通**端到端主干链路**（ASR → 过滤 → 双 Agent → Store A → 前端看板实时更新 → 看板娘状态切换），再逐层补强。

| 里程碑 | 范围 | 入口验收 |
|---|---|---|
| **M0** 基础设施 | 目录结构、`.venv`、`.gitignore`、依赖清单、数据模型骨架 | `python -c "import backend.app..."` 不报错 |
| **M1** 数据模型 + 双存储 | `NormUtterance`/`NormCursorEvent`/`MeetingSummary`/`GraphUpdateOp`/StructureGraph cells；Store A(json 真相源)+Store B(metadata)；`metadata_refs` 反查 | 单测：写入→`fromJSON` 同构→`metadata_refs` 取回原始论据 |
| **M2** Agent 编排（双 Agent） | LangGraph 风格 `StateGraph`：`input`/`filter`/`input_cursor`/`filter_cursor`/`load_board`/`gen_initial`/`assemble_analyze`/`analyze`/`assemble_sync`/`sync`/`update`；Mock LLM 接口（OpenAI 兼容抽象，`function calling` 桩） | 跑 mock 批次：输出 Store A cells 结构正确、含 `metadata_refs` |
| **M3** 后端服务 + WS 总线 | FastAPI + WS（`cursor.event` / `asr.event` / `mascot_state` / `board.update`）；REST（`/api/board` `/api/metadata` `/api/cursor/config`）；Agent 工具接口层（8 个 tool，执行时尊重 `lock`/`edit`，冲突回 `skipped`） | 用 `wscat`/脚本注入 mock 事件，看板增量推送正确 |
| **M4** 前端看板（X6） | `fromJSON` 渲染、增量 `toJSON({diff:true})` 应用、用户操作流（节点拖拽/双击编辑/折叠→用户操作事件）、`interacting` 编辑/查看模式切换 | 浏览器加载看板，手动拖节点不触发 AI 回写（lock 软保护） |
| **M5** 看板娘 | SVG 眼睛追踪（`atan2`+半径，`mousemove` 纯表现层）、`MascotState` 状态机映射 LangGraph 节点、WS `mascot_state` 驱动表情+气泡 | 注入 mock 节点状态，看板娘表情随 `analyzing`/`syncing` 切换 |
| **M6** 光标采集前端 | `CursorEventAdapter`：监听 X6 事件、`mousemove` **200ms 节流**、拖拽 `partial→final` 收口、`is_final` 进 `cursor.event` 总线 | 拖节点→只发 1 条 final 事件；hover 停留 >250ms 才发焦点 |
| **M7** 集成验证 | mock ASR + 光标源驱动全链路；输出 `run_demo.py` 与 README；关键路径可跑通 | 端到端 demo 无异常，看板随转写收敛 |

---

## 2. 目录结构（目标态）

```
AI_Meeting_Organizer/
├── .venv/                      # 运行时（已建，忽略）
├── requirements.txt            # 生产依赖（M3 起）
├── doc/                        # 设计文档（既有）
├── design-system/              # UI token 参考（既有）
├── backend/
│   ├── run_demo.py             # M0-M2 端到端 mock 验证（纯 stdlib 可跑）
│   ├── run_server.py           # M3 起 FastAPI 服务入口
│   └── app/
│       ├── __init__.py
│       ├── config.py           # THROTTLE_MS / HOVER_SETTLE_MS 等（GET /api/cursor/config 下发）
│       ├── models/             # 数据模型（dataclasses，M3 前不依赖 pydantic）
│       │   ├── asr.py          # NormUtterance
│       │   ├── cursor.py       # NormCursorEvent
│       │   ├── meeting.py      # MeetingSummary / InsightRecord
│       │   ├── graph_op.py     # GraphUpdateOp（5 种原子操作）
│       │   └── structure.py    # StructureGraph cells / NodeData / EdgeData
│       ├── storage/
│       │   ├── store_a.py      # 关系图真相源（json 文件；生产 jsonb）
│       │   └── store_b.py      # 元数据（按 meta_id 的 KV）
│       ├── adapters/
│       │   ├── asr_adapter.py      # 原始 ASR → NormUtterance
│       │   └── cursor_adapter.py   # 原始 X6 事件 → NormCursorEvent
│       ├── agent/
│       │   ├── state.py        # AgentState（TypedDict 等价 dataclass）
│       │   ├── graph.py        # StateGraph 定义（轻量编排器 / 或 langgraph）
│       │   ├── nodes.py        # 各节点函数（filter/load_board/.../update）
│       │   ├── prompts.py      # 提示词模板（系统提示+输出 schema+图摘要+光标焦点）
│       │   └── llm.py          # 统一 LLM 接入接口（OpenAI 兼容抽象 + Mock 实现）
│       ├── tools/              # Agent 工具接口层（function calling 执行端）
│       │   ├── graph_tools.py  # update_graph / create_node / lock_node / set_importance / search_nodes
│       │   └── metadata_tools.py # fetch_metadata / get_board / get_node
│       └── ws/bus.py           # WS 事件总线（cursor.event/asr.event/mascot_state/board.update）
└── frontend/
    ├── package.json            # @antv/x6 + vite + ts
    ├── index.html
    └── src/
        ├── main.ts
        ├── board/{render.ts,events.ts,cursor.ts}   # X6 渲染 / 用户操作流 / CursorEventAdapter
        ├── mascot/{MascotController.ts,eyes.ts}     # 看板娘
        └── api/{ws.ts,rest.ts}                      # 前端总线客户端
```

---

## 3. 接口契约（跨模块单一事实源）

### 3.1 统一输入流（与 `ASR_UnifiedSchema` / `Research_CursorIntent_Capture` 同形）

- `NormUtterance`：`utterance_id / meeting_id / session_id / seq / speaker{speaker_ref,source_id,display_name,is_resolved} / text / language / start_offset_ms / end_offset_ms / received_at_ms / is_final / is_partial / translation / source / raw_ref`
- `NormCursorEvent`：字段逐一对齐，差异仅在 `gesture_type + target`（替代 `text`）与 `actor`（替代 `speaker`）。`event_id / meeting_id / session_id / seq / actor{user_ref,device,is_resolved} / gesture_type / target{node_id,node_type,edge_id} / pointer{x,y} / intent_hint / start_offset_ms / end_offset_ms / received_at_ms / is_final / is_partial / source / raw_ref`

### 3.2 两 Agent 交接物 `MeetingSummary`

`analyze_node` 产出、`sync_node` 消费（见 `Design_InputProcessing.md` §3）：

```
MeetingSummary {
  meeting_id, meeting_title,
  insights: [ InsightRecord {
    type: point|evidence|issue|conclusion|action|conflict,
    summary, speaker_ref, evidence:[meta_id],
    confidence: 0..1, importance_hint: optional,
    related_to: optional[node_id], relation_to_related: support|oppose|derive|child
  } ],
  thought: str
}
```

### 3.3 图更新算子 `GraphUpdateOp`（sync_node 产出，update_node 执行）

`operations: [ add_node | link | merge_as_duplicate | replace | set_importance ]`，每项含 `meta_ids`（来自 `filtered_meta_ids`）→ 写入节点 `data.metadata_refs`。`thought` 可解释。

### 3.4 关系图 cells（Store A，X6 同构）

`{ schema:"amo.board/v1", graph_id, version, cells:[ {id, shape, position, size, ...attrs, data:{type,label,speaker_ref,importance,mention_count,edit,lock,resolved,metadata_refs,version}} ] }`。cell id = 业务 id（`n_issue_q3`）。

### 3.5 WS 协议（M3）

| event | 方向 | 内容 |
|---|---|---|
| `asr.event` | 前端/源→后端 | 信封包 `[NormUtterance]` |
| `cursor.event` | 前端→后端 | 信封包 `[NormCursorEvent]`，`THROTTLE_MS` 由前端按 config |
| `mascot_state` | 后端→前端 | `{type, state, label}`（MascotState 枚举） |
| `board.update` | 后端→前端 | `toJSON({diff:true})` 增量 cells（或全量 `get_board`） |

### 3.6 REST（M3）

`GET /api/board` · `GET /api/metadata?ids=` · `POST /api/cursor/config`(下发) · `GET /api/cursor/config`。

### 3.7 Agent 工具接口层（M3，见 `Design_FrontendBoard` §4）

`update_graph / fetch_metadata / get_board / get_node / search_nodes / lock_node / set_importance / create_node`——LLM function calling → 后端执行 → 尊重 `lock`/`edit` → 冲突回 `skipped`。

---

## 4. 当前沙箱约束与应对策略

- **网络受限**：`git push` / `pip install` / `npm install` 在沙箱内可能无法访问外网（已观测 `git ls-remote` exit 128）。
  - **后端 M0–M2**：用**纯标准库**（`dataclasses` + `json` + `asyncio`）实现，无需 pip，可直接 `run_demo.py` 跑通验证。
  - **生产依赖**（FastAPI/LangGraph/pydantic/X6）：列于 `requirements.txt` 与 `frontend/package.json`；环境就绪（可联网）后安装，接口不变。
  - **LangGraph 编排**：M0–M2 用自制轻量 `StateGraph` 编排器（语义等价：节点函数 + 条件路由 + checkpointer），M3 后可一键替换为 `langgraph`（节点/状态签名一致）。
- **提交策略**：按项目约定，本地提交可照常；**推送需老鸽明确指令**（前次未推送成功即因网络）。

---

## 5. 开发顺序（本次执行）

1. ✅ dev 分支 + .venv + .gitignore + requirements.txt
2. 🔄 本计划文档
3. ⏳ M0–M2 后端：数据模型 / 双存储 / 双 Agent 轻量编排 / Mock LLM / `run_demo.py` 端到端验证
4. ⏳ M3 后端服务 + WS + Agent 工具层（联网安装后）
5. ⏳ M4–M6 前端（X6 看板 / 看板娘 / 光标采集，联网安装后）
6. ⏳ M7 集成验证 + README

---

## 6. 待确认（落地时定）

- Q-A：`seq` 跨 ASR/光标共享单一单调序号（默认共享，统一入口分配）。
- Q-B：`metadata_refs` 单节点上限（默认不限，超 50 截断并折叠）。
- Q-C：Mock LLM 的「结构化输出」校验在 M2 用规则解析；M3 接真实模型后由 `function calling` 保证。
- Q-D：看板娘 `reasoning` 是否细分流式进度（默认 `analyzing`/`syncing` 两态即可）。
- Q-E：前端视觉以 Linear 暗色为主，还是 Miro 明亮白板风（默认 Linear 暗色，贴合「AI 会议秘书」专业氛围）。
