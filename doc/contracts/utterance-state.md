# 发言摄取与处理状态契约

> 状态：摄取主路径已实现；`committed` 表示处理流程完成，不保证发言已出现在看板。

## 事件身份和状态

以 `(meeting_id, utterance_id)` 唯一标识最终发言；重复提交必须返回同一事件的当前状态，不能重复增加证据或节点。

```text
accepted → processing → committed
                     ↘ failed → processing (显式重试)
```

- `accepted`：Store B 已安全接收原话；不代表分析完成。
- `processing`：事件正在编排、写入看板。
- `committed`：本次编排已完成并持久化。响应包含 `meta_id`、`board_version` 和 `board_effect`；`linked` 表示看板节点关联了这条原话，`unlinked` 表示流程完成但没有关联原话，页面提示核对并补录。
- `failed`：事件仍保留原话和错误摘要，可重试；不得把失败静默呈现成已整理。

## API 目标

```http
POST /api/utterances
Content-Type: application/json
```

```json
{"utterance_id":"seg-001","meeting_id":"mtg_demo","session_id":"s1","seq":1,
 "speaker":{"speaker_ref":"local:user","display_name":"发言人"},
 "text":"我们需要确认交付日期。","language":"zh-CN","start_offset_ms":0,
 "end_offset_ms":2200,"received_at_ms":1791280000000,"is_final":true,
 "is_partial":false,"source":"local_streaming"}
```

```json
{"ok":true,"utterance_id":"seg-001","state":"committed",
 "meta_id":"utt_evt_…","board_version":12,"board_effect":"linked"}
```

状态读取：`GET /api/utterances/{utterance_id}/status?meeting_id={meeting_id}`。不存在为 404；会议与事件身份不匹配为 404；字段校验失败为 422；暂时不可处理可返回 `failed` 状态及可读 `error`，不伪报成功。状态变化通过 WebSocket 广播。重启时扫描 `pending/processing` 事件并按幂等规则恢复；持久字段包括 `processing_state`、`attempts`、`last_error`、`updated_at`。

## ASR 规则

静音切分后的 final segment 是正常落图单位，partial 只显示、不入库。每个 segment 的 ID、序号和起止时间在重试/重连后稳定。浏览器待发送队列持久化，服务端确认处理完成后才移除；停止录音时报告未确认数，并允许重试或导出文本。没有实时分段时，整段 SenseVoice 转写作为兜底输入；已有实时分段时，整段转写只作核对，不自动重复入板。

## 实现备注

Store B 以兼容旧 `pending/done` 值的方式持久化摄取状态；接口映射为 `accepted/processing/committed/failed`。`board_effect` 通过看板节点的 `metadata_refs` 判断，旧记录也按当前看板计算。启动恢复会重放不属于 pending_batch 补偿批次的 pending/processing/failed 单句；批次补偿成功/失败也会更新单句状态。浏览器按会议把 final 事件写入 localStorage outbox，重试先查状态，停止后提供待发条数与文本导出。`linked` 只证明原话被某个节点引用，不证明抽取语义完全正确；用户仍应核对转写和节点内容。
