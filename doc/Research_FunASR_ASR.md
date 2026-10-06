# 调研文档：FunASR 本地部署语音识别方案（备选）

> **文档状态**：调研 v1.0
> **最后更新**：2026-09-16
> **关联文档**：
> - `doc/Design.md`（AI Conference Moderator 需求大纲 v0.3）
> - `doc/Research_TencentMeeting_ASR.md`（主链路：腾讯会议 OpenAPI 调研）
> **定位**：作为腾讯会议转写 API 的**备选/本地化方案**。FunASR 提供 ASR + 说话人分离的全链路本地推理，但**不包含会议平台与音频采集**。
> **调研目标**：确认 FunASR 能否以「本地部署、数据不出域」方式，为本产品提供实时、带说话人信息的转写文字流，并厘清它与腾讯会议 API 方案的能力边界。

---

## 0. 结论摘要（TL;DR）

1. **FunASR 是阿里达摩院开源的工业级语音工具链**，覆盖 ASR、VAD、标点、说话人分离、情感、音频事件，统一 `AutoModel` 接口，Apache-2.0 协议，**可私有化部署、数据不出本地**（天然满足 Design.md 5 的隐私要求）。
2. **说话人分离能力强且开箱即用**：以 `cam++`（7.2M 说话人嵌入）做声纹特征 + 聚类，一次 `generate()` 即返回每句的 `spk`（说话人编号）、`start/end`（毫秒）、`sentence`（文本）。实测一段 227s 多人会议录音自动切 77 句、识别 11 个说话人，**无需预设人数**。流式实时服务也支持 `--enable_spk` 增量标注。直接满足 FR-LIVE-01 / FR-LIVE-04。
3. **接口形态匹配多场景**：
   - 离线文件转写：`funasr-server` 暴露 **OpenAI 兼容** `/v1/audio/transcriptions`（一行 curl 调用，任意 OpenAI 客户端零改动接入）；
   - 实时流式：**WebSocket** 服务（`funasr_wss_server.py`，端口 10095，`2pass` 模式先出流式草稿、再离线修正），以及 OpenAI realtime WebSocket 服务；
   - 应用内嵌：`from funasr import AutoModel` Python API 或 ONNX/C++、GGUF 量化边缘部署。
4. **最大缺口（务必注意）**：**FunASR 只做语音识别，不提供会议平台，也不开放音频流接入**。若仍以腾讯会议为会议平台，其 API **仅开放转写文字（asr-push）而不开放原始音频**——此时用 FunASR 就必须**自建音视频链路或旁路采集音频**，等于把接入层整体换成「自建 WebRTC + FunASR」。换句话说，FunASR 是腾讯会议 asr-push 的**替代组件**，不是「腾讯会议 API + 自部署 ASR」的简单拼接。
5. **其他权衡**：说话人 ID 是聚类出来的**匿名编号**（speaker 0/1/2…），跨会议身份追踪需自建声纹库/注册映射；中文方言口音（7 方言 26 口音）优于腾讯会议字幕（仅中英）；但需自备 GPU/CPU 算力与运维（腾讯会议 API 按量付费免运维）。

---

## 1. 需求对齐（来自 Design.md）

| Design.md 条目 | 要求 | FunASR 对应能力 |
|---|---|---|
| FR-LIVE-01 [MVP] | ASR 实时转写，区分说话人 | ✅ 流式 WebSocket（2pass）+ `cam++` 说话人分离 |
| FR-LIVE-04 [V1] | 说话人归属 | ⚠️ 输出匿名 `spk` 编号，需外部映射企业用户/nickname |
| 4.2 触发策略 | 事件触发（句子完整、说话人切换） | ✅ VAD 切句 + `start/end` 毫秒时间戳，可作触发源 |
| 4.5 时延预算 | ASR 流式出字 ≤ 300ms | ⚠️ 流式为句子级 2pass（草稿→修正），未见官方 P 值，预估 1–3s，需实测 |
| 5 非功能 · 隐私 | 音频与转写默认不长期存储 | ✅ 本地部署、数据不出域，最强合规 |
| 5 非功能 · 成本 | 事件触发而非全量 | ⚠️ 本地算力固定成本，与调用量无关；需自估 GPU 实例费用 |
| 7 选型 · 会议平台接入 | Zoom/腾讯会议/飞书/自建 WebRTC | ❌ FunASR 不含平台层；需配合自建 WebRTC 或旁路采集 |

---

## 2. FunASR 是什么

