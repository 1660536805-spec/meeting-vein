# 看板结构图 — 存储与数据格式设计

> **文档状态**：草稿 v0.2 · 待评审
> **最后更新**：2026-09-16
> **关联文档**：`doc/Design.md`（§0.1 节点/边类型、§4.4 字段级权限、FR-LIVE-17/18、§6.2 统一 LLM 接入）、`doc/ASR_UnifiedSchema.md`（NormUtterance / speaker_ref / utterance_id）、`doc/Research_X6_MindMap.md`（X6 能力）
> **修订**：v0.1 → v0.2，按「关系图属后处理结果、原始语音文本属元数据、二者分库；图保持精炼并绑定元数据 id；图用于前端展示与 LLM 提示词两种场景」重写架构。

---

## 1. 设计目标与原则

| 原则 | 说明 |
|---|---|
| **双存储分离** | 「节点关系图」是后处理**派生结果数据**；「原始语音/议程文本」是**输入元数据**。二者分库存储，生命周期与读写模式不同 |
| **图保持精炼** | 关系图只存结构化结论（类型 / 精炼文本 / 说话人 ref / 重要性 / 关系），**不内嵌原始转写长文本**；原始文本唯一存放在元数据 store |
| **元数据 id 绑定** | 每个节点通过 `metadata_refs` 绑定其来源元数据 id，前端可随时反查「本节点由哪些论据精炼而来」 |
| **两种使用** | ① 经 API 载入前端由 X6 `fromJSON` 展示；② LLM 编排时序列化为大纲注入提示词 |
| **data 为真相源** | 节点/边语义与状态全在 `data`；`attrs`（SVG 样式）加载时由 `data` 派生，除人工 `style_override` 外不持久化派生样式 |

---

## 2. 双存储架构

```
[原始输入：语音流 / 议程]
      │  ASR + 说话人分离 + 议程解析
      ▼
┌──────────────────────────────┐
│ Store B · 元数据 Metadata      │  ← 原始输入，按 meta_id 索引（append-only）
│  NormUtterance / 议程项        │
└──────────────┬───────────────┘
               │  后处理（要点抽取 / 匹配去重 / 关系推断）
               ▼
┌──────────────────────────────┐
│ Store A · 节点关系图 Graph     │  ← 精炼派生，X6 cells
│  节点 + 边，含 metadata_refs   │
└──────┬──────────────────┬─────┘
       │                  │
  前端展示(fromJSON)    LLM 提示词(大纲序列化)
       │                  │
       │ 点击节点查论据     │ 需论据细节时按 meta_id 拉取
       └─ GET /metadata?ids=node.metadata_refs ┘
```

- **Store A（图）**：体量小、变更频繁、需 X6 零翻译、需被 LLM 注入。
- **Store B（元数据）**：体量大、append-only、按 id 查询、是原始文本唯一真相源。
- 两者通过 `metadata_refs`（节点→meta_id 列表）单向关联；图不反向内嵌文本。

---

## 3. Store A：节点关系图（精炼、X6 友好）

### 3.1 X6 序列化口径（核实）

- `graph.toJSON()` 返回 `{ cells: [...] }`，节点与边按渲染顺序混合。
- `graph.fromJSON()` 同时支持 `cells` 数组与 `{ nodes:[], edges:[] }` 对象。
- 节点元数据结构：`{ id, shape, position:{x,y}, size:{width,height}, attrs, zIndex, visible, data }`；`data` 专存业务数据、不参与渲染。
- 边元数据结构：`{ id, shape, source, target, attrs, labels }`；`source`/`target` 用 **cell id**。
- `toJSON({ diff: true })` 仅导出变化字段，用于增量推送。
- 落库前需 `omit` 内部字段 `__type` / `__id`。

### 3.2 顶层结构（canonical）

```json
{
  "schema": "ai-moderator.board/v1",
  "graph_id": "mtg_20260916_q3growth",
  "meeting_id": "mtg_20260916_q3growth",
  "version": 42,
  "updated_at": "2026-09-16T19:30:00Z",
  "cells": [ "/* 节点 + 边，混合数组，与 toJSON() 同构 */" ]
}
```

