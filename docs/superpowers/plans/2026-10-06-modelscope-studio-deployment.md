# 魔搭创空间 Docker 部署实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将会脉部署到魔搭 Docker 创空间，并得到评委可打开的在线体验链接。

**Architecture:** Nginx 在 7860 提供前端静态构建并代理 `/api`、`/ws`、`/asr`；入口脚本启动 Nginx、会议 API 和本机 ASR 三个进程。默认无真实 LLM 密钥，保持 MockLLM 演示并在说明中披露；ASR 模型缓存放在可配置目录。

**Tech Stack:** 现有 Vite/TypeScript 前端、FastAPI/Uvicorn、FunASR、Nginx、Docker、ModelScope Studio。

**Spec:** `docs/superpowers/specs/2026-10-06-modelscope-studio-deployment-design.md`

## Global Constraints

- 创空间唯一公开监听端口为 `7860`。
- 本地 `scripts/start_local.sh` 及其端口和行为保持不变。
- API、WebSocket 和 ASR 必须经同源 Nginx 路由。
- 不将真实 API key、`.env`、用户会议数据、音频或模型缓存打包上传。
- 默认说明并展示 MockLLM 演示模式；不得把 Mock 输出描述成真实大模型推理。
- Docker 创空间构建前需确认账号绑定/实名认证和平台资源配额状态。

## Review Focus

- 前端 WebSocket 使用动态同源地址且经过 `/ws` 代理；容器烟测覆盖连接路径。
- ASR 启动失败不能阻断会议后端、静态页面和文字演示；健康探测及页面状态应区分该故障。
- SenseVoice/Paraformer 首次下载可能超时或超出资源；启动行为需有清晰日志、缓存目录可配置。
- Vite 多页构建所有入口都要被 Nginx fallback 正确服务；烟测覆盖首页和工作台入口。
- 会议信息写入目录必须在容器中可写，重启持久性在 README 中准确说明。

---

### Task 1: 增加创空间运行配置

**Files:**
- Create: `Dockerfile`
- Create: `docker/nginx.conf`
- Create: `docker/entrypoint.sh`
- Modify: `.dockerignore`
- Modify: `local_asr/backend/app/config.py`（只在现有设置未覆盖时加入 ASR 可选启动/资源配置）

**Interfaces:**
- Nginx 对外监听 `0.0.0.0:7860`，代理内部会议 API `127.0.0.1:8000`、ASR `127.0.0.1:9000`，WebSocket `/ws` 需转发 Upgrade/Connection。
- `docker/entrypoint.sh` 以前台方式运行并监控三个服务；优雅终止所有子进程。
- 模型缓存目录可由 `LOCAL_ASR_MODEL_CACHE_DIR` 和 `LOCAL_ASR_STREAMING_CACHE_DIR` 配置，默认落在创空间可写缓存下。

- [ ] 确认 ASR 应用能在不安装可选 FunASR 推理依赖时保持 API 可启动并返回明确的模型不可用状态；以 `LOCAL_ASR_ENABLE_MODELS=0` 作为部署开关。
- [ ] 实现 `docker/nginx.conf` 的 `/` 静态托管、多页 fallback，以及 `/api`、`/ws`、`/asr` 路由。
- [ ] 编写入口脚本启动会议后端和 ASR；未启用 ASR 推理时设 `LOCAL_ASR_ENABLE_MODELS=0`，避免容器启动阻塞。
- [ ] 编写 Dockerfile 构建前端、安装两个 Python 服务依赖及 ffmpeg/Nginx，最终在 7860 启动。
- [ ] 编写 `.dockerignore` 排除 `.env`、本地 venv、node_modules、模型缓存、运行数据、音频和视频大文件。
- [ ] 运行 `docker build` 检查镜像能构建。

### Task 2: 容器本地冒烟验证与部署说明

**Files:**
- Modify: `README.md`
- Modify: `backend/app/server.py`（仅如跨域或 public host websocket 校验阻止同源代理时）
- Modify: `frontend/src/api/ws.ts`（仅如当前实现未按页面协议使用同源 `/ws`）

**Interfaces:**
- 容器 URL 为 `http://127.0.0.1:7860`。
- README 添加「魔搭创空间」章节：Docker 类型、CPU/内存要求、数据目录、模型下载、MockLLM 模式、可选 ASR 行为及在线体验链接位置。

- [ ] `docker run -p 7860:7860` 后请求 `/`、`/workspace.html`、`/api/status`、`/asr/models/status` 并记录预期响应。
- [ ] 检查 `/ws` WebSocket 握手及一轮文字输入更新看板的端到端路径。
- [ ] 检查 `.dockerignore` 和最终镜像文件清单，确认没有 `.env`、密钥、会议数据、录音或本地模型缓存。
- [ ] 更新 README，明确创空间 CPU 默认可先禁用本机 ASR 模型加载，仍可用文字体验 MockLLM；满足资源与账号条件后才打开模型推理。

### Task 3: 创建并发布魔搭创空间

**Files / external state:**
- ModelScope Studio：账号 `leo0767` 下创建一个 `会脉` Docker 创空间并推送本项目必要文件。

**Interfaces:**
- 根据创空间创建页选择 Docker SDK，公开端口 7860，硬件选择账号可用的最低稳定配置。
- 推送源仅包括 Task 1/2 所需的代码、前端源文件及运行依赖；不上传本地媒体素材目录、个人运行数据或凭据。

- [ ] 检查魔搭账号的 Docker 绑定、实名与硬件可用性；如果平台要求未完成的安全/法定账号操作，暂停并由用户完成。
- [ ] 在创空间设置为评委可访问的公开访问级别后创建项目，依据实际页面选项而非猜测。
- [ ] 上传部署所需项目文件，观察构建日志并修复容器启动问题。
- [ ] 等待部署成功，使用公开链接验证首页和文字输入演示。
- [ ] 将已验证创空间 URL 追加到 README 和报名使用说明。

## 执行边界

项目目录当前不是独立 Git 仓库，而是 `/Users/leo` 仓库中的未跟踪目录。不得在其父级仓库做全局 `git add`、提交或创建远端推送；创空间文件发布应使用创空间的专属上传或隔离导出的项目副本，只包含本计划列出的必要文件。
