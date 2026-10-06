# 核心输入信息处理模块 · 详细设计（双协作 Agent）

> **文档状态**：设计 v0.2 · 待评审（与 `Design_Agent_DataFlow.md` v0.5 对齐：分析 Agent 收窄为「仅输入信息→会议纪要」；**新增 §3.4 提示词段拼接顺序（KV 缓存优化），与 DataFlow §2.5 呼应**）
> **最后更新**：2026-09-18
> **关联文档**：
> - `doc/Design_Agent_DataFlow.md`（总编排；本模块取代其 `reason_node`，拆为 `analyze_node` + `sync_node`）
> - `doc/ASR_UnifiedSchema.md`（输入 `NormUtterance`）
> - `doc/Research_CursorIntent_Capture.md`（输入 `NormCursorEvent` → `filtered_focus`）
> - `doc/Design_StructureGraph_Storage.md`（Store A 关系图 / Store B 元数据 / `metadata_refs` / `data.edit.*`）
> - `doc/Design.md`（§6.2 统一 LLM 接入接口、§4.4 字段级权限）
> **模块定位**：核心输入处理 = **两个协作的 Agent**，顺序串联于单批次流水线内（对齐 `Design_Agent_DataFlow.md` v0.5）：
> - **分析 Agent（Analyzer）**：**只吃「过滤后的输入信息」（语音文本 + 内嵌窗口化 `mouse_action`）**，**不加载关系图（board_graph）** → 产出**会议总结中间层 `MeetingSummary`**（结构化节点 + 显式关系网 + 机器专属补全字段，承载论点/论据/议题/结论/行动项/分歧/问题及其相互隐层关系、归因、置信度）。**不做图结构操作、不判跨图重复**。
> - **图同步 Agent（Syncer）**：吃「`MeetingSummary` + 当前关系图（Store A）」→ 产出**流程图源数据 `GraphUpdateOp`**（落 Store A 的原子操作）。**对照 board_graph 完成跨图重复/相悖检测与挂接，不改写总结语义**。
> 二者经 `AgentState.meeting_summary` 交接；都走 `Design.md §6.2` 的统一 LLM 接入接口（结构化输出）。

---

## 1. 为什么拆成两个 Agent（职责分离）

| 维度 | 单 Agent（旧 `reason_node`） | 双 Agent（本设计） |
|---|---|---|
| 输出 | 一步到位出 `GraphUpdateOp` | Analyzer 出 `MeetingSummary`，Syncer 出 `GraphUpdateOp` |
| 语义与图耦合 | 高（归因/去重与图操作混在一起，难验证） | 低（语义层与图映射层解耦） |
| 复用 | 总结仅用于图 | `MeetingSummary` 可同时喂**纪要生成、搜索、回顾问答**等下游 |
| 可验证 | 图操作难单测 | Analyzer 可独立测「抽取/去重/关联」；Syncer 可独立测「映射/尊重 lock」 |
| 成本/延迟 | 一次重推理 | 两次推理（Analyzer 偏语义档、Syncer 偏结构化档，可分别调档） |

**结论**：拆两个 Agent 是把「**理解会议**」与「**画结构图**」两件事分离，前者是领域语义、后者是图映射规则，职责清晰、可独立演进与测试。

---

## 2. 协作拓扑（单批次内顺序串联）

```
[统一入口] ASR `is_final` 批次
      │
      ▼
[filter] filtered_text + filtered_meta_ids        [filter_cursor] filtered_focus（仅服务软保护，不进提示词）
      └────────────────────┬───────────────────────────────────────┘
                           ▼
            [load_board / gen_initial] → board_graph（Store A cells）★ 仅提供给 Syncer
                           ▼
        ┌──────────────────────────────────────────────────────────┐
        │ assemble_analyze：仅输入信息（系统 + 过滤文本[含窗口化 mouse_action]） │
        │                ★ 不注入 board_graph / filtered_focus                   │
        └───────────────────────────┬──────────────────────────────┘
                                     ▼
                      ① 分析 Agent：analyze_node  ──▶ MeetingSummary（结构化节点+关系网+_机器字段）
                                     │  （语义层：抽取/批次内关联/归因/置信；不判跨图重复）
                                     ▼
        ┌──────────────────────────────────────────────────────────┐
        │ assemble_sync：系统 + 完整看板 cells + MeetingSummary（光标仅软保护上下文）│
        └───────────────────────────┬──────────────────────────────┘
                                     ▼
                      ② 图同步 Agent：sync_node  ──▶ GraphUpdateOp
                                     │  （图映射：对照 board_graph 判重复/挂接/相悖 + add/link/merge/replace/set_importance）
                                     ▼
                              [parse] ──▶ [update] ──▶ [present]（Store A，尊重 lock/edit；推送流程图展示模块）
```