- 由阿里达摩院语音实验室开发的开源端到端语音识别工具包（GitHub `modelscope/FunASR`，16k+ Star）。
- 核心定位：**打通「训练 → 微调 → 部署」全链路**的工业级 ASR 基础设施，不是纯研究框架。
- 架构：**端到端 + 模块化**，VAD / ASR / 标点 / 说话人 / 情感 / 事件检测可灵活串联组合。
- 许可证：**代码 Apache-2.0（部分模块 MIT）**；**模型权重需遵守 Model License Agreement**（商用通常允许，但需确认具体条款，尤其是企业内部分发）。
- 生产就绪特征：Docker / Kubernetes 部署、OpenAI 兼容 API、vLLM / Triton 加速、ONNX / GGUF 边缘量化。

---

## 3. 模型体系

### 3.1 主识别模型

| 模型 | 参数量 | 语言 | 特点 | 适用 |
|---|---|---|---|---|
| **Paraformer-zh Large** | 220M | 中文 + 粤语 | 6 万+小时训练，内置标点恢复 + 时间戳预测；**中文精度最佳**（AISHELL CER 1.95%，非实时流式均有） | 中文会议转写、字幕、标注 |
| **SenseVoiceSmall** | 234M | 中/粤/英/日/韩 | 非自回归，低延迟（10s 音频约 70ms），**内建情感识别 + 音频事件检测**（鼓掌/笑声等） | 多语言实时、需情绪/事件标签 |
| **Fun-ASR-Nano** ⭐ | 800M | 中/英/日 + 7 方言/26 口音 | LLM-ASR（SenseVoice 音频编码 + Qwen3-0.6B），vLLM 加速，强上下文/专名/语码切换；**注意：checkpoint 不提供可靠的逐字符时间戳**（issue #106），逐字时间用 Paraformer | 高吞吐批处理、实时字幕、难例（专名/口音） |
| **Fun-ASR-MLT-Nano** | 800M | 31 语言 | 独立多语言 checkpoint | 31 语言广覆盖 |
| MOSS-Transcribe-Diarize（第三方） | — | 多语 | **一次离线请求同时输出长文转写 + 时间戳 + 说话人标签**，无需外部 VAD；非实时 | 多人会议/访谈/播客长音频离线 |

### 3.2 辅助任务模型（可组合串联）

| 模型 | 任务 | 参数量 | 说明 |
|---|---|---|---|
| fsmn-vad | 语音活动检测（VAD） | 0.4M | 所有 ASR 的前处理，毫秒级切分语音段 |
| ct-punc | 标点恢复 | 290M | 中英文自动加标点 |
| **cam++** | 说话人分离/验证 | 7.2M | 说话人声纹嵌入，**单核 CPU 即可实时**，是 diarization 核心 |
| eres2netv2 | 说话人验证（备选） | — | 短片段多场景可用 cam++ 或此模型 |
| emotion2vec | 情感识别 | 300M | 4 万小时训练 |

> 典型组合：`VAD + Paraformer/Fun-ASR-Nano + CT-Punc + CAM++` → 一段长音频变为「带标点、带时间戳、带说话人标签」的结构化文字。

---

## 4. 关键能力：说话人分离（Speaker Diarization）

这是本项目（FR-LIVE-01「区分说话人」）的核心诉求，FunASR 在这点上比「腾讯会议 API」更可控：

### 4.1 工作原理

三模块协同：FSMN-VAD 切语音段 → CAM++ 提取每段说话人声纹嵌入 → 聚类并标注说话人 ID。

### 4.2 离线一次调用（Python API）

```python
from funasr import AutoModel
model = AutoModel(
    model="paraformer-zh",          # 或 "iic/SenseVoiceSmall"
    vad_model="fsmn-vad",
    punc_model="ct-punc",           # 标点模型是说话人分离的硬依赖
    spk_model="cam++",              # 说话人分离
)
res = model.generate(input="meeting.wav", batch_size_s=300)
for s in res[0]["sentence_info"]:
    print(f'[{s["start"]/1000:.1f}s-{s["end"]/1000:.1f}s] '
          f'speaker {s["spk"]}: {s["sentence"]}')
```

输出 `sentence_info` 字段：`spk`（说话人编号）/ `start` / `end`（毫秒）/ `sentence`（带标点文本）。

官方示例（227s 真实多人会议）：自动切 77 句、识别 11 个说话人，**无需预设人数**。

### 4.3 关键调优参数

| 参数 | 作用 | 推荐 |
|---|---|---|
| `spk_model` | 声纹模型 | `cam++`；短片段多用 `eres2netv2` |
| `preset_spk_num` | 预设说话人总数，跳过自动数人 | 已知人数（如双人访谈=2）必设，消除第三类抖动；未知不传 |
| `vad_kwargs.max_single_segment_time` | VAD 单段最大时长(ms) | 30000–60000，过长混入多人拉低聚类精度 |

