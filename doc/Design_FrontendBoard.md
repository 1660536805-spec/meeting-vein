# 前端看板操作和显示模块 · 详细设计

> **文档状态**：设计 v0.3 · 待评审
> **最后更新**：2026-09-19
> **关联文档**：
> - `doc/Research_X6_MindMap.md`（AntV X6 编辑/显示能力、事件清单）
> - `doc/Design_StructureGraph_Storage.md`（Store A 关系图 / X6 友好格式 / `data.edit.*`/`lock` / `metadata_refs`）
> - `doc/Design_InputProcessing.md`（双 Agent；Syncer 产出 `GraphUpdateOp`）
> - `doc/Design_CursorCapture.md`（用户鼠标手势 → `NormCursorEvent` 意图流）
> - `doc/Design_Mascot.md`（看板娘容器可嵌入画布侧边）
> - `design-system/Miro.md`（画布视觉规范：无限画布 / 明黄强调 / 彩色便签色系，取自 awesome-design-md · Miro）
> - `design-system/Linear.md`（UI 控件规范：极简 / lavender 强调 / 暗色面板，取自 awesome-design-md · Linear）
> **模块定位**：前端看板 = **显示 + 操作 + Agent 工具接口 + 后端服务** 四合一。
> 1. **显示**：X6 `fromJSON` 渲染 Store A，支持增量更新；节点用 React/Vue 业务组件（要点卡片）。
> 2. **操作**：用户拖拽/编辑/锁定/折叠 → 经 X6 事件 → 发后端（用户操作流）。
> 3. **Agent 工具接口层**：把 Syncer（图同步 Agent）可调用的后端能力封装为**标准 Tool**，供 LLM 经统一接入接口（§6.2）调用。
> 4. **服务/接口**：REST + WS 清单，含看板载入、增量推送、论据反查、用户操作、光标流、看板娘流。

---

## 1. 看板显示（X6 渲染）

### 1.1 载入与渲染

- `GET /api/board/:graph_id` → 返回 Store A `cells`（`Design_StructureGraph_Storage.md` §3.2 同构）→ `graph.fromJSON(cells)` 零翻译渲染。
- 节点用 React/Vue 组件渲染（`Research_X6_MindMap.md` §5）：`point-card` / `conclusion-card` 等，带标签、发言人头像、重要性色、状态角标。
- 渲染前 `decorate(cell)`：由 `data` 派生 `attrs`（颜色/边框/图标），人工 `style_override` 优先（§3 文档原则）。

### 1.2 增量更新（降上板延迟）

- 后端 `update_node` 落库后，`graph.toJSON({ diff: true })` 推送变更 cells（新增/修改/删除的 cell 差量）。
- 前端 `graph.fromJSON(diff)` 局部更新；节点位置冲突时以**用户本地未提交编辑**优先（见 §5 仲裁）。

### 1.3 点击节点查论据（元数据反查）

```
用户点击节点 n_point_cost_01
  → 读 node.data.metadata_refs = ["utt_0012","utt_0045","utt_0098"]
  → GET /api/metadata?ids=utt_0012,utt_0045,utt_0098
  → 侧栏渲染原始句列表（说话人 + 时间 + 文本 + 来源）
```

### 1.4 图关系遍历交互（查看态，借鉴 archify reach/route/lens）

> 把 `Design_InputProcessing.md` §3.3 的 `edges[]` 关系网从「展示附属」升级为「可探索导航」。会议纪要从单点查询（§1.3）扩展到**沿关系链追溯**。

- **reach（上下游追溯）**：选中节点 → 高亮其全部上游支撑（`support`/`derive`/`subordinate` 反向）+ 下游引用（`support`/`oppose` 正向）；`strength` 决定高亮透明度，`transitive` 边递归展开多跳。
- **route（路径探测）**：在两点间求最短关系路径（如「论点 A 经哪些中间节点反驳了结论 C」），逐段以 `edge.relation` 标注语义；对抗边（`_countered_by`）用红色虚线。
- **lens（语义透镜）**：按 `relation` 类型切换可见边集——仅看 `support`、仅看 `oppose`、仅看 `derive`，过滤无关连线，降低大图认知负荷。
- **实现**：前端 `graph.fromJSON` 后本地构建邻接表，交互纯前端图遍历、不触发后端；深链 `#reach=<node_id>` / `#route=<a>~<b>` / `#lens=support` 供分享与复盘定位。