> 触发：**仅 ASR `is_final` 批次**（光标 `is_final` 不独立触发，见 `Design_CursorCapture.md` §7/§9）。单次批次内 Analyzer→Syncer 顺序执行，不嵌套循环。

---

## 3. 分析 Agent（Analyzer）

### 3.1 输入

| 输入 | 来源 | 用途 |
|---|---|---|
| `system_prompt_analyze` | 系统（抽取口径 + `MeetingSummary` schema 规则 + 隐层关系补全要求） | 约束抽取行为 |
| `filtered_text` + `filtered_meta_ids` | `filter_node` | 本批次新流入的精炼转写（**已内嵌窗口化 `mouse_action`**，作为「输入信息」一部分） |
| `agenda_topics`（轻量） | 会议议程（可选） | 仅作**候选父议题提示**，不提供完整看板；最终挂接由 Syncer 对照 board_graph 定 |

> ★ **不输入 `board_graph` / `filtered_focus`**：Analyzer 看不到关系图全貌，只基于本批次输入信息 + 议程推测节点间隐层关系（以 `_candidates`/`parent_hint` 权重化候选表达），跨图重复/相悖交由 Syncer 判定。

### 3.2 处理职责（只做语义，不动图）

1. **抽取**：从过滤文本提取论点/论据/议题/结论/行动项/分歧/问题，每条约精炼成一句 `summary`（同时写 `_raw_spans` 保留原始文本 span 供溯源）。
2. **归因**：标 `speaker_ref`（沿用 `ASR_UnifiedSchema` 的 `ent:/ms:/spk:` 前缀），并累积 `speaker_stance`（发言者立场 map，隐层）。
3. **溯源**：把支撑该 insight 的原始句 `meta_id`（= `filtered_meta_ids`）写入 `evidence`；生成 `_merge_keys`（去重指纹）供 Syncer 判重复。
4. **批次内关联（织网）**：在本批次节点间建立**显式关系网 `edges`**（`support`/`oppose`/`derive`/`subordinate`/`elaborate`/`answer`/`resolve`），并补 `_derivation`（推导链）、`strength`（关联强度）、`transitive`、`_countered_by`（反向对抗）等隐层字段；对跨节点归属用 `parent_hint` + `_candidates`（权重化候选父）表达。
5. **置信/重要度**：给 `confidence` 与 `importance_hint`，并写 `_conf_basis`（置信依据，机器专属）。
6. **会议级隐层**：维护 `open_threads`（未决议题链）、`agenda_topics` 覆盖、`speaker_stance`，补全节点之上的宏观关系。

> **不做**：① 加载/读取 `board_graph`；② 判定「与已有节点重复/相悖」（那是 Syncer 对照真实看板的事）；③ 创建/移动/连接任何图节点。Analyzer 只产出「这次会议说了什么、彼此什么隐层关系」的语义层，`parent_hint`/`_candidates` 仅为候选推测，最终由 Syncer 拍板。

### 3.3 输出 Schema：`MeetingSummary`（v2）

**设计原则**
1. **结构化节点 + 显式关系网**：节点平铺于 `nodes[]`，关系用独立 `edges[]` 织网（替代旧单维 `related_to`），可表达多跳 / 传递 / 对抗。
2. **`_` 前缀 = 机器专属字段（不供人阅读）**：承载推导理由、置信依据、候选父/候选关联、去重指纹、原始 span、歧义、追踪痕迹。这些字段**不进入会议纪要展示正文**，仅供 Syncer 程序化消费与调试回溯。
3. **括号内补充强调信息**：允许在 `_note` 等机器字段中以中文括号写「不供人读的强调」（如「（用户 hover 该节点时强调，关注度偏高）」「（此处存疑，需 Syncer 二次确认）」），作为 LLM 可读的补充信号。

