# ASR 输出统一数据格式（Agent 输入层设计）

> **文档状态**：设计 v0.1
> **最后更新**：2026-09-16
> **关联文档**：
> - `doc/Design.md`（AI Conference Moderator 需求大纲 v0.3）
> - `doc/Research_TencentMeeting_ASR.md`（主链路：腾讯会议 asr-push 调研）
> - `doc/Research_FunASR_ASR.md`（备选：FunASR 本地部署调研）
> **设计目标**：无论后端 ASR 来自腾讯会议 API 还是本地 FunASR，**Agent（看板引擎 / 要点抽取 / 说话人归属）只消费一种中间格式**，后端差异由适配层吸收。

---

## 0. 结论（TL;DR）

1. **两种方案的原始输出接口差异很大**——传输协议、字段命名、说话人标识语义、时间基准全部不同（详见 §1），但两者携带的信息都能无损收敛到同一个核心四元组：**说话人 + 时间段 + 文本 + 句子单元**。
2. **统一方案**：定义中间格式 `NormUtterance`（§2），两套后端各写一个 **Adapter**（§3）把原始 payload 转成 `NormUtterance`；Agent 只认 `NormUtterance`，不感知来源（字段 `source` 仅用于可观测与兜底）。
3. **说话人标识是最大的语义鸿沟**（账号体系 vs 匿名编号），单独用 `SpeakerResolver`（§4）归一化到统一的 `speaker_ref`，并标记 `is_resolved` 表示是否已映射到真人。
4. 时间统一为**相对会议起点（音频起点）的毫秒偏移** `start_offset_ms / end_offset_ms`；同时保留 `received_at_ms`（Agent 收到时的墙上时钟）以满足 Design.md 4.5 的「上板 ≤ 10s」延迟预算核算。

---

## 1. 原始输出字段差异对比

| 维度 | 腾讯会议 `meeting.asr-push` | FunASR（`sentence_info` / WS 2pass） | 差异性质 |
|---|---|---|---|
| 传输协议 | HTTPS Webhook（`payload[].asr_content[]` 数组） | WebSocket 2pass（`{mode, text, spk, timestamp}`）或 OpenAI 兼容 HTTP | **协议不同** |
| 句子文本 | `content.text` | `sentence`（离线）/ `text`（WS） | 字段名不同 |
| 句子 ID | `sid` | 无显式 ID（靠 `timestamp`/`start` 推断） | 缺字段 |
| 说话人 | `speaker.userid` / `open_id` / `ms_open_id` / `nickname` | `spk`（0/1/2 匿名编号） | **语义不同**（账号 vs 聚类） |
| 时间 | `speech_time`（毫秒，绝对或会议时间，待实测） | `start` / `end`（相对音频起点毫秒） | **基准不同** |
| 语言 | `content.language` | 模型隐含（Paraformer-zh 等） | 需 Adapter 补 |
| 翻译 | `content.translate[]` | 无（需另接翻译） | 缺字段 |
| 会议上下文 | `meeting_info`（meeting_id/code/subject/type…） | 无（调用方自带入参） | 缺字段 |
| 置信度 | 无 | 无（Paraformer 不输出置信度） | 均缺 |

**结论**：字段层不互通，但每个有用字段都能在 `NormUtterance` 找到对应槽位（见 §2 映射列）。

---

## 2. 统一中间格式 `NormUtterance`

Agent 入口消费的最小单元是「一句话」（句子级），与两种后端的最小推送单元一致。

### 2.1 单句对象（核心）

```json
{
  "utterance_id": "u_8f3a2c...",        // 全局唯一 UUID（Adapter 生成，替代 sid / 序号）
  "meeting_id": "meeting_abc123",       // 会议级主键（来自 meeting_info 或调用上下文）
  "session_id": null,                   // 分组会议 / 子会议 ID（腾讯 sub_meeting_id；FunASR 同场为 null）
  "seq": 17,                            // 同会议内单调递增序号（Adapter 按到达顺序分配，用于乱序检测）

  "speaker": {
    "speaker_ref": "spk_7d2f",          // ★ 统一说话人主键（归一化后，见 §4）
    "source_id": "ms_open_id:xxxx",     // 后端原始说话人标识（腾讯 ms_open_id / FunASR "0"）
    "display_name": "张三",              // 展示名（nickname / 声纹库命中名 / 匿名"说话人2"）
    "is_resolved": true                 // 是否已映射到企业真人（false=匿名聚类）
  },

  "text": "这个项目下周要出第一版原型。",
  "language": "zh",

  "start_offset_ms": 123450,           // ★ 相对会议/音频起点的毫秒偏移
  "end_offset_ms": 124560,
  "received_at_ms": 1726483200123,     // ★ Agent 收到时的墙上时钟（毫秒，用于上板延迟核算）

  "is_final": true,                    // 句子级最终结果（false=流式草稿 partial）
  "is_partial": false,                 // 草稿标记（2pass 的 online 段）

  "translation": [                     // 可选，仅腾讯开启翻译时有
    { "text": "We need v1 prototype next week.", "language": "en" }
  ],

  "source": "tencent_meeting",         // "tencent_meeting" | "funasr"（可观测/兜底用）
  "confidence": null,                  // 两后端当前均不提供，预留
  "raw_ref": "asr_push:trace_xxx:sid_yyy"  // 指向原始 payload 的溯源键（调试/对账用）
  "mouse_action": null                  // 可选：鼠标交互窗口（见 doc/Design_CursorCapture.md §7）；由光标流按语音段 ±1s 拼入，字段格式见该文档 §7.1
}
```

