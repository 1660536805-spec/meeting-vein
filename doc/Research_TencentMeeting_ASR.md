# 调研文档：腾讯会议语音识别（ASR）文字输出接口

> **文档状态**：调研 v1.0
> **最后更新**：2026-09-16
> **关联文档**：`doc/Design.md`（AI Conference Moderator 需求大纲 v0.3）
> **调研目标**：确认腾讯会议能否为本产品提供「实时、带说话人信息的转写文字流」，作为会议平台接入层（Q1）与 ASR 选型（第 7 章）的决策依据。

---

## 0. 结论摘要（TL;DR）

1. **可行，且有官方实时接口。** 腾讯会议开放平台提供 `meeting.asr-push` Webhook 事件，可**实时推送句子级转写文字**，payload 自带**说话人标识（userid / open_id / ms_open_id / nickname）、毫秒级发言时间戳、句子 ID（sid）、原文与翻译**。与 Design.md 的 FR-LIVE-01（实时转写区分说话人）和 FR-LIVE-04（说话人归属）直接匹配。
2. **主链路推荐**：企业自建应用（AK/SK 鉴权）+ `POST /v1/asr/push-status` 开启推送 + 接收 `meeting.asr-push` Webhook；用 `GET /v1/asr/details`（导出转写记录）做会中对账与兜底。
3. **主要约束**：
   - 需要付费版本（商业版 / 企业版 / 教育版），免费版仅「限时体验」；
   - 推送是**句子级**而非逐字流式，官方未承诺延迟数值 —— Design.md 4.5 的「ASR 流式出字 ≤ 300ms」预算大概率**不满足**，需按句子粒度（预估 1–3s）重新标定；对「上板 ≤ 10s」预算无实质影响；
   - Webhook 消息中**同企业用户才返回 userid**，外部参会人只有 `ms_open_id + nickname`，跨企业说话人归属要靠 nickname；
   - 推送只能由**会议创建者**开启，OAuth2.0 应用只能收到自己创建的会议的推送（企业级自建应用 + 企业 secret 鉴权可收企业下所有会议）。
4. **备选路线**：若脱离腾讯会议、自建音视频（Design.md Q1 候选中的自建 WebRTC），可用腾讯云 TRTC 的云端转录（`CreateCloudTranscription` + 回调），支持大模型引擎与**实时说话人分离**，新购赠送 10000 分钟。

---

## 1. 需求对齐（来自 Design.md）

| Design.md 条目 | 要求 | 腾讯会议对应能力 |
|---|---|---|
| FR-LIVE-01 [MVP] | ASR 实时转写，区分说话人 | ✅ `meeting.asr-push` webhook，speaker 字段含 userid/nickname 等 |
| FR-LIVE-04 [V1] | 说话人归属 | ✅ speaker.userid / ms_open_id + nickname（跨企业有局限，见 5.3） |
| 4.2 触发策略 | 事件触发（句子语义完整、说话人切换等） | ✅ 句子级推送天然对齐；`sid`（句子 ID）可用于去重与断句判定 |
| 4.5 时延预算 | ASR 流式出字 ≤ 300ms | ⚠️ 推送为句子级，未见官方延迟承诺，预计 1–3s，需实测 |
| 5 非功能 · 隐私 | 音频与转写默认不长期存储 | ✅ 全程不碰音频流，只接收文本推送，天然合规 |
| 5 非功能 · 合规 | 告知参会人 AI 记录 | ⚠️ 腾讯会议开启转写时会对全员提示「主持人已开启实时转写」，可作为一部分 |

---

## 2. 接口全景

腾讯会议与「语音转文字」相关的输出接口分三层：**会中实时**（Webhook 推送）、**会中/会后导出**（REST 拉取）、**录制转写**（会后批处理）。另有 TRTC 自建路线。

```
┌────────────────────────────────────────────────────────┐
│ 腾讯会议客户端（主持人开启实时转写）                       │
└──────────────┬─────────────────────────────────────────┘
               │ ① 控制类 REST API（开启转写 / 开启推送）
               ▼
┌─────────────────────────────┐     ┌──────────────────────────────┐
│ POST /v1/asr/push-status    │────▶│ Webhook: meeting.asr-push    │
│ （开启实时转写推送）          │     │ 句子级实时转写文字 + 说话人     │  ← 本产品主链路
└─────────────────────────────┘     └──────────────────────────────┘
┌─────────────────────────────┐
│ GET /v1/asr/details         │  ← 会中对账 / 兜底补齐（文件下载）
│ 导出实时转写记录              │
└─────────────────────────────┘
┌─────────────────────────────┐
│ /v1/records/transcripts/*   │  ← 会后：云录制转写段落/详情（批处理）
└─────────────────────────────┘
```