```json
{
  "_schema_version": "ms_v2",
  "analysis_note": "本批次围绕 Q3 渠道策略，确认成本上涨、出现预算分歧，未形成结论（见 open_threads）",
  "_trace": {                                    // ★ 机器专属：本次抽取全局推理痕迹
    "model": "analyzer-medium",
    "batch_seq": 1042,
    "board_snapshot": "n23",                    // 与 Syncer 对齐用看板快照号（仅标识，Analyzer 不读图）
    "ambiguous_count": 1,
    "dropped_insights": ["ins_x9"],             // 低置信被剔除的节点（供回溯）
    "_drop_reason": {"ins_x9": "confidence<0.4 且无 evidence"}
  },

  "agenda_topics": ["Q3渠道策略", "Q2预算"],      // 议程议题（节点挂接/未覆盖检测用）
  "speaker_stance": {                           // ★ 发言者立场 map（隐层宏观关系）
    "ent:user_zhang": {"supports": ["ins_a1"], "opposes": ["ins_b2"], "_lean": "cost_cut"},
    "ent:user_li":   {"supports": ["ins_b2"], "_lean": "growth"}
  },
  "open_threads": [                             // ★ 未决议题链（跨节点隐层）
    {"thread_id": "t_q3", "about": "Q3 主攻方向未定",
     "involved": ["ins_a1","ins_b2","ins_c3"],
     "_unresolved_because": "双方各执一词，缺 conclusion 节点",
     "expect_close": "action:act_d1"}           // 指向某行动项，完成后闭环
  ],

  "nodes": [
    {
      "node_id": "ins_a1b2c3",
      "type": "point",
      "summary": "渠道成本比去年涨30%",
      "speaker_ref": "ent:user_zhang",
      "evidence": ["utt_0012","utt_0045"],
      "confidence": 0.9,
      "importance_hint": "high",
      "source_offset_ms": [480200, 483900],

      "parent_hint": "iss_q3",                  // 建议挂接的父议题（候选，非最终）
      "_candidates": [                          // ★ 权重化候选父（供 Syncer 对照 board_graph 择优）
        {"target": "iss_q3", "score": 0.82, "by": "topic_overlap"},
        {"target": "iss_q2", "score": 0.31, "by": "speaker_mention"}
      ],
      "_sem_role": "claim",                     // claim/evidence/premise/conclusion/question
      "_merge_keys": ["渠道成本", "涨30%", "year_over_year"],  // ★ 去重指纹（Syncer 判 merge_as_duplicate）
      "_raw_spans": [                           // ★ 原始文本 span（机器溯源，不进正文）
        {"meta_id": "utt_0012", "quote": "渠道成本同比涨了三成", "offset_ms":[480200,480760]},
        {"meta_id": "utt_0045", "quote": "财务那边确认过", "offset_ms":[482100,482640]}
      ],
      "_conf_basis": "两个独立 utt 一致提及 + 财务确认(evidence)",  // ★ 置信依据（机器专属）
      "_ambiguous": false,                      // 歧义标记
      "_note": "(用户 hover 该节点时强调，关注度偏高)"  // 括号内补充强调（光标焦点信号，机器可读）
    }
  ],

  "edges": [                                    // ★ 显式关系网：补全节点间隐层关系
    {
      "edge_id": "e1", "from": "ins_a1b2c3", "to": "iss_q3",
      "relation": "subordinate", "strength": 0.82, "transitive": true,
      "_derivation": "ins_a1 是 iss_q3 下的具体数据点（topic=渠道成本）",  // ★ 推导链（机器专属）
      "_bidirectional": false
    },
    {
      "edge_id": "e2", "from": "ins_a1b2c3", "to": "ins_b2c4d5",
      "relation": "support", "strength": 0.6,
      "_derivation": "ins_a1（成本涨）支持 ins_b2（应砍预算）的论证前提",
      "_countered_by": "ins_c3"                 // ★ 反向被反驳边（隐层对抗）
    }
  ]
}
```

