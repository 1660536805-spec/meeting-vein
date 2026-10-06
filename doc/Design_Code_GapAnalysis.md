# 设计文档 vs 实际代码 差异分析报告

> **文档状态**：分析报告 v1.0
> **生成日期**：2026-09-25
> **分析范围**：`doc/` 全部 8 份设计文档 + `design-system/` 2 份规范 ↔ `backend/app/`（约 2000 行）+ `frontend/src/`（约 950 行）
> **方法**：逐条比对文档条款与代码实现（以真实行号为据）；未运行时验证。
> **差异类型**：`文档有但代码未实现` / `代码有但文档未描述` / `实现与文档不一致` / `文档已过时/与现状矛盾` / `一致`

---

## 0. 总体结论

| 维度 | 状态 |
|---|---|
| 双 Agent 顶层骨架（Analyzer→Summary / Syncer→GraphUpdateOp、13 节点图、受控词表、三段式 prompt 拼接） | ✅ 高度一致 |
| last-good 交付门 / 版本历史回滚 / lock-edit 字段级权限 / GraphChangeSet | ✅ 基本一致 |
| **真实 LLM 链路未按设计接通**（assemble 产物成死数据、无 function calling、正则抠 JSON） | 🔴 最大缺口 |
| **光标流整条失效**（input_cursor 不可达 + 软保护未实现 + 却又逐条触发全量推理） | 🔴 第二缺口 |
| **工具层孤岛**（8 个 Agent Tool 未接 LLM、未接 REST） | 🔴 第三缺口 |
| 前端交互/主题/看板娘表情：文档描述明显超前于 MVP 实现；文档间亦有自相矛盾处 | 🟡 需回写文档 |

统计（不含「一致」项）：**高 7 · 中 22 · 低 25**，另有大量一致项见各域小结。

---

## 1. 域一：Agent 编排与数据流
> 比对：`Design_Agent_DataFlow.md`、`Design_InputProcessing.md`、`DevPlan.md` ↔ `orchestrator.py`、`llm.py`、`prompts.py`、`tools/`、`config.py`、`ws/bus.py`