### 2.1 前提条件（重要）

| 条件 | 说明 |
|---|---|
| **会议账号版本** | 商业版 / 教育版 / 企业版 自动开通企业 API 能力（免费版实时转写仅「限时体验」，API 能力对接为企业版权益项）。购买后管理员在「用户中心 → 高级」创建密钥对，获取 `SecretId` / `SecretKey` / `APPID` / `SDKID` |
| **应用形态** | 企业自建应用（推荐）或三方应用（OAuth2.0）。自建应用分**企业级**（可获取企业下所有数据，含 App/各途径创建的会议）与**应用级**（仅本应用创建的会议） |
| **权限点** | 转写相关接口需要「管理企业会议转写」（自建应用 AK/SK）或「管理会议转写 / 查看会议转写」（OAuth2.0）权限点 |
| **开发者角色** | 管理员在「企业管理 → 用户管理」中将成员角色设为「应用开发者」 |

---

## 3. 主链路：实时转写推送（推荐方案）

### 3.1 Step 1 — 开启会中实时转写

**接口**：`PUT https://api.meeting.qq.com/v1/real-control/meetings/{meeting_id}/asr`

- 鉴权：AK/SK 或 OAuth2.0（需「管理会议转写」权限点）
- 要求会议处于**进行中**状态；**操作者必须是主持人角色**
- 关键入参：`operator_id` / `operator_id_type`（操作者身份）、`instance_id`（终端类型）、`is_open`（开关）、`open_asr_view`（是否自动打开转写侧边栏）

> 也可以由主持人在客户端手动开启（会中「应用 → 实时转写」）。API 开关的价值在于：产品可以自动为每场接入的会议开转写，不依赖主持人操作。

### 3.2 Step 2 — 开启实时转写推送

**接口**：`POST https://api.meeting.qq.com/v1/asr/push-status`

```json
{
    "meeting_id": "xxxxxxx",
    "operator_id_type": 1,
    "operator_id": "xxxxxxx",
    "is_open": true
}
```

关键规则：
- 仅**会议创建者**可开启本场会议的推送；
- **企业 secret 鉴权**（AK/SK）：可开启该用户所属企业下所有会议的推送；企业级自建应用通过 webhook 可收到**企业下所有开启推送的会议**；
- **OAuth2.0 鉴权**：只能开启/收到**通过 OAuth2.0 创建的会议**的推送；
- 待开始的会议、未打开转写功能的会议也支持先开启推送（转写打开后即开始推）；
- Rooms 会议室场景需额外传 `target_rooms_id`。

### 3.3 Step 3 — 接收 Webhook 事件 `meeting.asr-push`

**事件名**：`meeting.asr-push`。开启推送后，转写内容以**句子级**实时推送到应用配置的 Webhook URL（需配置事件订阅，并按官方要求做签名验证）。

**Payload 完整结构**（官方文档示例）：

```json
{
    "event": "meeting.asr-push",
    "trace_id": "xxxxxxxxxxxxx",
    "payload": [{
        "asr_content": [{
            "speaker": {
                "userid": "xxxxx",        // 同企业用户才返回
                "open_id": "xxxxxx",      // OAuth 用户返回
                "ms_open_id": "xxxxxxx",  // 会中临时身份，每场会议唯一
                "nickname": "xxxx"        // 发言人会中名称
            },
            "speech_time": "xxxxx",       // 发言时间，毫秒时间戳
            "content": {
                "text": "",               // 原文内容
                "language": "xxx",        // 原文语言
                "translate": [{           // 翻译内容（开启翻译时）
                    "text": "xxxx",
                    "language": "xxx"
                }]
            },
            "sid": "xxxxx"                // 句子 ID
        }],
        "meeting_info": {
            "meeting_id": "…", "meeting_code": "…", "subject": "…",
            "creator": { "userid": "…", "ms_open_id": "…", "user_name": "…", "instance_id": "2" },
            "meeting_type": 0,            // 0一次性 1周期性 2微信专属 4Rooms投屏 5个人会议号
            "meeting_id_type": 0,         // 0主会议 1分组会议
            "sub_meeting_id": "…",        // 周期性会议子会议 ID
            "start_time": 1608522626, "end_time": 1609415039
        }
    }]
}
```

**对本产品关键的字段解读**：