**字段表（新增/变更项，旧 `insights[].*` 已重构）**

**顶层**
| 字段 | 说明 |
|---|---|
| `_schema_version` | 契约版本（ms_v2），便于下游兼容 |
| `_trace` | ★ 机器专属：模型/批次号/看板快照号/被剔除节点/剔除原因 |
| `agenda_topics` | 议程议题，供挂接与未覆盖检测 |
| `speaker_stance` | ★ 发言者立场 map（谁支持/反对哪些节点、倾向） |
| `open_threads` | ★ 未决议题链（跨节点宏观关系 + 闭环条件） |

**`nodes[]`**
| 字段 | 说明 |
|---|---|
| `node_id` | Analyzer 生成 UUID（本批次唯一，供 `edges`/Syncer 引用） |
| `type` | `point`/`conclusion`/`issue`/`action`/`disagreement`/`question`/`evidence`（对齐 `Storage` §3.3） |
| `summary` | 精炼论点文本（非原始长句） |
| `speaker_ref` / `evidence` / `confidence` / `importance_hint` / `source_offset_ms` | 同旧义（归因/溯源/置信/重要度/时间窗） |
| `parent_hint` | 建议父议题（候选推测，非最终挂接） |
| `_candidates` | ★ 权重化候选父/候选关联（`score` + 依据），Syncer 对照 `board_graph` 择优 |
| `_sem_role` | ★ 语义角色（`claim`/`evidence`/`premise`/`conclusion`/`question`） |
| `_merge_keys` | ★ 去重指纹，Syncer 据其判 `merge_as_duplicate` |
| `_raw_spans` | ★ 原始文本 span（`quote` + `offset`），机器溯源、不进正文 |
| `_conf_basis` | ★ 置信依据（机器专属可解释） |
| `_ambiguous` | ★ 歧义标记（`true` 时 Syncer 需二次确认/降权） |
| `_note` | ★ 括号内补充强调（如光标关注度信号），机器可读、不供人 |

**`edges[]`**
| 字段 | 说明 |
|---|---|
| `edge_id` / `from` / `to` | 关系边标识与端点（`node_id` 或议题 id） |
| `relation` | `support`/`oppose`/`derive`/`subordinate`/`elaborate`/`answer`/`resolve`/`question_on` |
| `strength` | 关联强度 0–1（Syncer 据其设边权重/重要度） |
| `transitive` | 是否可传递（A→B→C 则 A 间接支撑 C） |
| `_derivation` | ★ 推导链说明（机器专属，可解释） |
| `_countered_by` | ★ 反向对抗边（隐层冲突，供 Syncer 标相悖） |
| `_bidirectional` | 是否双向 |

> **Syncer 消费预览**（详见 §4）：`nodes[]` → `add_node`/`merge_as_duplicate`/`replace`/`set_importance`（据 `_merge_keys` 与 `board_graph` 判重复；`parent_hint`+`_candidates` 定挂接）；`edges[]` → `link`（据 `relation`/`strength`/`_countered_by`）；`_trace`/`_raw_spans`/`_conf_basis` 仅调试与 `fetch_metadata` 兜底用，不进图。

> `MeetingSummary` 仍是**可复用中间层**：除喂 Syncer 外，可直达纪要生成、回顾问答等下游；对外展示时**剥离所有 `_` 前缀字段与 `edges` 的 `_*` 字段**，仅留人类可读的 `summary`/`type`/结构化关系。

### 3.4 提示词段拼接顺序（KV 缓存优化）

> 底层依据见 `Design_Agent_DataFlow.md` §2.5（KV cache 前缀复用 + sglang RadixAttention 树形共享）。本节把「每个 Agent 的 prompt 由哪些段、按什么顺序拼」写死，实现层直接套用，禁止夹心。

**分析 Agent（`assemble_analyze`）—— 严格两段**