| # | 文档描述 | 代码实际 | 类型 | 严重度 |
|---|---|---|---|---|
| 1-1 | 双 Agent：分析 Agent→`MeetingSummary`，规划 Agent→`GraphUpdateOp`（DataFlow §1/§2.2） | `analyze_node` 产 `meeting_summary`（orchestrator.py:164-166）；`sync_node` 产 `llm_output`（orchestrator.py:177-179） | 一致 | — |
| 1-2 | 13 个节点：input/input_cursor/filter/filter_cursor/load_board/gen_initial/assemble_analyze/analyze/assemble_sync/sync/parse/update/present（DataFlow §2.2-§2.3） | 同样 13 个节点注册（orchestrator.py:250-262） | 一致 | — |
| 1-3 | DevPlan §2/§4：M0-M2「纯标准库、自制轻量 StateGraph」，AgentState 为「TypedDict 等价 dataclass」 | 实际用 **pydantic BaseModel**（orchestrator.py:22）+ **真实 langgraph** StateGraph+MemorySaver（orchestrator.py:18-19） | 文档已过时/与现状矛盾 | 中 |
| 1-4 | 光标流平行不阻塞，「光标流不再汇入 assemble」（DataFlow §2.3 L114-124）；Input §6.3 边表也无 `filter_cursor→assemble_analyze` | 存在 `filter_cursor→assemble_analyze` 边（orchestrator.py:271）；且 `input_cursor` **无入边**，`run()` 仅从 `input` 单点 ainvoke（orchestrator.py:236-246,264-265）→ **filter_cursor_node 永不执行** | 实现与文档不一致 | **高** |
| 1-5 | DataFlow Q2：checkpointer 已有 board_graph 则跳过 load_board | `_route_init` 注释称短路，但 `board_loaded/exists` 为真时**仍返回 "load_board"**（orchestrator.py:229-233），每批次都回 Store A | 实现与文档不一致 | 中 |
| 1-6 | DataFlow Q3：gen_initial 生成议程根节点要写 `metadata_refs` 指向 `agd_*` | `gen_initial_node` 建 `n_issue_root` 时 `metadata_refs=[]`（orchestrator.py:141） | 实现与文档不一致 | 中 |
| 1-7 | filter_node 纯规则：关键词黑名单 + **停顿时长阈值**（DataFlow Q1） | 仅填充词删除 + 长度≤1 丢弃（orchestrator.py:68-89），无停顿时长判断 | 实现与文档不一致 | 低 |
| 1-8 | GraphOp JSON：`add_node.node={type,label,speaker_ref}` + `parent` 自动挂 subordinate 边（DataFlow §3 L206-236；Input §4.3） | `node` 为**字符串 id**，类型/标签平铺为 `node_type/label`（models.py:179-192）；`_apply_one` 的 add_node **不读取 `g.parent`**、不自动建边（storage.py:169-177） | 实现与文档不一致 | 中 |
| 1-9 | `replace` 用 `old`/`new` 字段（DataFlow §3 L222-225；Input §4.3 L269） | `GraphOp` 用 `node`+可选 `label/node_type`，无 old/new（models.py:179-192） | 实现与文档不一致 | 中 |
| 1-10 | `set_importance` 字段名 `level`（DataFlow §3 L226-228） | 字段名 `importance`（models.py:190）；storage.py:216 注释仍写 `importance.level` | 实现与文档不一致 | 低 |
| 1-11 | `replace` 保留演进痕迹 = 建 `replace` 边（DataFlow §3 L239） | replace 只原地改 label/type/meta，**不建边**（storage.py:193-210） | 实现与文档不一致 | 中 |
| 1-12 | DevPlan §3.2：MeetingSummary 含 `insights[]/thought` | 实际为 v2：`nodes[]/edges[]/analysis_note/_schema_version=ms_v2`（models.py:161-173），与 Input §3.3 一致 | 文档已过时/与现状矛盾（DevPlan 未随 ms_v2 更新） | 中 |
| 1-13 | 结构化输出为硬需求，禁止自由文本解析（Design.md §6.2 L370） | `_extract_json` 用**正则从文本抠第一个 JSON**（llm.py:47-62,118,155），无 schema 约束/response_format | 实现与文档不一致 | 中-高 |
| 1-14 | 统一 LLM 接口需：结构化输出、流式、前缀缓存、推理分档、思考可视化（Design.md §6.2；Q7） | 单次同步 `chat.completions.create`（llm.py:88-96），无 stream/分档/缓存控制；单一模型配置（config.py:49） | 文档有但代码未实现 | 中 |
| 1-15 | M3「LLM function calling → 后端执行」（DevPlan L28,132,161） | `_chat` 不传 `tools/tool_choice`；全库无 function calling 调用点 | 文档有但代码未实现 | **高** |
| 1-16 | 模型失败降级 = **停止自动更新**、看板保持可读（Design.md §5 L320、§9 L434） | 真实客户端异常回退**规则引擎继续改图**（llm.py:137-142,170-174） | 实现与文档不一致（降级语义相反） | 中 |
| 1-17 | `analyze_node/sync_node` 输入 ← `llm_messages_*`（DataFlow §2.2 L84-87；Input §6.1） | 两节点直接调 `llm.analyze/sync`，**不消费 assemble 产物**（orchestrator.py:164-166,177-179）；真实客户端另自建系统提示（llm.py:102-117,144-154）→ `assemble_*` 为死数据 | 实现与文档不一致 | **高** |
| 1-18 | Analyzer 受控词表 7 类节点/8 类语义边；两段/三段拼接顺序（Input §3.4） | `prompts.py` 词表与 models 一致；assemble_analyze 两段、assemble_sync 三段且顺序正确（orchestrator.py:154-175） | 一致 | — |
| 1-19 | 8 个工具：update_graph/fetch_metadata/get_board/get_node/search_nodes/lock_node/set_importance/create_node（DevPlan §3.7；FrontendBoard §3.1） | graph_tools.py 6 个 + metadata_tools.py 2 个，名称全对（graph_tools.py:26-77；metadata_tools.py:22-26） | 一致（但见 1-20） | — |
| 1-20 | 工具协议：LLM function calling → ToolExecutor 执行 → skipped 回传（FrontendBoard §3.3） | `tools` 在 server.py:52 实例化后**未挂到任何端点**（各端点直接调 `store_a.commit_graph_update`）；也未注册进 LLM → 工具层孤岛 | 实现与文档不一致 | **高** |
| 1-21 | skill 注入：Syncer system 提示含 x6-graph-ops 图形规范（prompts.py:49 自称） | 仅一行**文本提及路径**，无任何代码加载 `SKILL.md`；三份设计文档亦无 skill 注入章节 | 代码有但文档未描述（双向缺口） | 中 |
| 1-22 | `board.update` 推 `toJSON({diff:true})` **增量**（DevPlan §3.5 L124；FrontendBoard §1.2） | `_broadcast_board` 每次推**全量** cells（server.py:62-69） | 实现与文档不一致 | 中 |
| 1-23 | WS 上行支持 `asr.event` 信封（DevPlan §3.5 L121） | `_handle_uplink` 仅处理 `cursor.event`，其余直接 return（server.py:342-355）；ASR 走 REST `/api/asr/push` | 文档有但代码未实现 | 中 |
| 1-24 | 光标不独立触发图推理，`CURSOR_INDEPENDENT_TRIGGER=false`（DataFlow §2.4 L152；Cursor §5.3/§9） | 每条 `cursor.event` 上行即 `_drive` → **完整双 Agent 管线**（server.py:349-354,332-339） | 实现与文档不一致 | **高** |
| 1-25 | MAX_RETRY=2、失败不部分更新、保留 last-good；`route_parse` 重试/降级 | 完全一致（config.py:26；orchestrator.py:181-193,221-227；storage.py:307-310） | 一致 | — |
| 1-26 | Input §9 Q6：重试**复用同一 meeting_summary，不重跑 Analyzer** | route_parse 失败回到 assemble_analyze → Analyzer 重跑（orchestrator.py:225-226,276-277）；另 Input §6.3 L354 自称「重跑双 Agent」，**文档自相矛盾** | 实现与文档不一致（且文档内部矛盾） | 低 |
| 1-27 | Input §9 Q2：大图分层序列化（BFS 子树+占位引用） | `serialize_for_llm` 全量序列化（prompts.py，server.py:98 复用） | 文档有但代码未实现 | 低-中 |
| 1-28 | 工具 skipped 需含原因（DevPlan L132） | storage 层 skipped 带 reason（storage.py:220-232）；GraphTools 只回 id 列表（graph_tools.py:18,54,58），两处结构不统一 | 实现与文档不一致 | 低 |
| 1-29 | 写操作尊重 lock/edit、边冲突双写保留 | `_apply_one` 完整实现（storage.py:181-183,197-208,214-215） | 一致 | — |
| 1-30 | 「代码有但文档未描述」：AgentState 新增 `filtered_utterances/repair_receipt/change_set/mascot_state` 等字段；`board.rollback` 事件；`/ws` 统一端点与多 WS 端点拆分；环境变量清单（OPENAI_*/AMO_*） | orchestrator.py:32-52；server.py:299-301,357-426；config.py:44-52 | 代码有但文档未描述 | 低 |