### 4.4 实时流式说话人分离

实时 WebSocket 服务（`funasr/bin/realtime_ws.py` 或 `funasr_wss_server.py`）支持 `--enable_spk --spk_model cam++`，服务内部维护 **speaker tracker**，随流式识别**增量更新**说话人标签——每收到一句最终结果即带 `spk` 字段，前端可按人分列滚动。

> 注意：流式 diarization 成熟度标注为「Community verified」，生产前需实测并发、背压、重连与长会漂移。

---

## 5. 接口与部署形态

### 5.1 三种接入面

| 接入面 | 协议/端点 | 适用 | 备注 |
|---|---|---|---|
| **离线文件转写** | HTTP `POST /v1/audio/transcriptions`（OpenAI 兼容，funasr-server） | 会后批处理、上传音频 | 任意 OpenAI SDK 改 base_url 即接（LangChain/Dify/Coze 零改动） |
| **实时流式** | WebSocket `ws://host:10095`，`2pass` 模式（先流式草稿再离线修正） | 会中实时字幕、麦克风流 | `chunk_size`/`chunk_interval` 可调；第一帧 JSON 握手带 hotwords/itn |
| **OpenAI realtime** | OpenAI realtime WebSocket（funasr-server realtime 模式） | 边缘/实时前端 | OpenAI-compatible HTTP / WebSocket |

### 5.2 部署形态与硬件

| 形态 | 命令/镜像 | 硬件 | 状态 |
|---|---|---|---|
| pip + funasr-server | `pip install funasr` → `funasr-server --device cuda` | GPU 8GB+ / CPU | 开发快速验证 |
| Docker | 官方 CPU/GPU 镜像 | CPU / GPU | 生产部署 |
| vLLM 加速 | `pip install vllm` 注册 FunASR 架构 | NVIDIA GPU | 高吞吐批处理，社区验证 |
| Triton / TensorRT | SenseVoice TensorRT | NVIDIA GPU | 生产验证（规划） |
| ONNX / C++ | `funasr-export` + ONNX Runtime | CPU / 边缘 | 移动/浏览器/树莓派 |
| GGUF / llama.cpp / SenseVoice.cpp | 量化推理 | CPU / 桌面 GPU | 边缘 standalone，生产验证 |
| Kubernetes | Docker Compose / ClusterIP | 集群 | 私有 API、批处理、实时 |

### 5.3 启动示例

```bash
# 实时 WebSocket 服务（端口 10095）
pip install -U funasr modelscope
git clone https://github.com/modelscope/FunASR.git
cd FunASR/runtime/python/websocket
python funasr_wss_server.py --port 10095

# OpenAI 兼容离线服务
funasr-server --device cuda --port 8899
curl -X POST http://localhost:8899/v1/audio/transcriptions \
  -F "file=@meeting.wav" -F "model=fun-asr-nano" -F "response_format=verbose_json"
```

---

## 6. 对本产品的适配分析

### 6.1 适配良好的点

- **隐私合规满分**：本地部署，音频与转写不离开内网，直接满足 Design.md 5「默认不长期存储、不跨租户训练」且与「开场告知」配合即可。
- **说话人分离质量高且可控**：CAM++ 聚类匿名编号 + 可选 `preset_spk_num`，比腾讯会议 API「外部参会人只有 nickname」更利于统一处理。
- **中文/方言更强**：Fun-ASR-Nano 7 方言 26 口音，Paraformer 中文 CER 远低于 Whisper；腾讯会议字幕仅中英。
- **全链路可微调**：可在预训练模型上微调垂直行业词表/口音，适配公司术语。
- **接口兼容 OpenAI**：未来若看板引擎想直接复用 Whisper 工具链，切换 base_url 即可。

### 6.2 必须解决的架构缺口

| 缺口 | 说明 | 应对 |
|---|---|---|
| **无音频接入层** | FunASR 只吃音频（文件 / PCM 流），不提供会议平台；腾讯会议 API 又**不开放原始音频**，只开放转写文字 | 若要 FunASR 替换腾讯会议 asr-push，接入层需改为**自建 WebRTC 或旁路采集音频**（与 Design.md Q1「自建 WebRTC」选项耦合）。否则 FunASR 只能用于「本地音频文件 → 纪要」的会后场景 |
| **说话人匿名** | `spk` 是 0/1/2 聚类编号，跨会议不绑定真人 | 需建「声纹注册库 / 已知片段匹配」把 spk 映射到企业 userid/nickname；或会前让参会人朗读注册句 |
| **算力与运维** | 需自备 GPU/CPU、扩缩容、监控、回滚 | 容器化 + K8s；按需预留实例。相比腾讯会议 API 的「免运维按量」是明显额外成本 |
| **实时 diarization 成熟度** | 流式说话人分离为社区验证级 | MVP 可先做「离线文件 → 会后看板」，再演进实时；或实时用昵称旁路（见下） |