- **cell id = 业务 id**：节点 `n_<语义>`，边 `e_<关系>_<from>_<to>`；免去 cell id ↔ 业务 id 映射。
- 提供 `normalize(cells)` → `{ nodes:[], edges:[] }` 供人类可读存储 / 调试 / 非 X6 消费方。

### 3.3 节点单元格（精炼）

```json
{
  "id": "n_point_cost_01",
  "shape": "mind-node",
  "position": { "x": 200, "y": 160 },
  "size": { "width": 240, "height": 64 },
  "data": {
    "type": "point",
    "label": "渠道成本比去年涨 30%",
    "speaker_ref": "ent:user_zhang",
    "importance": { "level": "high", "score": 0.82 },
    "mention_count": 3,
    "edit": { "text_edited": false, "type_edited": false, "position_frozen": false, "importance_override": false },
    "lock": { "locked": false, "locked_by": null, "locked_at": null },
    "resolved": true,
    "metadata_refs": ["utt_0012", "utt_0045", "utt_0098"],
    "style_override": null,
    "version": 7,
    "created_at": "2026-09-16T19:02:11Z",
    "updated_at": "2026-09-16T19:28:40Z"
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `data.type` | enum | `issue`/`point`/`conclusion`/`action`/`disagreement`/`question`（Design §0.1） |
| `data.label` | string | **精炼后的要点文本**（非原始长句） |
| `data.speaker_ref` | string\|null | 提出者 ref：`ent:`/`ms:`/`spk:`（见 ASR_UnifiedSchema），`null`=系统/议程 |
| `data.importance` | object | `level`(critical/high/normal/low) 驱动视觉；`score` 可选 |
| `data.mention_count` | int | 被提及次数（AI 独占） |
| `data.edit` / `data.lock` | object | 字段级权限与锁定（§5） |
| `data.resolved` | bool | 分歧/问题是否收敛 |
| **`data.metadata_refs`** | **string[]** | **绑定来源元数据 id（精炼来源）**；前缀 `utt_`=语音句、`agd_`=议程项、`man_`=人工，前端据此反查论据 |
| `data.style_override` | object\|null | 人工显式覆盖样式 |
| `data.version` / 时间戳 | — | 版本与审计 |

> **精炼要点**：图内**不含**原始转写全文、说话人姓名、时间戳细节——这些只在 Store B。图只保留展示与编排必需的最小字段 + `metadata_refs` 指针。

### 3.4 边单元格

```json
{
  "id": "e_sub_issue_cost_01",
  "shape": "mind-edge",
  "source": "n_issue_q3",
  "target": "n_point_cost_01",
  "labels": [{ "attrs": { "label": { "text": "从属" } } }],
  "data": {
    "relation": "subordinate",
    "created_by": "ai",
    "confidence": 0.95,
    "conflict": false,
    "version": 2
  }
}
```
`data.relation` ∈ `subordinate`/`support`/`oppose`/`duplicate`/`replace`（Design §0.1）。

---

## 4. Store B：语音文本元数据（原始输入）

按 `meta_id` 索引的原始记录，**是转写文本的唯一真相源**。结构对齐 `ASR_UnifiedSchema` 的 `NormUtterance`，并扩展议程/人工来源。

```json
{
  "meta_id": "utt_0012",
  "kind": "utterance",
  "meeting_id": "mtg_20260916_q3growth",
  "seq": 12,
  "speaker": { "speaker_ref": "ent:user_zhang", "display_name": "张工", "is_resolved": true },
  "text": "我看了一下去年的投放数据，渠道成本整体比去年涨了大概 30%，尤其是信息流那块。",
  "language": "zh",
  "start_offset_ms": 480200,
  "end_offset_ms": 483900,
  "received_at_ms": 484100,
  "is_final": true,
  "translation": null,
  "source": "tencent",
  "raw_ref": "mtg_xxx/asr-push/sid_xxx",
  "created_at": "2026-09-16T19:02:12Z"
}
```

| 字段 | 含义 |
|---|---|
| `meta_id` | 全局唯一；前缀 `utt_`/`agd_`/`man_` 区分来源 |
| `kind` | `utterance`(语音句) / `agenda`(议程项) / `manual`(人工录入) |
| `speaker` | 说话人 ref + 显示名 + 是否可跨会议解析（来自 SpeakerResolver） |
| `text` | **原始转写全文** |
| `start/end_offset_ms` | 相对会议起点的时间段 |
| `source` / `raw_ref` | 来源后端与原始回执指针（对账用） |
| `agenda` 类额外 | `agenda_item_id`、`agenda_text`（议程解析产物） |

> 议程项（`kind:"agenda"`）也是元数据：会前解析的议题骨架作为顶层 `issue` 节点的 `metadata_refs` 来源之一。

---

## 5. 元数据绑定与论据溯源

**绑定规则**
- 后处理抽取要点时，把"支撑本节点精炼文本的原始句"的 `meta_id` 写入 `data.metadata_refs`。
- 重复提及：向 `metadata_refs` **追加并去重** `meta_id`，`mention_count` 同步 +1。
- 取代/分歧：旧节点 `metadata_refs` 保留（演进痕迹），新节点挂自己的 `metadata_refs`。

**前端查论据流程**
```
用户点击节点 n_point_cost_01
  → 读 node.data.metadata_refs = ["utt_0012","utt_0045","utt_0098"]
  → GET /api/metadata?ids=utt_0012,utt_0045,utt_0098
  → 渲染原始句列表（说话人 + 时间 + 文本 + 来源）