---

## 2. 域二：结构图存储 / ASR 统一 schema / REST 接口
> 比对：`Design_StructureGraph_Storage.md`、`ASR_UnifiedSchema.md` ↔ `storage.py`、`models.py`、`adapters.py`、`server.py`

| # | 文档描述 | 代码实际 | 类型 | 严重度 |
|---|---|---|---|---|
| 2-1 | schema 名 `"ai-moderator.board/v1"`（Storage §3.2 L66） | `"amo.board/v1"`（storage.py:83；models.py:252） | 实现与文档不一致 | 低 |
| 2-2 | shape：`mind-node`/`mind-edge`（Storage §3.3/§3.4） | `amo-node`/`edge`（models.py:233,243）；前端按 `edge` 判别（render.ts:31） | 实现与文档不一致 | 中 |
| 2-3 | 边 source/target 为裸 cell id 字符串（Storage §3.4 L125-126） | `{"cell": id}` 对象（models.py:244-245）；validate/nav 均 `.cell` 取值（storage.py:257；nav.ts:31） | 实现与文档不一致 | 低 |
| 2-4 | 边 data 含 `created_by/confidence/conflict/version`（Storage §3.4 L128-135） | 初始仅 `{relation}`（models.py:246）；conflict 仅在冲突时动态补（storage.py:181-183） | 实现与文档不一致 | 低-中 |
| 2-5 | 节点 data 含 `style_override/created_at/updated_at`（Storage §3.3 L97-99） | NodeData 无此三字段（models.py:205-217） | 文档有但代码未实现 | 低 |
| 2-6 | cell id 规范 `e_<关系>_<from>_<to>`（Storage §3.2 L75） | `e_{source}__{target}`（storage.py:179；server.py:254），无关系段 | 实现与文档不一致 | 低 |
| 2-7 | `metadata_refs` 上限 20 条、超限裁剪（Storage §11 Qa 已闭合） | `_append_meta` 只去重不设限（storage.py:156-160） | 文档有但代码未实现 | 中 |
| 2-8 | StoreB 记录全字段：`kind: utterance/agenda/manual` + seq/speaker 对象/language/received_at_ms/is_final/translation/raw_ref/created_at（Storage §4 L146-162） | 仅 `{meta_id, kind:"utt", text, speaker_ref, start/end_offset_ms, source}`（server.py:433-436,467-470）；kind 取值 `utt` 而非 `utterance`；**agenda/manual 两类无写入路径** | 实现与文档不一致 | 中 |
| 2-9 | speaker_ref 规则：腾讯有 userid → `ent:`，否则 `ms:`（ASR §4 L160-165） | 一律 `ms:{userid or ms_open_id or anonymous}`（adapters.py:31-41），`ent:` 前缀永不产生 | 实现与文档不一致 | 中 |
| 2-10 | translation 为数组 `[{text,language}]`（ASR §2.1） | `Optional[str]`（models.py:78） | 实现与文档不一致 | 低 |
| 2-11 | NormUtterance 含 `mouse_action` 槽位与 `confidence` 预留（ASR §2.1 L75-77） | dataclass 无这两个字段（models.py:64-80）；mouse_action 由 filter_node 的 utts dict 承载（orchestrator.py:82-87），功能等价但契约外 | 实现与文档不一致 | 低 |
| 2-12 | FunASR `start/end` 单位为**毫秒**（ASR §3.1） | `int(info.get("start",0)*1000)` 按**秒**换算（adapters.py:62-63）——无法确认真实单位，二者必有其一错 | 实现与文档不一致（待实测） | 低 |
| 2-13 | `seq` 单调递增、统一入口分配（ASR §3.1；Cursor §12 Q1 共享序号） | `_seq()` 用时间低位近似（adapters.py:19-21），ASR/光标不共享序号 | 实现与文档不一致 | 低 |
| 2-14 | 版本历史独立 append-only 集合、snapshot 存改动前 data+position、人工操作也入历史（Storage §7/§8） | `{graph_id}.history.json` 按图一文件，记录结构与 §8 一致；rollback 也入历史（storage.py:95-154,311-324） | 一致 | — |
| 2-15 | 乐观锁 `version`（Storage §9） | `save_if_version` 已实现（storage.py:88-93）；但 Agent 落库 `commit_graph_update` **未走乐观锁**（storage.py:298-323 直接 save），仅用户操作端点用 `_save_cells_optimistic`（server.py:72-80） | 实现与文档不一致 | 低-中 |
| 2-16 | last-good 交付门：schema/悬空边/规模阈值、失败整体回滚 | `validate_graph` + `commit_graph_update` 完整实现（storage.py:241-267,290-331；config.py:34-35） | 一致 | — |
| 2-17 | REST 清单（FrontendBoard §4.1） | 文档所列 9 条全部实现；**额外多出**：`GET /api/board`（兼容）、`GET /api/board/{id}/history`、`POST /api/board/{id}/snapshot`+`GET /api/view/{token}`、`POST /api/asr/push`、`POST /api/cursor/push`、`POST /api/cli/push`、`POST /api/cursor/config`（server.py:83-471） | 代码有但文档未描述 | 低 |
| 2-18 | `serializeForLLM` 大纲注入（Storage §6.2） | `prompts.serialize_for_llm` 已实现并用于 outline 端点与 assemble_sync（server.py:98；orchestrator.py:172） | 一致 | — |