### 1.5 箭头绑定（边随节点联动，借鉴 Excalidraw arrow-binding）

> 我们 `edges[]` 已有 `source`/`target` 节点引用，但**未定义"节点移动 / 删除 / 合并时边如何联动"**——这是视图正确性的硬缺口。借鉴 Excalidraw 的箭头绑定（边锚定到元素，元素移动边跟随、删除元素边自动清理/标记）。

- **移动联动**：节点拖拽 → 以其为端点的 `edges` 控制点自动重算（X6 用端口 `port` 或 `sourceAnchor`/`targetAnchor` 绑定），无需重绘边；`reach`/`route` 高亮随新几何实时刷新。
- **删除联动**：节点被删（用户或 AI `remove`）→ 所有以其为端点的边**随之删除**，并记 `removed` 入 `GraphChangeSet`（§4.4），避免悬空边（对齐 `Design_InputProcessing.md` §3.4 校验「无悬空边」）。
- **合并联动**：两节点 `merge_as_duplicate` → 边重指向保留节点（`merge` 语义），旧节点出边/入边迁移，不丢关系（对应 `edges[].relation` 的 `support/derive` 等保持）。
- **绑定存储**：绑定信息随 `edge` 存 Store A（`data.bind=true` / 端点 `port_id`），落库即持久，刷新后 X6 `fromJSON` 重建绑定。

---

## 2. 用户操作（X6 事件 → 用户操作流）

用户在前端的所有编辑，经 X6 事件采集 → 归一化为**用户操作事件** → 发后端（与 Agent 操作同源 Store A，靠 `lock` 仲裁）。

| 用户操作 | X6 事件 | 产出的用户操作事件 | 后端动作 |
|---|---|---|---|
| 拖拽移动节点 | `node:mouseup`（移动后） | `op:move`（`position`） | 写 `position`；标 `edit.position_frozen=true` |
| 双击改名/改结论 | `node-editor` 失焦 | `op:edit_label`（`label`） | 写 `label`；标 `edit.text_edited=true` |
| 改节点类型 | 卡片下拉 | `op:edit_type`（`type`） | 写 `type`；标 `edit.type_edited=true` |
| 手动连线（父子） | `edge:connected` | `op:link`（`relation`） | `addEdge`；`created_by=human` |
| 整体锁定 | 卡片锁按钮 | `op:lock`（`locked`） | 写 `lock.locked`；AI 此后只读 |
| 设重要性 | 卡片重要性控件 | `op:set_importance`（`level`） | 写 `importance.level`；标 `manual_override=true` |
| 折叠子树 | `node:collapse` | `op:collapse` | 本地显隐（不强制落库，可选同步） |
| 增删子节点 | `node:added`/`node:removed` | `op:add`/`op:remove` | 同 Agent 落库路径 |

> 用户操作事件结构（对齐 `NormCursorEvent` 风格，但 `created_by=human`）：`{ op_id, graph_id, cell_id, op, payload, actor, ts_ms }`。

---

## 3. Agent 工具接口层（核心：LLM 可调用的标准 Tool）

Syncer（图同步 Agent，`Design_InputProcessing.md` §4）在统一 LLM 接入接口（§6.2）下注册以下 Tool。LLM 通过 function calling 调用，后端校验后执行落库。**工具即 Agent 对看板的「双手」**。

### 3.1 工具清单