| 字段 | 对看板引擎的价值 |
|---|---|
| `speaker.ms_open_id` | 会中唯一、跨终端稳定，**推荐作为说话人主键**（userid 对企业外用户不返回） |
| `speaker.nickname` | 直接可用于「要点由谁提出」的展示（FR-LIVE-04） |
| `speech_time`（毫秒） | 撑起 Design.md 4.2 的触发策略（说话人切换、静默阈值判定）与可观测性 |
| `sid`（句子 ID） | 句子级去重、断句判定；句子 = 要点抽取的最小输入单元 |
| `content.text` | 送入 LLM 抽取/分类/匹配链路的正文 |
| `content.translate` | 多语言会议（Q9）的现成翻译通道，MVP 可忽略 |
| `meeting_info` | 分组会议需按 `sub_meeting_id` 区分看板归属 |

### 3.4 Step 4（辅助）— 导出转写记录（对账 / 兜底）

**接口**：`GET https://api.meeting.qq.com/v1/asr/details`

- 会议开启实时转写后，**会中与会后**均可调用，分页导出；
- 输出为**文件下载链接**（`download_url`，txt/word/pdf，有效期 2 小时），或含双语内容；
- 权限点：「查看或管理实时转写」。

> 用途：Webhook 推送可能因网络抖动丢失消息。会中可周期性拉取/解析该记录做**对账补齐**；会后用于生成最终纪要与转写归档（FR-POST-02）。注意返回的是文件链接而非结构化 JSON，解析 txt 后需要自己做分段。

### 3.5 辅助 — 热词设置

**接口**：设置语音识别热词（自定义字幕/转写/智能录制热词），可提升产品领域术语（如看板引擎相关词表、公司/项目名词）的识别准确率。与 TRTC 的热词机制类似（`term|weight` 格式，权重 1–11 或 100）。

---

## 4. 会后路线：录制转写（批处理）

| 接口 | 说明 |
|---|---|
| `GET /v1/records/transcripts/paragraphs` | 获取云录制转写的**段落列表**（段落总数、段落 ID、起止毫秒时间）；返回 `audio_detect` 声纹识别状态 —— 针对 **Rooms 设备一台设备多人讲话**的场景，可自动区分多名发言人（单设备最多 12 人） |
| 录制转写详情 / 段落文本 | 按段落 ID 获取转写文本与说话人信息 |
| 查询单个录制详情（文件、纪要） | 获取 AI 纪要 |

> 价值：MVP 阶段如果会中实时链路风险大，可先做「会后看板」——用录制转写做批处理回放生成结构图；但产品核心卖点是实时性，此路线仅作降级/开发调试用。

---

## 5. 约束、风险与待实测项

### 5.1 版本与商务约束

- 实时转写/字幕/录制转写在**免费版仅限时体验**；API 能力对接为企业版权益。落地需采购**商业版及以上**（商业版支持 300 人、企业版 500 人，Design.md 要求单场 ≥ 100 人，商业版即满足）。
- 三方应用（OAuth2.0）接入目前处于预约/内测式管理；**企业自建应用 + AK/SK 是最顺的路径**，但要求会议在本企业账号体系内创建。

### 5.2 实时性与粒度

- **推送粒度是句子级**，官方文档未承诺延迟数值（无 P95/出字延迟指标）。参考同类实现，预估句子完成后 1–3 秒内推送，**无法满足 Design.md 4.5「流式出字 ≤ 300ms」的字面预算**，但「要点上板 P95 ≤ 10s」预算依然充裕。→ 建议 M1 实测推送延迟，并在 Design.md 中把 300ms 预算改写为「句子级转写到达延迟 ≤ 3s（待实测标定）」。
- 无逐字（word-by-word）流式接口开放给第三方；若未来确实需要逐字流，只能走 TRTC 自建路线。

### 5.3 说话人识别边界

- 同企业用户返回 `userid`；**企业外部参会人（微信入会、跨企业）只有 `ms_open_id` + `nickname`**。跨企业会议的说话人长期归属（跨会议追踪，FR-POST-05）无法靠 userid 打通，需以企业通讯录映射 nickname 或人工确认。
- `ms_open_id` 是**每场会议唯一**的临时 ID，跨会议不稳定 —— 设计数据模型时需要「会内 ms_open_id → 企业 userid/nickname」的会级映射表。
- Rooms 设备多人共用一台终端的场景：录制转写侧有声纹分离能力（单设备最多 12 人），实时推送侧的说话人粒度需实测。

### 5.4 其他风险