```
无需在图里存任何原始文本，即实现「查看本节点由哪些论据精炼而来」。

**LLM 按需取论据**
- 默认只把精炼图大纲注入提示词（见 §6.2），prompt 不膨胀。
- 当编排需要论据细节（如判定相悖、生成纪要引用），LLM 通过工具 `fetch_metadata(ids)` 按 `meta_id` 拉取 Store B，避免全量转写进上下文。

---

## 6. 节点关系图的两种使用

### 6.1 前端展示（API 载入）

- `GET /api/board/:graph_id` → 返回 Store A `cells` → `graph.fromJSON(cells)` 渲染。
- 实时增量：`graph.toJSON({ diff: true })` 推送变更 cells，`graph.fromJSON(diff)` 局部更新（降上板延迟，Design §4.5）。
- 点击节点触发 §5 论据反查。

### 6.2 LLM 编排提示词注入

- `serializeForLLM(cells)` 把图压成紧凑大纲（缩进树），注入 system/user prompt 作为「当前看板状态」：

```
[议题] Q3 增长策略
  ├─ [要点] 渠道成本比去年涨30% (发言人:ent:user_zhang, 提及3次) [论据:utt_0012,utt_0045,utt_0098]
  ├─ [结论] Q3 主攻存量复购
  │     └─ [取代] 旧结论: Q3 拓新客 (由 utt_0077 推翻)
  └─ [分歧] 是否砍掉低毛利渠道 (未收敛)
```
- **精炼原则**：仅序列化 `type`/`label`/`speaker_ref`/`mention_count`/`relation` 与 `metadata_refs` 指针，**不塞原始文本**；符合 Design §6.2「统一 LLM 接入接口」下事件触发 + 结构化上下文的诉求。
- 触发策略沿用 Design §4.2（语义完整 / 结论性表述 / 说话人切换 / 静默）决定何时重新序列化并注入。

---

## 7. 状态字段与人工操作兼容（Design §4.4 + FR-LIVE-17/18）

| §4.4 字段归属 | 落库字段 | 行为 |
|---|---|---|
| 节点文本：人工编辑后冻结 | `edit.text_edited=true` | AI 不再改写 `label` |
| 节点类型：人工可改，AI 可建议 | `edit.type_edited=true` | 人工值优先 |
| 位置/层级：人工拖拽后冻结 | `edit.position_frozen=true` | AI 不再重排 `position` |
| 提及次数：AI 独占 | （无标记） | 仅 AI 写 `mention_count` |
| 关系边：双写，冲突保留双方 | `conflict=true` | 人工+AI 同边冲突时标记，双方保留 |
| 重要性：AI 打分 + 人工可覆盖 | `importance.manual_override=true` | 人工值优先 |
| 整体锁定（FR-LIVE-17） | `lock.locked=true` | 该节点 AI 完全只读，跳过所有自动写回 |

**自动写回伪代码**
```
onAutoUpdate(cell, patch):
  if cell.data.lock.locked: return SKIP
  if 'label'   in patch and cell.data.edit.text_edited:        return SKIP
  if 'type'    in patch and cell.data.edit.type_edited:        return SKIP
  if 'position'in patch and cell.data.edit.position_frozen:     return SKIP
  if 'importance' in patch and cell.data.importance.manual_override: return SKIP
  cell.data.version += 1
  snapshotToHistory(cell)          # 入版本历史
  applyPatch(cell, patch)
  # 若 patch 含新论据，同步追加 metadata_refs（去重）