---

## 3. 域三：输入处理与光标捕获
> 比对：`Design_InputProcessing.md`、`Design_CursorCapture.md` ↔ `orchestrator.py`（filter/光标节点）、`server.py`、`cursor.ts`、`config.py`

| # | 文档描述 | 代码实际 | 类型 | 严重度 |
|---|---|---|---|---|
| 3-1 | 光标流 `input_cursor → filter_cursor → filtered_focus`（Cursor §1/§5） | `input_cursor` 不可达（见 1-4），`filter_cursor_node` 永不执行 → `filtered_focus` 恒空（orchestrator.py:91-114,270-271） | 文档有但代码未实现（逻辑断链） | **高** |
| 3-2 | `mouse_action` 按语音段 ±1s 窗口拼入 utterance（Cursor §7；ASR §2.1） | `_fuse_mouse_action` 已实现（orchestrator.py:116-132），但输入 focus 恒空 → `mouse_action` 恒 `[]`；且前端 drag 事件 `start/end_offset_ms` 硬编码 0（cursor.ts:56-57），即便可达 ts 语义也失真 | 实现与文档不一致 | **高** |
| 3-3 | 软保护：`filtered_focus` 命中 drag/hold 时 `replace` 跳过 label/type/position（Cursor §6；Input §4.3） | `update_node` 不读 `filtered_focus`（orchestrator.py:195-208）；storage 无 `skip_fields` 机制 | 文档有但代码未实现 | 中 |
| 3-4 | 采集事件清单含 `dblclick/collapse/canvas_hold`（Cursor §2.1） | CursorEventAdapter 仅绑 mousedown/mousemove/mouseup/mouseenter/mouseleave（cursor.ts:21-34），**无 dblclick/collapse/blank 事件** | 文档有但代码未实现 | 中 |
| 3-5 | hover 去抖：后端按停留 ≥250ms 过滤（Cursor §5.2） | 后端逻辑已写（orchestrator.py:105-107），但前端 hover 事件不带 start/end_offset（cursor.ts:28-31）→ dur=0 全部会被丢弃（若节点可达） | 实现与文档不一致 | 中 |
| 3-6 | 查看模式降采样 `VIEW_MODE_SAMPLE_RATE=0.3`（Cursor §2.4/§9） | config 有字段（config.py:21），前后端均无使用 | 文档有但代码未实现 | 低 |
| 3-7 | `POST /api/cursor/debug-sample` 按需开关（Cursor §10；Q6） | 未实现 | 文档有但代码未实现 | 低 |
| 3-8 | `THROTTLE_MS=200 / HOVER_SETTLE_MS=250 / DRAG_PX_THRESHOLD=8 / CURSOR_FUSION_WINDOW_MS=1000 / CURSOR_INDEPENDENT_TRIGGER=false`（Cursor §9） | config.py:18-23 数值全一致；`GET /api/cursor/config` 可下发（server.py:130-135）——但前端 throttle 硬编码未拉取（cursor.ts:8） | 一致（前端未消费配置，低） | — |
| 3-9 | MeetingSummary v2 schema（Input §3.3，含 `_candidates/_merge_keys/_raw_spans/_conf_basis/_ambiguous/_note`、edges 8 类关系） | models.py:124-173 逐字段对齐 | 一致 | — |
| 3-10 | Analyzer 不读图；Syncer 吃「Summary+图+filtered_focus」三输入（Input §3.1/§4.1） | assemble_analyze 不注图（orchestrator.py:154-161）✓；但真实 `llm.sync` 的 filtered_focus 参数恒空（见 3-1） | 一致（链路断） | — |
| 3-11 | Syncer 按需调 `fetch_metadata`：高置信不调、低置信必调（Input §9 Q4；Storage §11 Qc） | fetch_metadata 工具已写（metadata_tools.py）但无 LLM 调用通道（见 1-15/1-20），置信分档判定不存在 | 文档有但代码未实现 | 中 |
| 3-12 | `intent_hint` 默认关闭、字段保留（Cursor §3） | from_x6_event 透传 intent_hint（adapters.py:116），前端从不填充 | 一致 | — |
| 3-13 | WS 断线重连补发、按 event_id 幂等去重（Cursor §4/§8） | 前端 events.ts 无重连补发；后端无 event_id 去重 | 文档有但代码未实现 | 低-中 |
| 3-14 | 前端发言文本框 → `/api/cli/push`（网页发言人） | main.ts:152-176 + server.py:449-471，CLI/web 双调试口 | 代码有但文档未描述 | 低 |

