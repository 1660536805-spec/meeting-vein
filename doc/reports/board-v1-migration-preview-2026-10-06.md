# `amo.board/v1` → v2 本地辩论样本迁移预览（2026-10-06）

> 仅预览，未写回 `.amo_data`，未批量迁移。此报告由本地两个 `sim_debate200_*` 看板快照运行 `StoreA.migration_preview` 得出。磁盘文件当前 schema 均为 `amo.board/v1`；`sim_debate200_v2` 是文件 ID，不代表其 schema 已升级。

## 结果

| 看板 | 源版本 | 节点数 | 源边数 | v2 父级变更数 | 待人工审阅节点 | 保留语义边 | 原话引用（迁移前→后） |
|---|---:|---:|---:|---:|---:|---:|---:|
| `sim_debate200_preview` | 55 | 216 | 254 | 159 | 56 | 95 | 222 → 222 |
| `sim_debate200_v2` | 61 | 215 | 223 | 144 | 70 | 79 | 220 → 220 |

## 审阅要点

- 预览将明确的 `subordinate` 结构边转为节点 `parent_id`；`support` / `oppose` 等语义边保留，不能作为树父级。
- 原话引用数量均保持不变。
- 两个预览分别将 56 / 70 个节点标为待人工审阅，主要为没有唯一明确结构父级的 evidence 节点/要点。迁移器没有静默选择语义边作为结构父级。
- 此结果不是抽取质量或 200 句指标报告。批量升级前还需完成推断规则、人工审阅入口和备份/恢复测试。

## 源样本统计

- `sim_debate200_preview`：类型计数 `{'action': 9, 'conclusion': 15, 'evidence': 71, 'issue': 6, 'point': 115}`；旧边关系计数 `{'oppose': 38, 'subordinate': 159, 'support': 57}`。
- `sim_debate200_v2`：类型计数 `{'action': 11, 'conclusion': 13, 'evidence': 75, 'issue': 7, 'point': 109}`；旧边关系计数 `{'oppose': 21, 'subordinate': 144, 'support': 58}`。