```

---

## 8. 版本历史（FR-LIVE-18 回滚）

独立 append-only 集合，仅存 Store A 单元格快照：

```json
{
  "graph_id": "mtg_20260916_q3growth",
  "cell_id": "n_point_cost_01",
  "version": 7,
  "changed_by": "ai",
  "trigger": "repeat-mention",
  "snapshot": { "/* 改动前完整 data + position */" },
  "created_at": "2026-09-16T19:28:40Z"
}
```
- 一键回滚：取 `snapshot` 还原 `data` + `position`。
- 人工编辑同样入历史（`changed_by:"human"`），满足可观测性（Design §5）。

---

## 9. 存储后端建议（对齐 Design §7）

| Store | 推荐 | 说明 |
|---|---|---|
| **A 关系图** | 单文档 `jsonb` / doc | 真相源，X6 零翻译；`version`/`updated_at` 做乐观锁 |
| **B 元数据** | 按 `meta_id` 的 KV / doc 集合 | append-only，按 id 列表查询；可作跨会议检索索引 |
| 版本历史 | 独立 append-only 集合 | 见 §8 |
| 向量检索 | 节点匹配召回（Design §4.1） | 对 Store B `text` 做嵌入，独立于两 store |

> 真相源始终是 Store A（图）+ Store B（元数据）；图库/向量库仅派生索引。

---

## 10. 与既有文档一致性

| 维度 | 来源 | 本设计 |
|---|---|---|
| 节点 6 类 / 边 5 类 | Design §0.1 | `data.type` / `data.relation` |
| 字段级权限 | Design §4.4 | `data.edit` + `data.lock` |
| 说话人标识 | ASR_UnifiedSchema | `data.speaker_ref`（图内仅 ref） |
| 句子溯源 | ASR_UnifiedSchema | `data.metadata_refs` → `meta_id`（= utterance_id 加前缀） |
| 统一 LLM 接入 | Design §6.2 | §6.2 大纲序列化注入提示词 |
| 可视化底座 | Research_X6_MindMap | `shape: mind-node/mind-edge` + `data` 渲染 |

---

## 11. 落地建议与待确认

1. **M0**：定 `shape` 注册名与 React/Vue 渲染契约；定 `decorate()` 派生 `attrs` 规则；定 `meta_id` 生成与前缀规范。
2. **M1**：打通 `Store B(元数据) → 后处理 → Store A(cells) → fromJSON 渲染`，验证图往返无丢失、`metadata_refs` 可反查。
3. **M2**：接入 `edit`/`lock` 标记、版本历史回滚、前端论据反查面板。
4. **待确认**：
   - Qa（已闭合）：`metadata_refs` **限制最大 20 条**；超限时保留高置信/近源引用，丢弃低价值重复引用（防噪声节点膨胀，呼应 `Storage` §5）。
   - Qb（已闭合）：`SpeakerResolver` 落库于 **Store B 的 speaker 维度表**；`ent:` 真人直连 `speaker.display_name`（跨会议稳定），`local:`/`anon:` 仅本会议临时标识不跨会议追踪（呼应 `ASR_UnifiedSchema` §4）。
   - Qc（已闭合）：`fetch_metadata` **注册为标准 tool**（统一 LLM 接入接口 §6.2），`InputProcessing` §4.4 已调用；高置信相悖判定可不调、低置信必调（见 `InputProcessing` §9 Q4）。