| 风险 | 影响 | 应对 |
|---|---|---|
| Webhook 推送丢失/乱序 | 看板漏要点 | 用 `sid` 去重排序；`/v1/asr/details` 周期对账补齐 |
| 主持人未开转写 | 无推送 | Step 1 API 自动开启转写（需主持人权限的 operator）；会前创建会议时检查配置 |
| OAuth 应用只能收自己创建的会议 | 接入范围受限 | 默认采用企业级自建应用 |
| 分组会议 | 多个分会场转写混流 | 按 `meeting_id_type`/`sub_meeting_id` 路由到不同看板 |
| 转写语言限制 | 方言不支持；语言覆盖以官方为准 | Q9 明确多语言需求后再定；有 `translate` 兜底 |

---

## 6. 备选路线：TRTC 自建链路（脱离腾讯会议客户端）

若 Q1 最终选择「自建 WebRTC」或需要深度定制，腾讯云 TRTC 提供两条 ASR 输出路径：

1. **服务端云端转录**：REST API `CreateCloudTranscription`（`trtc.tencentcloudapi.com`）发起任务， transcription 机器人以虚拟观众进房订阅音频流，识别结果**通过回调推送**到业务后台。`AsrParam.Lang` 支持 `bigmodel-zh` 等大模型引擎（支持中英混、方言、30+ 语种），支持 `VadSilenceTime`（VAD 断句 240–2000ms）、热词（`HotWordList`）、实时翻译。
2. **客户端 SDK**：TRTC SDK 的 `AITranscriberManager` 接口，客户端直接监听 `onReceiveTranscriberMessage` 获取实时转写消息。
3. **说话人分离**：TRTC 每个 user 独立音频流（订阅粒度天然分说话人）；录音文件识别的 bigmodel 引擎亦支持**实时说话人分离**。
4. **计费**：语音转文本后付费，新开通赠 10000 分钟。

**对比结论**：TRTC 路线粒度更细（可配 VAD）、无企业版商务门槛、延迟可控，但要自己搭会议（音视频、入会体验、Rooms 硬件全自建）。**对本产品而言，腾讯会议 OpenAPI 路线开发量小一个数量级，优先采用**；TRTC 路线作为「自建平台」选项的配套 ASR 方案记录在案。

另有 **TMSDK（腾讯会议 SDK）**：面向生态合作伙伴的客户端 SDK（Mac/Windows/iOS/Android/QT/Electron），可将腾讯会议嵌入自有应用，部分会前功能走 REST API。SDK 需通过腾讯会议售后/商务获取，适合「把会议能力嵌进自家产品」的场景，本产品当前无需。

---

## 7. 建议落地顺序（对齐 Design.md 里程碑）

| 阶段 | 动作 |
|---|---|
| M0 决策 | 确认采购商业版/企业版账号；创建企业级自建应用，申请「管理企业会议转写」权限点，配置 Webhook 订阅 |
| M1 打通 | 「创建会议（API）→ 自动开转写+开推送 → 接收 asr-push → sid 去重落库 → 直出文字列表页」。实测：句子推送延迟分布、外部参会人字段缺失影响、Webhook 丢失率 |
| M1 验证 | 用真实转写流喂看板引擎原型（要点抽取 → 上板），验证上板 ≤ 10s |
| 后续 | `/v1/asr/details` 对账补齐；热词注入；录制转写用于会后纪要对齐 |

---

## 8. 参考链接

| 内容 | 链接 |
|---|---|
| REST API 概览（语音转写接口清单与权限点） | https://cloud.tencent.com/document/product/1095/113415 |
| 开启或关闭实时转写 | https://cloud.tencent.com/document/product/597/106034 |
| 开启或关闭实时转写推送 | https://cloud.tencent.com/document/api/1759/107522 |
| Webhook 实时转写推送（payload 结构） | https://cloud.tencent.com/document/product/862/107523 |
| 导出实时转写记录 | https://cloud.tencent.com/document/api/266/94442 |
| 企业自建应用接入指引 | https://cloud.tencent.com/document/api/460/83669 |
| 基本概念（userid / ms_open_id 等） | https://cloud.tencent.com/document/product/1095/79796 |
| 查询录制转写段落信息 | https://meeting.tencent.com/support/topic/798/index.html |
| 企业版权益对比 | https://meeting.tencent.com/business-service-enterprise |
| TRTC AI 转录与翻译（服务端） | https://trtc.io/zh/document/79873 |
| TRTC CreateCloudTranscription | https://cloud.tencent.com/document/product/647/129058 |
| 腾讯会议 SDK（TMSDK） | https://github.com/Tencent-Meeting/TencentMeetingSDK |