| Tool 名 | 描述 | 参数 | 返回 |
|---|---|---|---|
| `update_graph` | 应用一批图操作（`GraphUpdateOp.operations`） | `operations: [{op,node,parent,meta_ids,relation,level,...}]` | `{ applied: int, skipped: string[], errors: [] }` |
| `fetch_metadata` | 按 `meta_id` 拉 Store B 原始论据 | `ids: string[]` | `[{meta_id,speaker,text,start_offset_ms}]` |
| `get_board` | 获取当前关系图（cells 或大纲） | `format: "cells"\|"outline"` | `BoardGraph \| Outline` |
| `get_node` | 获取单个节点详情（含 `metadata_refs`/`edit`/`lock`） | `node_id: string` | `NodeData` |
| `search_nodes` | 按关键词/类型检索节点（向量或文本） | `query, type?` | `[{node_id,label,score}]` |
| `lock_node` | 锁定/解锁节点（AI 只读） | `node_id, locked: bool` | `{ ok }` |
| `set_importance` | 设节点重要性（标 `manual_override`） | `node_id, level` | `{ ok }` |
| `create_node` | 直接建节点（强意图场景兜底） | `type,label,speaker_ref,parent?,meta_ids?` | `{ node_id }` |

### 3.2 `update_graph` 参数（对齐 `GraphUpdateOp`）

```json
{
  "name": "update_graph",
  "description": "将分析得到的会议总结映射为关系图原子操作并应用。op ∈ add_node/link/merge_as_duplicate/replace/set_importance",
  "parameters": {
    "operations": [
      { "op": "add_node", "node": {"type":"point","label":"渠道成本比去年涨30%","speaker_ref":"ent:user_zhang"},
        "parent": "n_issue_q3", "meta_ids": ["utt_0012","utt_0045"] },
      { "op": "link", "source": "n_point_cost", "target": "n_issue_q3", "relation": "subordinate" },
      { "op": "merge_as_duplicate", "node": "n_point_cost", "meta_ids": ["utt_0099"] },
      { "op": "replace", "old": "n_concl_old", "new": {"type":"conclusion","label":"Q3 主攻存量复购"}, "meta_ids": ["utt_0077"] },
      { "op": "set_importance", "node": "n_point_cost", "level": "high" }
    ]
  },
  "returns": { "applied": 5, "skipped": [], "errors": [] }
}
```

### 3.3 工具调用协议（LLM function calling → 后端执行）

```
LLM(统一接口, tools=[update_graph,fetch_metadata,...])
   │  输出 tool_call：{ name:"update_graph", arguments:{ operations:[...] } }
   ▼
后端 ToolExecutor
   ├─ 1) 鉴权/限流（会议级、用户级）
   ├─ 2) 校验 schema（op 合法、parent 存在、meta_ids 存在）
   ├─ 3) 尊重 lock/edit（见 §5）：标记 skip_fields / 拒绝
   ├─ 4) 应用 patch 到 Store A（写版本历史）
   ├─ 5) 推送 diff 给前端（WS /ws/board）
   └─ 6) 返回 { applied, skipped, errors } 给 LLM（LLM 可据 skipped 纠正）
```

> 后端执行默认**尊重人工锁定**（§5），skipped 项回传 LLM，使其知道「该节点用户已锁，未改」。

### 3.4 工具调用交付校验门（last-good，借鉴 archify）

> 防止「坏图」污染 Store A：Syncer 的 `update_graph` 结果**先过校验再替换**，失败保留上一版可读图（类比 archify 的 last-good 交付门）。

- **校验顺序**：① schema 合法（`op`/`parent`/`meta_ids` 存在）→ ② 不破坏 `lock`/`edit.*` → ③ 图无悬空边 / 无孤立必连节点 → ④ 规模阈值（单批新增 ≤ N、总节点 ≤ M，防爆炸）。
- **last-good 守卫**：校验失败则**丢弃本次 diff、保留 checkpointer 中上一版 `board_graph`**，返回 `{ applied:0, errors:[...] }` 给 LLM（据 errors 纠正重发），绝不写半图。
- **repair receipt**：每次成功落库附带可机读回执 `{ version, applied_ops, skipped, changed_nodes:[id], rollback_to:prev_version }`；前端据 `changed_nodes` 做变更高亮（§4.4），回执入版本历史。

---

## 4. 后端服务 / 接口清单

### 4.1 REST