### 6.3 与腾讯会议 API 方案的本质区别

腾讯会议方案 = **平台 + 音频 + ASR + 说话人** 全包（但说话人信息受账号体系限制、不开放音频、需付费版）。
FunASR 方案 = **只含 ASR + 说话人**，平台与音频需自建。

因此两者不是「同一层的可选接口」，而是**两种产品形态**：
- 选腾讯会议方案 → 接入层是腾讯会议，ASR 用其 asr-push；
- 选 FunASR 方案 → 接入层是自建音视频，ASR/diarization 用 FunASR。

---

## 7. 约束与风险

| 风险 | 影响 | 应对 |
|---|---|---|
| 平台层空白 | 没有音频来源，FunASR 无用武之地 | Q1 若定「自建 WebRTC」，FunASR 才顺；否则回退用腾讯会议 asr-push |
| 模型许可证 | 代码 Apache-2.0/MIT，模型需 Model License Agreement | 商用前确认 Fun-ASR-Nano / SenseVoice / cam++ 的商用条款 |
| 说话人漂移 | 长会（>1h）聚类可能把同一人拆成多号 | 用说话人注册/声纹库校准；分段处理 + 跨段对齐 |
| 实时延迟不确定 | 无官方 P 值，2pass 草稿→修正 | M1 实测端到端延迟；必要时用 Paraformer-online 纯流式放弃修正换延迟 |
| GPU 成本 | 并发高时需多卡 | vLLM 批处理 + 量化；评估单实例并发上限 |
| 重连/背压 | WebSocket 客户端需自处理 | 官方已知限制，需自写重连与背压逻辑 |

---

## 8. 两方案对比（决策用）

| 维度 | 腾讯会议 asr-push（主链路） | FunASR 本地部署（备选） |
|---|---|---|
| 平台层 | ✅ 腾讯会议提供 | ❌ 需自建 WebRTC/旁路采集 |
| 音频来源 | 平台内部，不开放 | 自供（自建或采集） |
| 说话人信息 | userid/ms_open_id/nickname（受账号体系限制） | 匿名 spk 编号（可自建声纹库绑定） |
| 中文/方言 | 仅中英字幕 | 7 方言 26 口音，中文 CER 更优 |
| 延迟 | 句子级 1–3s（实测待定） | 句子级 2pass（实测待定） |
| 隐私 | 文本推送，不碰音频 | 数据完全不出域 |
| 成本 | 按量/企业版订阅，免运维 | 算力+运维固定成本 |
| 上线速度 | 快（API 对接） | 慢（含平台/采集/部署） |
| 可控性/可微调 | 低 | 高 |

---

## 9. 建议

1. **首发仍建议腾讯会议 asr-push**（接入最快、平台齐全）。FunASR 作为 Q1「自建 WebRTC」分支的预设 ASR 组件记录在案。
2. **若 Q8（是否存储音频）答案为「不存储」且隐私优先级极高**，可评估「自建 WebRTC + FunASR」全本地方案，但需把平台层工作量计入 M1 之后的里程碑。
3. **混合路线（可选）**：实时用腾讯会议 asr-push 出板；会后用 FunASR（本地）对会议录音重新转写+diarization 做高精度对齐与校正——兼顾速度与可控。
4. **待实测项**：两方案的句子级端到端延迟 P95、并发上限、外部/匿名说话人归属准确率、长会说话人漂移。

---

## 10. 参考链接

| 内容 | 链接 |
|---|---|
| FunASR 官网（部署矩阵/能力） | https://funasr.com/en/ |
| FunASR 模型对比 | https://www.funasr.com/en/models.html |
| 实时流式与字幕部署 | https://funasr.com/en/deploy/realtime.html |
| 说话人分离博客（含代码/输出） | https://funasr.com/en/blog/funasr-speaker-diarization.html |
| FunASR 主仓库 | https://github.com/modelscope/FunASR |
| Fun-ASR 模型仓库 | https://github.com/FunAudioLLM/Fun-ASR |
| SenseVoice 模型仓库 | https://github.com/FunAudioLLM/SenseVoice |
| WebSocket HTML5 客户端接入 | https://github.com/modelscope/FunASR/tree/main/runtime/html5 |
| FunASR 快速部署与调用指南 | https://blog.csdn.net/qq_24211853/article/details/163339642 |
| 说话人分离实战指南 | https://blog.csdn.net/gitblog_00430/article/details/159787323 |