```
[1] system_prompt_analyze            ← 全局长前缀（缓存必命中）
    └─ 抽取口径 + MeetingSummary schema 规则 + 隐层关系补全要求 + 固定 few-shot
    └─ 跨批次/跨会议逐 token 一致
[2] filtered_text + mouse_action     ← 尾部（变量，必然不在缓存）
    └─ 本批次新流入的精炼转写（已内嵌窗口化 mouse_action）
    └─ 置于最后，使「失效后缀」最短
```

- 不出现 `board_graph` / `filtered_focus` / 任何批次相关变量 → 前缀绝不被污染（与 §3.1 / §3.2 职责一致，且是 KV 双赢）。

**规划 Agent（`assemble_sync`）—— 严格三段**

```
[1] system_prompt_sync              ← 全局长前缀（缓存必命中）
    └─ 图操作 schema + 边类型枚举(8类) + 去重/挂接口径 + 固定 few-shot
[2] board_graph 序列化               ← 中段（半稳定）
    └─ serializeForLLM(board_graph)：仅表达图结构与节点 data
    └─ 变化频率低：仅本批次 update_node 落库后下一次才变
[3] meeting_summary                  ← 尾部（变量，每批次全新）
    └─ analyze_node 产出
```

- ★ **顺序不可调换**：`[2]` 必须在 `[3]` 之前。若 `[3]` 插入 `[1][2]` 之间，board_graph 段前缀断裂失效（详见 DataFlow §2.5 反模式）。
- `board_graph` 序列化须保持**稳定 token 形态**：节点按 `node_id` 排序、`data` 字段顺序固定，避免同语义不同 token 破坏命中。

---

## 4. 图同步 Agent（Syncer）

### 4.1 输入

| 输入 | 来源 | 用途 |
|---|---|---|
| `system_prompt_sync` | 系统（图映射规则 + `GraphUpdateOp` schema + lock/edit 约束） | 约束映射行为 |
| 完整看板 cells | `board_graph`（Store A） | 决定 add vs merge vs link，避免重复建节点 |
| `MeetingSummary` | `analyze_node` 输出 | 要被映射成图操作的语义层 |
| `filtered_focus` | `filter_cursor_node` | 「挂接位置建议 / 勿改正在操作的节点」（仅软保护上下文，不进 `MeetingSummary`） |

### 4.2 处理职责（只做图映射，不改写语义）

1. **映射节点**：遍历 `MeetingSummary.nodes[]` → `add_node`（新）/`merge_as_duplicate`（据 `_merge_keys` 命中已有）/ `replace`（新结论取代旧）；挂接父由 `parent_hint`+`_candidates`（对照 `board_graph` 择优）决定。
2. **映射关系**：遍历 `edges[]` → `link`（`relation`/`strength` 映射边类型与权重；`_countered_by` 标相悖）；`open_threads`/`speaker_stance` 用于重要度与闭环提示。
3. **尊重 lock/edit**：对 `data.lock.locked=true` 或 `data.edit.*` 标记的节点，**跳过对应字段写回**（与 `Storage` §7 同源；光标拖拽软保护见 §4.3）。
4. **绑定元数据**：每个 `add_node`/`merge` 把 `evidence`（`meta_id`）写入 `metadata_refs`（对齐 `Storage` §5）；`_conf_basis`/`_raw_spans` 供 `fetch_metadata` 兜底。
5. **跨图重复/相悖终判**：Analyzer 不读图，此处对照 `board_graph` 完成「与已有节点重复/相悖」的最终判定（含 `_ambiguous` 节点二次确认）。

### 4.3 输出 Schema：`GraphUpdateOp`（沿用 `Design_Agent_DataFlow.md` §3）

```json
{
  "operations": [
    { "op": "add_node", "node": { "type": "point", "label": "渠道成本比去年涨30%", "speaker_ref": "ent:user_zhang" },
      "parent": "n_issue_q3", "meta_ids": ["utt_0012", "utt_0045"] },
    { "op": "link", "source": "n_point_cost", "target": "n_issue_q3", "relation": "subordinate" },
    { "op": "merge_as_duplicate", "node": "n_point_cost", "meta_ids": ["utt_0099"] },
    { "op": "replace", "old": "n_concl_old", "new": { "type": "conclusion", "label": "Q3 主攻存量复购" }, "meta_ids": ["utt_0077"] },
    { "op": "set_importance", "node": "n_point_cost", "level": "high" }
  ],
  "thought": "简短推理说明（可解释性）"
}
```