| 方法 | 路径 | 说明 | 调用方 |
|---|---|---|---|
| `GET` | `/api/board/:graph_id` | 取 Store A cells（首次渲染） | 前端 |
| `GET` | `/api/board/:graph_id/outline` | 取大纲（Agent `get_board` 内部用） | Agent Tool |
| `GET` | `/api/metadata?ids=utt_0012,utt_0045` | 取 Store B 论据（反查/工具） | 前端 / Agent |
| `POST` | `/api/board/:graph_id/update` | 应用 `GraphUpdateOp`（Agent 落库主路径，亦可被 Tool 内部复用） | 后端/Syncer |
| `POST` | `/api/node/:node_id/lock` | 锁定/解锁（用户操作） | 前端 |
| `POST` | `/api/node/:node_id/importance` | 设重要性 | 前端 |
| `POST` | `/api/node/:node_id/op` | 用户操作事件（move/edit/link/...） | 前端 |
| `POST` | `/api/board/:graph_id/rollback` | 版本回滚（FR-LIVE-18） | 前端 |
| `GET` | `/api/mascot/config` | 看板娘运行参数 | 前端 |

### 4.2 WebSocket

| 通道 | 方向 | 内容 |
|---|---|---|
| `WS /ws/board/{meeting_id}` | 后端→前端 | 图 diff（`toJSON({diff:true})`）、lock 变更、回滚通知 |
| `WS /ws/cursor`（或复用 ASR WS） | 前端→后端 | `cursor.event` 信封（鼠标手势意图流） |
| `WS /ws/mascot/{meeting_id}` | 后端→前端 | `mascot_state` 表情状态 |
| `WS /ws/asr`（ASR 流） | 后端→前端（可选） | 实时转写回显（非必需） |

### 4.3 事件总线 / SDK

前端封装 `BoardSDK` 统一订阅 WS + 调 REST：

```typescript
class BoardSDK {
  load(graphId): Promise<BoardGraph>         // GET /api/board
  subscribeBoard(cb: (diff: CellDiff) => void)  // WS /ws/board
  emitUserOp(op: UserOp)                       // POST /api/node/:id/op
  emitCursor(ev: NormCursorEvent)              // WS /ws/cursor
  fetchMetadata(ids: string[]): Promise<Metadata[]>  // GET /api/metadata
  onMascot(cb: (s: MascotState) => void)       // WS /ws/mascot
}
```

### 4.4 变更集预览（借鉴 archify `delta`）

每次 AI 批次落库前，`ToolExecutor`（`Design_InputProcessing.md` §4）产出一个**结构化变更集 `GraphChangeSet`**，前端据此做「本次 AI 改了什么」的可视化，而非裸 diff 推送：

```typescript
interface GraphChangeSet {
  batch_id: string;            // 对应 AgentState.batch_id
  meeting_summary_ref: string; // 本批会议纪要 id（溯源到 MeetingSummary）
  added:  CellDiff[];          // 新增节点/边
  modified: CellDiff[];        // 修改（label/type/importance/挂接）
  removed: CellDiff[];         // 删除（含 merge 折叠）
  skipped: CellDiff[];         // 因 lock/edit 跳过的项（回传 LLM）
  generated_at: number;        // 时间戳
}
```

- **查看态展示**：工具栏「变更」按钮展开时间线，按 `batch_id` 倒序列出每批摘要（"新增 3 节点 / 修改 2 / 跳过 1"），点击单批高亮对应 `added`/`modified` 节点（过场闪烁，可关）。
- **语义高亮**：`skipped` 项显式标「用户已锁」徽标，让人工操作可见、可撤销。
- **回放/对比**：借鉴 archify 的 **Before → Delta → After** 三态视图，支持对任意历史 `batch_id` 与前一批做对比（依赖 `Design_Agent_DataFlow.md` Q2 checkpointer 缓存的 `board_graph` 历史快照）。
- **guard 守卫**：`update_node` 落库前先跑 `validateGraph`（闭环检测/类型合法/孤儿边），**校验失败则不推送、保留上一版 good 图并回执 `repair_receipt`**（定位哪条 `GraphUpdateOp` 非法）——对应 archify 的 last-good 交付门，杜绝坏图污染画布。

### 4.5 导出与分享（借鉴 archify 导出能力）