---

## 4. 域四：前端看板（X6）与看板娘
> 比对：`Design_FrontendBoard.md`、`Design_Mascot.md`、`design-system/Miro.md`、`design-system/Linear.md` ↔ `main.ts`、`board/*`、`mascot/*`、`api/*`、`index.html`、`package.json`

| # | 文档描述 | 代码实际 | 类型 | 严重度 |
|---|---|---|---|---|
| 4-1 | 节点用 React/Vue 业务组件渲染（point-card 等，FrontendBoard §1.1；Research_X6_MindMap §5） | 内置 `rect` shape + label（render.ts:58-82）；package.json 引了 react/antd 但未注册任何 X6 组件 | 文档有但代码未实现 | 中 |
| 4-2 | 增量更新 `toJSON({diff:true})` + 局部 fromJSON（§1.2） | `applyBoardUpdate` 全量覆盖，注释自认 MVP 全量（render.ts:93-96） | 实现与文档不一致 | 中 |
| 4-3 | reach/route/lens + 深链 `#focus/#reach/#route/#lens`（§1.4） | nav.ts 完整实现邻接表/reach 递归/BFS route/lens 过滤；main.ts 接线；`transitive` 多跳已实现（nav.ts:63-104） | 一致 | — |
| 4-4 | route 逐段语义标注、对抗边（`_countered_by`）红色虚线（§1.4） | 高亮统一主蓝（render.ts:122-140），无对抗边特判 | 文档有但代码未实现 | 低 |
| 4-5 | 删除联动：节点删除时以之为端点的边随之删除（§1.5） | `op:remove` 已联动删边（server.py:268-273） | 一致 | — |
| 4-6 | 合并联动：merge 后旧节点出/入边迁移到保留节点（§1.5） | `merge_as_duplicate` 仅删节点，**边不迁移** → 产生悬空边（会被 validate 拒，操作回 skipped）（storage.py:186-192） | 文档有但代码未实现 | 中 |
| 4-7 | 绑定持久化：`data.bind`/`port_id` 随边落库（§1.5） | 无绑定字段 | 文档有但代码未实现 | 低 |
| 4-8 | 用户操作 UI：双击改名、卡片下拉改类型、锁按钮、连线、重要性控件（§2 表） | 后端 8 类 op 端点齐备（server.py:221-284）；前端**无任何编辑 UI**，SDK 的 emitUserOp/lock/setImportance/rollback 均未被 main.ts 调用 | 文档有但代码未实现（前端层） | 中-高 |
| 4-9 | 工具调用协议 ToolExecutor 六步（鉴权/校验/lock/skip_fields/推送/回传）（§3.3） | 后端落库主路径 `commit_graph_update` 覆盖校验/lock/回执/change_set；但无独立 ToolExecutor、无 LLM 调用回传闭环 | 实现与文档不一致 | 中 |
| 4-10 | GraphChangeSet 变更集预览 + skipped「用户已锁」徽标（§4.4） | `build_change_set` 后端实现（storage.py:269-288）；前端 ChangeHighlighter 闪烁 + 摘要文案（changes.ts 全文；main.ts:99-109）；徽标用红色虚框代替 | 基本一致 | — |
| 4-11 | Before→Delta→After 回放对比（§4.4） | 未实现 | 文档有但代码未实现 | 低 |
| 4-12 | 导出 PNG/SVG（§4.5） | `@antv/x6-plugin-export` + export.ts 已实现（main.ts:124-131） | 一致（WebM 回放未实现，低） | — |
| 4-13 | 只读快照分享链 `/view?token=`（§4.5） | 后端已实现（server.py:312-329）；但无 `/view` 渲染页，token 仅返回 JSON | 实现与文档不一致 | 低 |
| 4-14 | 主题：Miro 亮色画布+明黄 `#FFD02F` 强调、Linear 暗色面板 `#08090A`+lavender `#5E6AD2`（§6.1） | 实际为 **antd 蓝白主题**（`#1677ff` 系，index.html + main.ts token 注入） | 文档已过时/与现状矛盾 | 中 |
| 4-15 | 编辑/查看模式 `interacting` 切换（§6） | 写死 `nodeMovable: true`（render.ts:19），无模式切换 | 文档有但代码未实现 | 中 |
| 4-16 | 演示态/激光指针（§6.2） | 未实现 | 文档有但代码未实现 | 低 |
| 4-17 | BoardSDK 接口（§4.3） | BoardSDK.ts 全部实现并扩展 outline/lock/setImportance/rollback/history/snapshot/close | 一致（含扩展） | — |
| 4-18 | MascotState 11 态（Mascot §1.1：含 assembling/parsing/updating） | 后端 11 态全发（orchestrator.py:66-218）；**前端类型只有 8 态**，缺 `assembling/parsing/updating`（MascotController.ts:3-5），收到时气泡显示英文原值 | 实现与文档不一致 | 低-中 |
| 4-19 | 表情：SVG class 动画（呼吸/眨眼/点头/比划…）+ 文案气泡（Mascot §1.1/§5.1） | 仅气泡文案 + error 时整体透明度 0.6（MascotController.ts:21-25）；机器人造型与文档 SVG 结构不同（index.html:123-147） | 实现与文档不一致（简化） | 中 |
| 4-20 | `IDLE_RETURN_MS=1500` success/error 后自动回 idle（Mascot §6/Q3） | 前端无 idleTimer（MascotController.ts）；`/api/mascot/config` 下发了该参数（server.py:146-151）但前端未消费 | 文档有但代码未实现 | 低-中 |
| 4-21 | 眼睛追踪细节：鼠标移出回正、`prefers-reduced-motion`、transition 平滑、垂直幅度 85%、高光 catchlight（Mascot §4.1/Q5） | atan2+半径追踪已实现（eyes.ts:8-22）；其余五项均无 | 文档有但代码未实现 | 低 |
| 4-22 | `mascot_state` 事件 `label` 为中文文案（Mascot §2.1） | 后端 `label` 填 state 英文值（server.py:337），文案由前端 LABELS 表自译（MascotController.ts:7-16）——职责与文档相反 | 实现与文档不一致 | 低 |
| 4-23 | X6 技术栈 | `@antv/x6 ^2.18.1`（package.json:12），render.ts 做 v1→v2 cell 归一化（render.ts:27-86），与 Research_X6 文档结论吻合 | 一致 | — |

