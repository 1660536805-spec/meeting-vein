# AI Meeting Organizer · 本地语音会议看板

项目主仓库：[meeting-vein](https://github.com/1660536805-spec/meeting-vein)。后续代码与文档更新统一推送到该仓库。

一个本机运行的会议产品：浏览器录音或上传音频，SenseVoiceSmall 在本机转写，发言进入双 Agent 编排，实时更新 AntV X6 看板。语音服务不可用时，底部手动发言仍可使用。

请通过 `http://127.0.0.1:5173/` 打开，不要直接打开 `frontend/index.html` 的 `file://` 路径；开发服务代理负责连接两个后端。

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
- **本机边界**：三个服务只绑定 `127.0.0.1`；音频不上传到云端。LLM 未配置时界面显示“演示模式”，由 MockLLM 生成看板；如果配置兼容模型，会议文本才会按配置发往该模型服务。

## 目录

```
backend/            Python 后端（纯 stdlib 可跑 M0-M2；M3 起需 pip install）
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

打开 `http://127.0.0.1:5173/`。录音支持开始、暂停、继续、停止；也可选择本地音频文件。转写成功后会自动发往看板；若看板暂不可用，页面保留转写文本并提供“重试发送”，不会重新识别或生成另一个发言 ID。音频格式需要 `ffmpeg` 可解码。页面显示 ASR 模型状态和看板分析模式。

看板右侧使用 DS 鲸鱼娘 Live2D 模型。模型、贴图、动作与表情素材约 4.2 MiB，来源和署名链见 [`frontend/public/mascot/NOTICE.md`](frontend/public/mascot/NOTICE.md)：上善无形 → ZipZipPipe → 氵六青，许可为 CC BY-NC-SA 4.0（须署名、仅限非商业使用，改编须相同方式共享）。该素材不可直接用于商业产品；商业使用前须取得权利人授权。

首次加载 Live2D 需要浏览器可访问 Live2D 官方 Cubism Core CDN；Core 是专有运行时，本项目不随仓库分发。模型加载成功后会显示动态看板娘；Core、WebGL 或模型加载失败时会显示项目自制静态图，状态提示、录音和看板功能仍可用。若一直看到静态图，先检查网络是否拦截 `cubism.live2d.com`、浏览器 WebGL 是否可用，并查看浏览器控制台及页面是否通过本地开发服务器打开。

录音过程中，本机流式 Paraformer 每约 480 毫秒接收一个音频块，持续显示可修正的临时文字和音频块到显示的响应时间。当前实现将静音切分后的最终分段提交到看板；没有成功分段时，停止录音后可用 SenseVoiceSmall 对完整录音识别兜底。分段发送失败的恢复和最终落图回执仍在[产品完整化实施方案](docs/superpowers/plans/2026-10-06-product-completion.md)中列为待完成项。首次启动需等待两个模型加载；流式模型不可用时仍可录音并在停止后识别。响应时间是浏览器端估算，不等同于从某个字说出口到显示的精确时延；是否达到 1 秒目标需在实际麦克风和电脑上测试。
流式模型权重缓存于已忽略的 `local_asr/model_cache/`，首次下载约 881 MB。
下载完成后可运行 `local_asr/.venv/bin/python local_asr/scripts/benchmark_streaming.py` 测量示例音频每块的推理耗时；真实麦克风延迟以页面显示和实际观察为准。

如只想验证看板，先填底部文本框并点“发送”；即便 ASR 未安装或模型尚未就绪，这条路径也能工作。不要在演示模式下把生成的会议结论当作真实 LLM 分析。

### 单独运行与健康检查

```bash
cd backend && PYTHONPATH=. ../.venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8000
cd local_asr && PYTHONPATH=backend .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 9000
cd frontend && npm run dev -- --host 127.0.0.1 --strictPort
```

以上三条分别在不同终端运行。健康地址：`http://127.0.0.1:8000/api/status`、`http://127.0.0.1:9000/health`、`http://127.0.0.1:9000/models/status`。启动器会检查端口是否被占用，不会关闭其他进程；在启动器终端按 Ctrl+C 只停止它启动的三个子进程。

### 模型与隐私

ASR 默认使用 `iic/SenseVoiceSmall` 与 `fsmn-vad`，首次运行会通过 ModelScope 下载权重。录音上传至本机 loopback ASR 进程进行解码；除非显式打开调试选项，否则不保存音频文件。转写原文、会议看板保存在 `backend/.amo_data/`，请按敏感会议资料处理；该目录已被 Git 忽略。若配置外部 LLM，阅读其数据处理政策并在有权发送会议内容时才启用。环境变量示例见 [`.env.example`](.env.example)。

### 验证

```bash
cd local_asr && .venv/bin/python -m pytest -q
cd ../backend && PYTHONPATH=. ../.venv/bin/python -m unittest discover -s tests -p 'test*.py' -q
cd ../frontend && npm test -- --run && npm run build
```

## 设计文档索引

`doc/Design.md`（总纲）、`Design_Agent_DataFlow.md`（双 Agent 编排）、`Design_StructureGraph_Storage.md`（双存储）、
`Design_CursorCapture.md`（光标采集）、`Design_InputProcessing.md`（双 Agent 交接）、
`Design_FrontendBoard.md`（前端+工具层）、`Design_Mascot.md`（看板娘）、`ASR_UnifiedSchema.md`（统一输入流）。

## 已知边界

自动化测试使用假 ASR runtime 验证了事件契约和看板集成；这不等于当前电脑已完成真实麦克风 → SenseVoiceSmall 的端到端验证。未安装 `asr` 可选依赖时，页面会显示具体模型加载错误，手动发言和看板仍可运行。详细融合设计见 `docs/superpowers/specs/2026-09-23-meeting-product-integration-design.md`。