| 格式 | 用途 | 实现 |
|---|---|---|
| PNG | 静态截图、贴纪要 | X6 `toPNG()` / `toSVG()` |
| SVG | 矢量、可二次编辑 | X6 `toSVG()` |
| WebM | 变更回放动效（可选） | 逐 batch 快照合成（依赖 §4.4 历史） |
| 分享卡 | 只读链接 | 后端把 `board_graph` 序列化 + 深链 `#focus=<node_id>` |

- **深链**：节点支持稳定 id 深链（`#focus=<node_id>` / `#reach=<node_id>` 上下游 / `#route=<a>~<b>` 路由），刷新/分享后定位到同一视图（借鉴 archify reach/route）。
- 导出默认走「当前查看态」所见即所得；可勾选「含 AI 变更高亮」。
- **只读快照分享链**（借鉴 Excalidraw readonly share link）：把当前 `board_graph`（或某历史 `batch_id` 版本，依赖 §4.4 历史 + `DataFlow Q2` checkpointer 快照）序列化为**不可变只读链接**——`POST /api/board/:id/snapshot` 返回带 token 的 `/view?token=...`，打开后仅渲染、无编辑/无 Agent 写回。用于对外发会议纪要图、复盘分享，与上面可写深链区分（深链带 `#` 仍走可交互视图，快照链为只读归档）。

---

## 5. 用户操作 vs Agent 操作的区分与冲突仲裁（lock 优先）

两者都改 Store A，必须有一致仲裁规则（对齐 `Design_StructureGraph_Storage.md` §7）：

| 场景 | 仲裁 | 说明 |
|---|---|---|
| 用户对节点 `lock.locked=true` | AI **完全跳过**该节点所有写回 | FR-LIVE-17 |
| 用户 `edit.text_edited=true` | AI 不改 `label` | 人工文本优先 |
| 用户 `edit.type_edited=true` | AI 不改 `type` | 人工类型优先 |
| 用户 `edit.position_frozen=true` | AI 不改 `position`（不重排） | 人工布局优先 |
| 用户 `importance.manual_override=true` | AI 不改 `level` | 人工重要性优先 |
| 用户拖拽中（`NormCursorEvent` drag/hold） | AI 临时跳过该节点 label/type/position（软保护） | 运行时，非持久 |
| 用户与 AI 同建一条边 | 标记 `conflict=true`，双方保留 | 双写不覆盖 |
| Agent `update_graph` 返回 skipped | 回传 LLM，不强行覆盖 | 可解释 |

> **优先级总原则**：`lock`（持久）> 用户 `edit.*`（持久）> 光标软保护（临时）> AI 自动写回。所有自动写回经 `onAutoUpdate` 校验（§7 文档伪代码）入版本历史。

---

## 6. 编辑/查看模式（X6 `interacting`）

- **查看模式**（会议进行中）：`interacting:false` + 禁用 `Transform`/`Clipboard` 等编辑插件，防误触；仅保留点击查论据、看板娘。
- **编辑模式**（复盘/协作）：全量编辑能力开启（`node-editor`/`History`/`Selection`/`Keyboard`）。
- 鼠标手势采集在查看模式下降采样（见 `Design_CursorCapture.md` §2.4）。

### 6.1 主题（暗/亮，取自 `design-system/` token）

视觉 token 取自 `design-system/Miro.md`（画布本体：明亮无限画布 + 明黄强调 `#FFD02F` + 彩色便签色系）+ `design-system/Linear.md`（UI 控件：极简 + lavender 强调 `#5E6AD2` + 暗色面板 `#08090A`），二者组合成「亮色画布 + 暗色控件」双区主题（借鉴 archify 暗/亮切换能力）：

| 区域 | 主题 | 主色 | 来源 |
|---|---|---|---|
| 画布背景 / 节点卡片 | 亮 | 白底 + 明黄强调 | Miro |
| 顶部工具栏 / 侧栏 / 变更面板 | 暗 | `#08090A` 底 + lavender 强调 | Linear |

- 节点 `type` → 颜色映射沿用 Miro 便签色系（论点/论据/议题/结论/行动项/分歧 各一色），定义在 `design-system/Miro.md` 的 `colors.brand` 区。
- 主题切换为前端运行时状态（CSS 变量），不进 `board_graph` 数据；导出 PNG 按当前主题所见即所得（见 §4.5）。