---

## 5. 最需要关注的 Top 5 差异（跨域汇总）

1. **真实 LLM 链路未按设计接通**（1-15/1-17/1-13）：assemble 组装的提示词是死数据，真实客户端另起炉灶自建提示 + 正则抠 JSON，无 function calling。设计中的 Prompt/Schema 契约与运行态是两张皮。
2. **光标流整条失效**（1-4/3-1/3-2/3-3）：`input_cursor` 无入边 → filter_cursor 永不执行 → `filtered_focus` 恒空 → `mouse_action` 恒空、软保护缺失；与此同时每条 cursor.event 又触发一次全量双 Agent 推理（1-24），与「光标不独立触发」的设计完全相反。
3. **工具层孤岛**（1-19/1-20/3-11）：8 个 Tool 齐备但既未注册给 LLM，也未挂到任何 REST/WS 端点；`fetch_metadata` 置信分档调用策略无从谈起。
4. **schema/命名漂移**（1-8/1-9/1-10/2-1/2-2/2-6/2-8/2-9）：GraphOp 字段结构、schema 名、shape 名、cell id 格式、StoreB kind/字段、speaker 前缀规则等与文档不一致，接真实模型或第三方消费方时按文档实现会失败。
5. **文档整体滞后于代码方向**：DevPlan 的「M0-M2 纯标准库」、MeetingSummary v1 字段（1-3/1-12）、FrontendBoard 的 Miro/Linear 主题（4-14）、React 组件渲染（4-1）、增量 diff（2-22/4-2）均已被代码的新决策（langgraph/pydantic、ms_v2、antd 蓝白、全量推送）超越，需批量回写。

