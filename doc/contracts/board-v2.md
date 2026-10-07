# amo.board/v2 契约（目标契约）

> 状态：实施目标；当前代码仍写 `amo.board/v1`。本文件不表示迁移已完成。

## 结构

```json
{
  "schema": "amo.board/v2",
  "graph_id": "mtg_demo",
  "version": 12,
  "cells": [
    {"id":"issue_root","shape":"amo-node","data":{"type":"issue","label":"议题","parent_id":null,"metadata_refs":[]}},
    {"id":"point_1","shape":"amo-node","data":{"type":"point","label":"观点","parent_id":"issue_root","metadata_refs":["utt_evt_…"]}},
    {"id":"edge_1","shape":"edge","source":{"cell":"point_1"},"target":{"cell":"point_2"},"data":{"relation":"oppose"}}
  ]
}
```

- 每个节点 `data.parent_id` 是唯一结构归属；根节点为 `null`。子级布局、议题树和面包屑只读取该字段。
- `data.relation` 仅用于语义关系边：`support | oppose | duplicate | replace | derive`。`subordinate` 是 v1 的旧格式，v2 迁移后不再承担父子语义。
- `metadata_refs` 保存 Store B 的 `meta_id` 引用，v2 迁移不得删除或重写这些引用。
- 父级必须指向同一看板中的节点；禁止自挂和环。无法唯一推断的节点进入“待归属”人工审阅组。

## v1 兼容与写入

读 v1 时先保留原始快照，再生成迁移预览。优先使用明确的 `subordinate` 父子边；缺失时只根据可验证的旧层级信息推断。多父、环或无法判断的关系不得静默选边：保留语义边和引用，把节点标为待审阅。迁移接受后写 v2；回滚以原快照恢复。历史和只读分享快照必须继续按其原 schema 读取。

迁移预览至少返回源版本、目标版本、父级变更列表、待审阅节点、保留的语义边数量及原话引用数量。未对 `backend/tests/辩论赛200句.txt` 完成差异报告前，不批量升级已有数据。

## 更新规则

人工编辑和模型建议共用父级校验。添加、移动、合并或删除节点时，明确处理其子节点、关系边和 `metadata_refs`；合并不能丢失这些关联。v2 的 `version` 单调递增，变更前的完整版本保留在历史中。
