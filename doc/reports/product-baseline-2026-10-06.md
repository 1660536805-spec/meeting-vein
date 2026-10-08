# 会脉产品基线（2026-10-06）

> 本文固定的是实施前的代码基线。后续落地进度以产品完整化计划任务状态和新增迁移报告为准。

## 工作树与范围

- 仓库目录：`ai_meeting_organizer-fusion`；此次核对时分支为 `main`，未发现 `AGENTS.md`。
- 工作树原有两份未跟踪文件：`docs/superpowers/plans/2026-10-06-modelscope-studio-deployment.md` 和 `docs/superpowers/specs/2026-10-06-modelscope-studio-deployment-design.md`。本次未修改或清理它们。
- 产品目标和优先级以 `doc/Design.md`、`doc/Product_Optimization_Plan_2026-10-06.md` 为准；本报告是代码现状快照，不宣称目标已完成。

## 已有能力（代码证据）

| 能力 | 当前证据 | 边界 |
| --- | --- | --- |
| 本地 ASR 和最终分段 | `local_asr/backend/app/main.py`、`frontend/src/asr/live-stream.ts`、`frontend/src/asr/panel.ts` | 有流式临时字幕和静音切分最终分段；真实麦克风链路与时延仍需实际验收。 |
| 手动与 ASR 发言摄取 | `backend/app/utterance_ingest.py`、`backend/app/server.py` | 按会议和发言 ID 幂等；当前响应没有统一的 accepted/processing/committed/failed 状态。 |
| 双 Agent / Mock 与真实 LLM | `backend/app/llm.py`、`backend/app/config.py` | 未启用时使用 MockLLM；配置存在不能证明真实客户端成功运行。 |
| Store A / Store B | `backend/app/storage.py` | 看板与原话分开持久化，可由 `metadata_refs` 反查；看板当前序列化为 v1。 |
| 历史与只读快照 | `backend/app/storage.py`、`backend/app/server.py`、`frontend/src/view.ts` | 快照 token 通过同一服务读取且页面只读；没有身份、TTL 或撤销能力，不能承诺对外/跨设备分享。 |
| 录音与手动输入 | `frontend/src/asr/panel.ts`、`frontend/src/main.ts` | 当前前端 POST 成功后显示“已送达”，不表示服务端已确认看板持久化；失败队列未达到目标持久恢复契约。 |

## 尚未达到的目标契约

1. 应用主读写路径尚未切换到 `amo.board/v2` 的显式 `parent_id`；当前 `backend/app/models.py` 和运行中 Store A 默认仍为 `amo.board/v1`。迁移转换、Store A 预览、版本校验的备份/接受/回滚原语已在本计划实施中增加，但尚未接入 API 或前端，也未对现有会议执行。
2. 摄取没有统一状态查询、每事件 attempts/error 时间戳和重启扫描恢复；目标见 `doc/contracts/utterance-state.md`。
3. 单机 M1 的空会创建、议程确认、字段级人工保护/版本冲突、结束会议与纪要导出尚未形成已验收闭环。
4. M2 的身份、访问控制、可撤销只读 token、备份恢复和腾讯会议接入均未交付。
5. 当前未有经复核的 200 句黄金标注与实际质量报告；PRD 数值保留为目标，不能作为当前性能或准确率声明。

## 演示样本

`backend/tests/辩论赛200句.txt` 固定为 200 行输入样本。配套人工标注位于 `backend/tests/annotations/debate-200-gold.csv`，包括正反方立场、核心冲突、重复提及、结论候选、主持人总结和待办锚点。`backend/tests/annotations/empty-meeting.md` 定义零原话、零节点的空会基准。迁移预览结果见 `doc/reports/board-v1-migration-preview-2026-10-06.md`。此标注是首轮回归集；统计前仍需按契约中的计数规则复核，不把基准标签数量当作准确率结果。