## 6. 修复建议（按优先级）

| 优先级 | 动作 |
|---|---|
| P0 | 接通 LLM 真实链路：`analyze/sync_node` 改为消费 `llm_messages_*`；用 function calling 或 `response_format(json_schema)` 替代正则抠取；工具注册进 LLM |
| P0 | 修光标流：给 `input_cursor` 提供入口/合并入边或删除该支路；`cursor.event` 不再逐条触发 `agent.run`，仅缓存入 State 等待 ASR 批次合并；前端 drag 事件回填 `start/end_offset_ms` |
| P1 | 工具层接入 REST 或编排器；实现软保护 `skip_fields`；实现 merge 边迁移；`route_init` 真正短路；gen_initial 写议程 metadata_refs |
| P1 | schema 对齐：GraphOp 字段（node/old/new/level）、shape 名、schema 名、speaker `ent:` 前缀 —— 二选一：改代码或改文档，**必须单边收敛** |
| P2 | 前端补用户操作 UI（锁/改类型/改名/连线）；实现 diff 增量推送与局部 fromJSON；看板娘补 3 缺失状态与 IDLE_RETURN；hover 事件带 offset |
| P2 | 文档回写：DevPlan 里程碑与 ms_v2、FrontendBoard §6.1 主题与 §1.1 渲染方案、DataFlow §2.3 光标边、Input §6.3 vs §9 Q6 的矛盾（二选一） |

## 7. 一致性亮点（无需改动）

- 双 Agent 顶层职责、13 节点集合、`route_parse` 重试/降级、MAX_RETRY=2
- 受控词表（7 节点类型/8 语义边/5 库边/4 档重要性）在 models/prompts/storage 三处一致
- MeetingSummary v2 与 Input §3.3 逐字段一致（含全部 `_` 机器字段）
- last-good 交付门、版本历史/回滚、GraphChangeSet、乐观锁（用户操作路径）
- Prompt 两段/三段拼接顺序、`serialize_for_llm` 大纲、`stable_hash`（SHA-1 截断防 PYTHONHASHSEED）
- reach/route/lens + 深链、导出 PNG/SVG、快照分享 token、变更集闪烁高亮
- 光标采集参数数值（200ms/250ms/8px/1000ms/false）与 Cursor §9 完全一致
- 眼睛追踪（atan2+半径）、BoardSDK facade、X6 v2 归一化
