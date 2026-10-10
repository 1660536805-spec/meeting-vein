# AI Meeting Organizer · 本地语音会议看板

项目主仓库：[meeting-vein](https://github.com/1660536805-spec/meeting-vein)。后续代码与文档更新统一推送到该仓库。

一个本机运行的会议产品原型：可建会并确认议程，浏览器录音或上传音频后由本机 ASR 转写，发言进入双 Agent 编排并更新 AntV X6 看板；会后可以结束会议并导出带来源的 Markdown/HTML 纪要。真实麦克风、真实 LLM 和整套 M1 可靠性验收仍未完成，因此当前交付状态仍是本地 Demo。

请通过 `http://127.0.0.1:5173/` 打开；开发服务代理负责连接两个后端。若误从 `file://` 打开工作台，页面会寻找本地预览服务并引导跳转。

## 架构

```
本地 ASR(9000) → NormUtterance ┐
                        ├─→ 过滤 → 双 Agent(分析→图同步) → Store A(关系图, X6 同构) → 前端看板
光标流(NormCursorEvent)┘                                                          ↑ metadata_refs
                                                                Store B(原始论据, 按 meta_id 反查)
```

- **双存储**：Store A=后处理派生关系图（精炼，X6 `fromJSON` 零翻译）；Store B=原始语音/议程元数据（按 `meta_id` 索引，供节点论据反查）。
- **双 Agent**：分析 Agent 产出 `MeetingSummary`（论点/论据/结论/行动/分歧）；图同步 Agent 产出 `GraphUpdateOp` 落 Store A。两 Agent 经 `AgentState.meeting_summary` 交接。
- **统一输入流**：ASR 与光标同形（`NormUtterance` / `NormCursorEvent`），对等流入编排。
- **看板娘**：DS 鲸鱼娘 Live2D 模型替换原 SVG 秘书；会议状态驱动模型动作，眼睛追踪纯表现层。
- **本机边界**：推荐启动方式将三个服务绑定 `127.0.0.1`；音频不保存。识别出的原话会保存在本机 Store B。界面报告实际运行的 LLM 客户端：Mock 模式不会调用真实模型；真实 OpenAI 兼容客户端会把会议文本发往所配置的服务。

## 当前交付范围

| 阶段 | 当前状态 | 能力边界 |
| --- | --- | --- |
| M0 演示可靠 | 进行中 | 有预置会议、手动输入、本地 ASR 和只读快照基础；窄屏/大样本可读性、落图回执和恢复能力仍待验收。 |
| M1 单机内测 | 未完成 | 目标是单机部署、单人编辑、多人只读查看，并完整走通建会、议程、输入、纠错、结束与纪要导出；不能据当前 Demo 宣称完成。 |
| M2 对外试用 | 未开始 | 登录权限、跨设备安全分享、撤销、备份恢复、监控及腾讯会议授权接入尚未交付。 |

MockLLM 是规则化演示输出，不是真实模型推理，也不能用于宣称抽取质量。真实 LLM 仅在实际实例成功初始化后才可作为真实链路；模型 key 或端点配置本身不构成真实链路验收。指标目标及测量定义见 [`doc/Design.md`](doc/Design.md)，当前尚无合格标注集的质量报告。

## 目录

```
backend/            Python 后端（部分演示/CLI 路径可用 stdlib；FastAPI 服务需安装依赖）
  app/
    config.py models.py storage.py adapters.py llm.py prompts.py orchestrator.py
    tools/        Agent 工具接口层（尊重 lock/edit，冲突回 skipped）
    ws/bus.py      WS 事件总线
    server.py      FastAPI 服务入口（需 pip install）
  run_demo.py      端到端 mock 验证（无需联网）
frontend/          前端（需 npm install）
  src/asr/          录音、上传、转写提交与状态面板
local_asr/         本地 SenseVoiceSmall ASR 服务及测试
scripts/start_local.sh  三服务本机启动器
  src/board/        X6 渲染 + 光标采集(CursorEventAdapter, 200ms 节流)
  src/mascot/       看板娘状态、Live2D 渲染与静态回退
  src/api/ws.ts     WS 客户端
doc/                设计文档（Design_* / Research_*）
docs/superpowers/plans/2026-10-06-product-completion.md  产品完整化实施方案
design-system/      UI token 参考（Linear 暗色 / Miro 白板）
```

## macOS 本地启动

需要 Python 3.12、Node.js/npm、`ffmpeg` 和 `uv`。首次安装会下载较大的 PyTorch、FunASR 和模型权重；请预留磁盘空间和网络时间。模型权重首次加载状态可在页面和 `http://127.0.0.1:9000/models/status` 查看。

```bash
cd /path/to/ai_meeting_organizer-fusion
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cd local_asr
uv sync --python 3.12 --extra asr --extra dev
cd ../frontend
npm ci
cd ..
bash scripts/start_local.sh
```

已有预览标签使用 5176 时，可改用 `AMO_FRONTEND_PORT=5176 bash scripts/start_local.sh`。工作台通过输出的 `http://127.0.0.1:<端口>/workspace.html` 使用；误从 `file://` 打开时会尝试跳转到 5176 或 5173，服务未启动时显示启动说明。无 `meeting_id` 的工作台入口会创建一场“新会议”并跳转到该会议；创建失败时页面会显示重试入口。

打开 `http://127.0.0.1:5173/`。录音支持开始、暂停、继续、停止；也可选择本地音频文件。转写成功后会自动发往看板；若看板暂不可用，页面保留转写文本并提供“重试发送”，不会重新识别或生成另一个发言 ID。音频格式需要 `ffmpeg` 可解码。页面显示 ASR 模型状态和看板分析模式。

看板右侧使用 DS 鲸鱼娘 Live2D 模型。模型、贴图、动作与表情素材约 4.2 MiB，来源和署名链见 [`frontend/public/mascot/NOTICE.md`](frontend/public/mascot/NOTICE.md)：上善无形 → ZipZipPipe → 氵六青，许可为 CC BY-NC-SA 4.0（须署名、仅限非商业使用，改编须相同方式共享）。该素材不可直接用于商业产品；商业使用前须取得权利人授权。

首次加载 Live2D 需要浏览器可访问 Live2D 官方 Cubism Core CDN；Core 是专有运行时，本项目不随仓库分发。模型加载成功后会显示动态看板娘；Core、WebGL 或模型加载失败时会显示项目自制静态图，状态提示、录音和看板功能仍可用。若一直看到静态图，先检查网络是否拦截 `cubism.live2d.com`、浏览器 WebGL 是否可用，并查看浏览器控制台及页面是否通过本地开发服务器打开。

录音过程中，本机流式 Paraformer 每约 480 毫秒接收一个音频块，持续显示临时文字和音频块到显示的响应时间。静音切分后的最终分段提交到看板，连续讲话最长约 8 秒切段；没有成功分段时，停止录音后可用 SenseVoiceSmall 对完整录音识别兜底。有实时分段时，停止录音后生成的完整转写仅供核对，不自动重复入板。前端会查询服务端的 accepted、processing、committed、failed 状态，并将未确认分段保存在浏览器本地队列；`committed` 还会区分原话已关联看板与处理完成但未入板。失败时可重试发送，当前会议的积压批次也可从语音面板手动恢复。首次启动需等待两个模型加载；流式模型不可用时仍可录音并在停止后识别。响应时间是浏览器端估算，不等同于从某个字说出口到显示的精确时延；是否达到 1 秒目标需在实际麦克风和电脑上测试。状态定义见 [`doc/contracts/utterance-state.md`](doc/contracts/utterance-state.md)。
流式模型权重缓存于已忽略的 `local_asr/model_cache/`，首次下载约 881 MB。
下载完成后可运行 `local_asr/.venv/bin/python local_asr/scripts/benchmark_streaming.py` 测量示例音频每块的推理耗时；真实麦克风延迟以页面显示和实际观察为准。

如只想验证看板，先填底部文本框并点“发送”；即便 ASR 未安装或模型尚未就绪，这条路径也能工作。不要在演示模式下把生成的会议结论当作真实 LLM 分析。

“生成本地只读链接”会在 Store A 保存只读快照 token。它不是带身份验证或时效撤销的公开分享服务；只有能够访问同一个服务地址的查看者才可打开。默认 `127.0.0.1` 地址仅本机可达，不代表跨设备分享成功。需要带走数据时，可用“导出本地快照”下载包含看板、原话和历史的 JSON 文件。

### 单独运行与健康检查

```bash
cd backend && PYTHONPATH=. ../.venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8000
cd local_asr && PYTHONPATH=backend .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 9000
cd frontend && npm run dev -- --host 127.0.0.1 --strictPort
```

以上三条分别在不同终端运行。健康地址：`http://127.0.0.1:8000/api/status`、`http://127.0.0.1:9000/health`、`http://127.0.0.1:9000/models/status`。启动器会检查端口是否被占用，不会关闭其他进程；在启动器终端按 Ctrl+C 只停止它启动的三个子进程。

本地存储位于 `backend/.amo_data/`。如果启动时报 `board_storage_unreadable` 或 `metadata_storage_unreadable`，先停止后端并复制整个目录留作恢复依据，再从已知可用的备份恢复；不要删除损坏文件后直接重启。Store B 会忽略崩溃留下的半截日志尾行，但完整日志行或快照损坏会明确阻止继续写入。

### 模型与隐私

ASR 默认使用 `iic/SenseVoiceSmall` 与 `fsmn-vad`，首次运行会通过 ModelScope 下载权重。录音上传至本机 loopback ASR 进程进行解码；音频不落盘，转写原文与会议看板保存在 `backend/.amo_data/`，请按敏感会议资料处理；该目录已被 Git 忽略。工作台在建会、文本输入和录音入口提示这些边界。若运行状态显示真实外部 LLM，会议文本会离开本机并发送至所配置的服务；阅读其数据处理政策并在有权发送会议内容时才启用。环境变量示例见 [`.env.example`](.env.example)。

### 验证

```bash
cd local_asr && .venv/bin/python -m pytest -q
cd ../backend && PYTHONPATH=. ../.venv/bin/python -m unittest discover -s tests -p 'test*.py' -q
cd ../frontend && npm test -- --run && npm run build
```

## 魔搭创空间在线部署（Docker）

除本机运行外，可用单镜像把整个原型部署为**评审可访问的在线体验链接**。镜像由 Nginx 在唯一公开端口 `7860` 提供服务，并把 `/api`、`/ws`、`/asr` 同源反向代理到容器内的会议后端与本地 ASR。

```bash
# 本地构建并运行（国内镜像源已设为默认；部署到 x86_64 创空间时追加 --platform linux/amd64）
docker build -t meeting-vein:local .
docker run --rm -p 7860:7860 meeting-vein:local
# 打开 http://127.0.0.1:7860/
```

构建产物与约束：

- `Dockerfile`：多阶段（Node 构建前端 → Python 运行时）；基础镜像、apt / pip / npm 源均可通过 `--build-arg` 覆盖（默认国内镜像）。
- `docker/nginx.conf`：7860 同源入口，多页 fallback + `/api`、`/ws`、`/asr` 代理。
- `docker/entrypoint.sh`：启动并监控 Nginx + 会议 API + 本地 ASR，任一必需进程退出即容器失败，便于创空间日志暴露故障。
- `.dockerignore`：排除 `.env`、密钥、会议数据（`.amo_data`）、模型缓存、媒体与文档，镜像不含任何凭据或个人数据。

容器内的能力边界（与创空间说明一致）：

| 项 | 容器默认 | 说明 |
| --- | --- | --- |
| 大模型 | `AMO_LLM_ENABLED=0`（MockLLM） | 保持演示可用且不外发会议文本；需要真实模型时在创空间设私密环境变量 `AMO_LLM_ENABLED=1` 与 `OPENAI_*`。 |
| 本地 ASR | `LOCAL_ASR_ENABLE_MODELS=0` | 不加载 FunASR/torch 双模型（镜像不含重依赖），语音显示为未就绪；**手动/文本输入到看板更新的完整链路仍可用**。需要语音时可装 `asr` 依赖并置 `LOCAL_ASR_ENABLE_MODELS=1`。 |
| 数据目录 | `/app/backend/.amo_data` | 容器内可写；实际持久性取决于创空间实例，重启可能重置，重要数据请随时导出快照。 |
| 模型缓存 | `/data/models/*` | 仅在启用 ASR 模型时使用，可用 `LOCAL_ASR_MODEL_CACHE_DIR` / `LOCAL_ASR_STREAMING_CACHE_DIR` 覆盖。 |

创空间侧：创建 Docker 类型、公开访问级别的创空间，对外端口填 `7860`；构建前需完成平台要求的账号绑定与实名认证。发布后把在线地址补充到本节与报名说明，并明确标注当前为 **Mock 演示模式**，不得把 Mock 输出当作真实模型推理。

## 设计文档索引

`doc/Design.md`（总纲）、`Design_Agent_DataFlow.md`（双 Agent 编排）、`Design_StructureGraph_Storage.md`（双存储）、
`Design_CursorCapture.md`（光标采集）、`Design_InputProcessing.md`（双 Agent 交接）、
`Design_FrontendBoard.md`（前端+工具层）、`Design_Mascot.md`（看板娘）、`ASR_UnifiedSchema.md`（统一输入流）。

## 已知边界

自动化测试使用假 ASR runtime 验证了事件契约和看板集成；这不等于当前电脑已完成真实麦克风 → SenseVoiceSmall 的端到端验证。未安装 `asr` 可选依赖时，页面会显示具体模型加载错误，手动发言和看板仍可运行。详细融合设计见 `docs/superpowers/specs/2026-09-23-meeting-product-integration-design.md`。本地 Store B 会保存发言原文；启用外部 LLM 时会议文本会离开本机并发送到配置的服务。