> **`mouse_action`（可选，跨流融合字段）**：默认 `null`。当启用鼠标信号采集（`Design_CursorCapture.md`）时，由 `filter_cursor_node` 输出在 utterance 语音段 `[start_offset_ms-1000, end_offset_ms+1000]` 窗口内的 `filtered_focus`，于 `assemble` 拼装阶段填入本字段，作为该句 LLM 上下文的附加上下文（列表，元素为 `{ts, gesture, node, [from/to]}`）。Agent 消费契约不变：仍只解析 `utterances[]`、按 `utterance_id` 去重、按 `seq` 排序、按 `is_final` 触发；`mouse_action` 仅作提示词注记，不参与去重/触发。

### 2.2 批量信封（实时推送封装）

实时流以信封方式投递，支持一次多句（腾讯 `asr_content` 数组、FunASR 2pass 修正段均可能多句）：

```json
{
  "event": "transcript.utterance",
  "version": "1.0",
  "meeting_id": "meeting_abc123",
  "emitted_at_ms": 1726483200100,
  "utterances": [ /* NormUtterance ... */ ]
}
```

> Agent 消费契约：**只解析 `utterances[]`，按 `utterance_id` 去重、按 `seq` 排序、按 `is_final` 决定是否触发下游（要点抽取 / 上板）**。`event`/`version`/`emitted_at_ms` 仅用于管道元信息。

---

## 3. 适配层（Adapter）设计

每个后端一个 Adapter，职责：解析原始 payload → 填 `NormUtterance` → 调 `SpeakerResolver` 归一化 `speaker_ref` → 发往统一总线。

```
            ┌─────────────────────┐         ┌─────────────────────┐
 腾讯会议 ──▶│ TencentASRAdapter  │         │   FunASRAdapter     │◀── FunASR
 Webhook     │ (HTTPS → NormUtt)  │         │ (WS/HTTP → NormUtt) │    WS/HTTP
            └─────────┬───────────┘         └──────────┬──────────┘
                      │                                 │
                      └──────────┬──────────────────────┘
                                 ▼
                        ┌──────────────────┐
                        │ SpeakerResolver  │  ← 把 source_id 归一化为 speaker_ref
                        └─────────┬────────┘
                                 ▼
                   统一总线：transcript.utterance (NormUtterance[])
                                 ▼
                        ┌──────────────────┐
                        │   Agent 看板引擎  │  ← 只认 NormUtterance，不感知来源
                        └──────────────────┘
```

### 3.1 字段映射表

| NormUtterance 字段 | 腾讯会议来源 | FunASR 来源 |
|---|---|---|
| `utterance_id` | Adapter 生成 UUID（绑定 `sid`） | Adapter 生成 UUID（绑定 `start`+文本哈希） |
| `meeting_id` | `meeting_info.meeting_id` | 调用上下文注入（创建 WS/HTTP 会话时绑定） |
| `session_id` | `meeting_info.sub_meeting_id`（无则 null） | null |
| `seq` | Adapter 按到达顺序递增 | Adapter 按到达顺序递增 |
| `speaker.source_id` | `speaker.ms_open_id`（优先）/ `userid` | `str(spk)` |
| `speaker.display_name` | `speaker.nickname` | `"说话人" + spk`（声纹库命中后覆盖） |
| `speaker.is_resolved` | `userid` 存在 → true；否则 false | 默认 false（声纹库命中 → true） |
| `text` | `content.text` | `sentence` / `text` |
| `language` | `content.language` | Adapter 补（模型默认语言，如 `zh`） |
| `start_offset_ms` | `speech_time` → 需减会议起点换算（**基准待实测确认**） | `start` |
| `end_offset_ms` | `speech_time` + 估算句长（腾讯只给单点，待实测是否含句末） | `end` |
| `received_at_ms` | Adapter 收到 Webhook 时刻 | Adapter 收到 WS 消息时刻 |
| `is_final` | 默认 true（句子级） | 2pass offline 段 true；online 段 false |
| `is_partial` | false | online 段 true |
| `translation` | `content.translate` | null |
| `source` | `"tencent_meeting"` | `"funasr"` |
| `raw_ref` | `trace_id:sid` | WS mode + 句索引 |