`op` → 落库动作映射见 `Design_Agent_DataFlow.md` §3 表（`add_node`/`link`/`merge_as_duplicate`/`replace`/`set_importance`），其中 `meta_ids` 直接来自 Analyzer 的 `evidence`。

> **光标软保护**：当 `filtered_focus` 中 `gesture_type ∈ {drag_node,hold_node}` 命中某节点，Syncer 在该节点的 `replace`/`add` 操作中标记 `skip_fields:["label","type","position"]`（见 `Design_CursorCapture.md` §6）。

---

## 5. 工具调用（Syncer 可调用）

Syncer 在统一 LLM 接入接口（§6.2）下注册标准 tool，按需取论据：

```json
{ "name": "fetch_metadata",
  "description": "按 meta_id 拉取 Store B 原始论据文本（说话人+时间+原文），用于判定相悖或生成引用",
  "parameters": { "ids": ["utt_0012"] },
  "returns": [ { "meta_id": "utt_0012", "speaker": "ent:user_zhang", "text": "…", "start_offset_ms": 480200 } ] }
```

> 默认不把全文转写塞进提示词（防膨胀）；需细节时才调 `fetch_metadata`，对齐 `Storage` §5 / §6.2。

---

## 6. LangGraph 节点与 State 更新

### 6.1 节点表（取代 `reason_node`）

| 节点 | 职责 | 输入←State | 输出→State |
|---|---|---|---|
| `input_node` / `input_cursor_node` | 收 ASR / 光标批次 | — | — |
| `filter_node` / `filter_cursor_node` | 过滤 / 去抖聚合 | raw_* | `filtered_*` |
| `load_board_node` / `gen_initial_node` | 载图 / 初始生成 | — | `board_graph`,`board_loaded` |
| `assemble_analyze_node` | 组装 Analyzer 提示词（**仅输入信息**：系统 + 过滤文本[含 mouse_action]） | `system_prompt`,`filtered_text` | `llm_messages_analyze` |
| `analyze_node` | **分析 Agent**：Analyzer → `MeetingSummary` | `llm_messages_analyze` | `meeting_summary` |
| `assemble_sync_node` | 组装 Syncer 提示词 | `system_prompt`,`board_graph`,`meeting_summary`,`filtered_focus` | `llm_messages_sync` |
| `sync_node` | **图同步 Agent**：Syncer → `GraphUpdateOp` | `llm_messages_sync` | `llm_output` |
| `parse_node` | 校验 `GraphUpdateOp` | `llm_output` | `parse_ok`,`update_patch` |
| `update_node` | 应用增量（尊重 lock/edit + 软保护） | `update_patch`,`filtered_focus` | 持久化 |

### 6.2 State 扩展（TypedDict）

```python
class AgentState(TypedDict):
    meeting_id: str
    meeting_title: str
    raw_utterances: List[NormUtterance]
    raw_cursor_events: List[NormCursorEvent]
    filtered_text: List[str]
    filtered_meta_ids: List[str]
    filtered_focus: List[NormCursorEvent]
    board_loaded: bool
    board_graph: Optional["BoardGraph"]
    system_prompt: str
    # —— 双 Agent 交接 ——
    llm_messages_analyze: List[dict]   # assemble_analyze 输出
    meeting_summary: Optional["MeetingSummary"]   # ★ Analyzer 输出（两 Agent 交接物）
    llm_messages_sync: List[dict]      # assemble_sync 输出
    llm_output: Optional["GraphUpdateOp"]         # Syncer 输出
    parse_ok: bool
    retry_count: int
    update_patch: Optional[dict]
```

### 6.3 边与条件路由