### 6.2 演示态 / 激光指针（查看态，借鉴 Excalidraw laser pointer）

> 我们「查看模式」目前只有点击查论据，偏**静**；会议进行中常需 presenter 引导注意力。借鉴 Excalidraw 协作激光指针，在查看态叠加**临时引导层**。

- **激光指针**：查看态下 presenter 长按（或工具栏「演示」）进入引导态，鼠标移动在画布投射**渐隐光点**（不写任何节点/边，纯 overlay，松手即消），其他同览者（若多人）同步看到。
- **演示高亮**：可选「聚焦某节点 → 其余节点降透明度」的聚光灯效果，配合 `reach`/`route`（§1.4）做"边讲边展开关系链"的引导式复盘。
- **不污染数据**：激光指针/演示高亮均属**查看态 overlay**，不进 `board_graph`、不入版本历史、不触发 Agent；与 `Design_CursorCapture.md` 的 `NormCursorEvent`（拖拽/点击）语义隔离——纯展示辅助。

---

## 7. 配置

| 参数 | 默认 | 说明 |
|---|---|---|
| 增量推送开关 | 开 | `toJSON({diff:true})` 降延迟 |
| 用户操作防抖 | 300ms | 连续 move/edit 合并上报 |
| lock 仲裁严格度 | 严格 | 锁后 AI 零写回 |
| 冲突边保留 | 是 | `conflict=true` 双方保留 |

---

## 8. 与既有文档一致性

| 本模块 | 关联文档 |
|---|---|
| X6 渲染 / 编辑 / 事件 | `Research_X6_MindMap.md` |
| Store A cells / `decorate` / `metadata_refs` / lock/edit | `Design_StructureGraph_Storage.md` |
| Syncer 产出 `GraphUpdateOp` → `update_graph` Tool | `Design_InputProcessing.md` §4 |
| 光标意图流 `NormCursorEvent` | `Design_CursorCapture.md` |
| 看板娘 WS / 容器 | `Design_Mascot.md` |
| 统一 LLM 接入 / Tool 注册 | `Design.md` §6.2 |
| 前端视觉 token（画布/控件主题） | `design-system/Miro.md` · `design-system/Linear.md`（取自 awesome-design-md） |
| 查看态图遍历 / 变更集预览 / 导出 | 借鉴 `archify`（JSON IR 渲染范式；本项目仍用 X6，非替换） |
| 箭头绑定 / 只读快照分享链 / 演示态激光指针 | 借鉴 `Excalidraw`（手绘白板范式；本项目仍用 X6，不引入 Rough.js 手绘风） |

---

## 9. 待确认 / 开放项

- Q1（已闭合）：`update_graph` Tool 为 Syncer **唯一**落库路径——统一走 Tool（可观测、可校验、可 skipped 回传、过 §3.4 校验门），Syncer 不直接调 `POST /api/board/update`。
- Q2（已闭合）：**Store A 乐观锁 `version` + 后到者尊重先到者 `lock`/`edit`**——同节点并发以 `version` 兜底、以用户手动 `lock`/未提交 `edit` 为权威源（呼应 DataFlow §5 Q6 软保护仲裁），Agent 写回冲突时整体回退并重试。
- Q3（已闭合）：**用户本地未提交编辑优先**——前端增量更新与本地编辑冲突时，本地编辑为准、AI 变更以 `diff` 标记（高亮待确认）不静默覆盖，用户确认后合入（呼应 DataFlow §5 Q6「用户手动操作最高优先级」）。
- Q4（已闭合）：**默认文本检索**（节点少、结构化、`type`+`summary` 分词即可）；大图（>500 节点）启用向量检索增强（呼应 `Storage` §9 向量库），`search_nodes` 接口对前端透明。
- Q5（已闭合）：看板娘容器**悬浮层**（右下角，低耦合、不挤占 X6 画布与侧栏），与 `Design_Mascot.md` §9 Q6 一致。
- Q6（已闭合）：**全部落版本历史**——用户操作事件 `changed_by=human` 全量入版本历史（满足可观测性 `Design.md` §5「编辑保留率 100%」、冲突仲裁可溯源），与 Agent 操作（`changed_by=agent`）区分记录。