> **待实测关键项**：腾讯 `speech_time` 究竟是「句首绝对时间」还是「句末」、是否含句末偏移——决定 `start_offset_ms`/`end_offset_ms` 的换算公式（§6）。

---

## 4. 说话人归一化（SpeakerResolver）

这是两方案**最大的语义鸿沟**，单独成层：

- **腾讯路径**：`speaker_ref = ms_open_id`（会内稳定、跨终端一致）。若 `userid` 存在（同企业），`is_resolved=true` 并可进一步映射到企业通讯录 `userid`；外部参会人只有 `ms_open_id + nickname`，`is_resolved=false`，靠 `nickname` 展示。
- **FunASR 路径**：`spk` 是每场重新聚类的匿名编号，`speaker_ref = "spk_{n}"`，默认 `is_resolved=false`。要做跨会议/跨真人绑定，需**声纹注册库**（会前朗读注册句 / 已知片段匹配）把 `spk` 映射到企业 `userid`/`nickname`。

统一 `speaker_ref` 规则建议：

```
speaker_ref =
  if source == tencent_meeting and userid present:  "ent:" + userid        // 企业内真人，最稳
  if source == tencent_meeting:                     "ms:"  + ms_open_id    // 会内临时，跨会不稳
  if source == funasr and enrolled:                 "ent:" + enrolled_id   // 声纹库命中
  else:                                             "spk:" + spk           // 匿名，待解析
```

> Agent 在使用 `speaker_ref` 做「要点由谁提出」「跨会议追踪（FR-POST-05）」时，**必须先查 `is_resolved`**：匿名 `spk:` 前缀不参与跨会议聚合，仅作当次会议内展示。

---

## 5. Agent 消费约定（对齐 Design.md）

| Design.md 条目 | 统一格式如何支撑 |
|---|---|
| FR-LIVE-01 [MVP] 实时转写区分说话人 | `NormUtterance.speaker.display_name` + `speaker_ref` |
| FR-LIVE-04 [V1] 说话人归属 | `speaker_ref` + `is_resolved`；跨会议靠 `ent:` 前缀 |
| 4.2 触发策略（句子完整/说话人切换） | 以「单条 `is_final=true` 的 `NormUtterance`」为最小触发单元；`speaker_ref` 变化 = 说话人切换；`end_offset_ms` 与上条间隔 = 静默阈值 |
| 4.5 时延预算（上板 ≤ 10s） | `received_at_ms − start_offset_ms − meeting_start_epoch` 核算端到端延迟；`is_final` 才计入 |
| 5 非功能·隐私 | FunASR 路径数据不出域；腾讯路径只过文本；`raw_ref` 仅存溯源键，不落原始音频 |
| 去重/乱序 | `utterance_id` 去重；`seq` 排序；Webhook 丢包用腾讯 `/v1/asr/details` 或 FunASR 离线重跑补齐 |

---

## 6. 待实测 / 开放问题

1. **腾讯 `speech_time` 基准**：是绝对 unix 毫秒还是会议相对毫秒？句首还是句末？是否附带句末偏移？——决定 §3.1 时间换算实现，M1 打通时抓一条真实 payload 确认。
2. **FunASR 流式 `spk` 稳定性**：长会（>1h）聚类漂移是否导致同一人多个 `spk` 编号，需在 Resolver 加跨段对齐。
3. **置信度缺失**：两后端均不输出置信度，要点抽取如需「低置信跳过」需另接或自估（如翻译回译一致性）。
4. **部分终态语义**：FunASR 2pass 的 `is_partial` 段是否会在 `is_final` 后撤回/覆盖——Agent 需以 `is_final` 为准、忽略此前 partial 的同句。

---

## 7. 落地建议（对齐里程碑）

| 阶段 | 动作 |
|---|---|
| M0 决策 | 定稿 `NormUtterance` schema（本协议）；确定 `speaker_ref` 前缀规则 |
| M1 打通 | 先实现 `TencentASRAdapter` + `SpeakerResolver`（ms_open_id 路径）；用真实 asr-push 验证 §6.1 时间基准 |
| M1+ | 实现 `FunASRAdapter`（WS 2pass）；Resolver 加声纹库分支（若走自建 WebRTC） |
| 持续 | Agent 全链路只依赖 `NormUtterance`；新增后端只需加一个 Adapter，不改 Agent |