```python
g.add_edge("filter", "route_init")
# （光标流不再汇入 assemble_analyze：mouse_action 已内嵌 ASR 文本；filtered_focus 仅服务 update_node 软保护）
g.add_conditional_edges("filter", route_init, {"load_board":"load_board","gen_initial":"gen_initial"})
g.add_edge("load_board", "assemble_analyze")
g.add_edge("gen_initial", "assemble_analyze")
g.add_edge("assemble_analyze", "analyze")
g.add_edge("analyze", "assemble_sync")                   # ★ 交接 meeting_summary
g.add_edge("assemble_sync", "sync")
g.add_edge("sync", "parse")
g.add_conditional_edges("parse", route_parse,
    {"update":"update", "assemble_analyze":"assemble_analyze"})   # 重试回到 analyze 重抽
g.add_edge("update", END)
```

> 重试：校验失败且 `retry_count < MAX_RETRY` 时回到 `assemble_analyze` 重跑双 Agent（Analyzer 可基于 `parse` 反馈纠正）；超限降级仅更新已可靠部分。

---

## 7. 配置（模型档位 / 重试）

| 项 | Analyzer | Syncer | 说明 |
|---|---|---|---|
| 模型档位（§6.2 effort） | 中等（语义优先） | 偏低（结构化映射） | 分别调档控成本/延迟 |
| 流式 | 可选 | 否（需完整 JSON） | Syncer 需稳定结构化输出 |
| 重试上限 `MAX_RETRY` | 2 | — | 超限降级 |
| 上下文 | 仅过滤文本[含窗口化 mouse_action]（不加载看板） | 完整 cells + MeetingSummary | — |

---

## 8. 与既有文档一致性

| 本模块 | 关联文档 |
|---|---|
| 输入 `NormUtterance` / `NormCursorEvent` | `ASR_UnifiedSchema.md` / `Research_CursorIntent_Capture.md` |
| 看板摘要 `serializeForLLM` | `Design_StructureGraph_Storage.md` §6.2 |
| `GraphUpdateOp` 落库语义 | `Design_Agent_DataFlow.md` §3 |
| `metadata_refs` 绑定 | `Design_StructureGraph_Storage.md` §5 |
| lock/edit 软保护 | `Design_StructureGraph_Storage.md` §7 / `Design_CursorCapture.md` §6 |
| 统一 LLM 接入 / 工具调用 | `Design.md` §6.2 |
| `MeetingSummary` 下游复用（纪要/问答） | `Design.md`（规划中纪要生成） |

---

## 9. 待确认 / 开放项

- Q1（已闭合）：**不短路**——纯规则 `filter_node` 已丢绝大多数闲聊，分析仪仍跑保证不漏实质发言；双调用为常态（代价已由 `Design.md` §9「长会议成本」在 M1 实测评估，非当前阻塞）。
- Q2（已闭合）：Syncer 看板上下文**默认分层序列化**——小图用完整 cells，大图仅序列化与本次 `meeting_summary` 相关的子树（按 `parent_hint`/`_candidates` 命中节点做 BFS 邻域），超界节点以占位引用替代，控 token 长度（呼应 DataFlow §2.5 稳定 token 形态）。
- Q3（已按此方案落地）：跨图「重复/相悖」终判在 Syncer（对照 `board_graph`）；向量召回（`Storage` §9）作 Syncer 兜底（大图时 `parent_hint`/`_candidates` 初筛后召回精排）。Analyzer 只产出批次内 `edges` 与 `_merge_keys`，不读看板。
- Q4（已闭合）：**高置信不调、低置信调**——`confidence ≥ 0.8` 且 `relation` 明确（support/oppose/derive）时直接判定，不调 `fetch_metadata`；`confidence < 0.8` 或关系模糊（如 `_ambiguous=true`）时调 `fetch_metadata` 拉原文精排（呼应 `Storage` §11 Qc）。
- Q5（已闭合）：**默认不持久化**——`MeetingSummary` 为批次内中间态，仅内存流转喂 Syncer；最终产物是 `board_graph` + 对外会议纪要（由前端/下游生成）。降低存储复杂度；若将来需审计追溯再开独立集合（不进 Store A/B）。
- Q6（已闭合）：**缓存复用**——`meeting_summary` 在批次内缓存；Syncer 重试（`MAX_RETRY=2`，见 DataFlow §5 Q4）复用同一 `meeting_summary`，**不重跑 Analyzer**（避免双 LLM 调用翻倍、保证图谱一致性）。
