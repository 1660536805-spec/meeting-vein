# 会脉 · 在线部署运行手册（魔搭创空间 / Docker）

本手册用于把会脉部署为**评审可通过浏览器打开的在线体验链接**，并说明能力边界。
配套产物：`Dockerfile`、`docker/nginx.conf`、`docker/entrypoint.sh`、`.dockerignore`。

---

## 1. 架构

单容器、单公开端口：

```
浏览器 ──► Nginx :7860（唯一对外）
             ├─ /            静态托管前端（Vite 多页产物）
             ├─ /api/*   →  会议后端 FastAPI :8000（127.0.0.1）
             ├─ /ws/*    →  会议后端 WebSocket（含 Upgrade）
             └─ /asr/*   →  本地 ASR FastAPI :9000（去掉 /asr 前缀）
```

`docker/entrypoint.sh` 启动并监控三个进程；任一必需进程退出即容器退出，便于平台日志暴露故障。

---

## 2. 本地构建与验证

```bash
cd ai_meeting_organizer-fusion

# 默认国内镜像源；部署到 x86_64 创空间时追加 --platform linux/amd64
docker build -t meeting-vein:local .

docker run --rm -p 7860:7860 meeting-vein:local
```

冒烟检查（另开终端）：

```bash
curl -fsS http://127.0.0.1:7860/            -o /dev/null && echo "首页 OK"
curl -fsS http://127.0.0.1:7860/workspace.html -o /dev/null && echo "工作台 OK"
curl -fsS http://127.0.0.1:7860/api/status | head -c 400; echo
curl -fsS http://127.0.0.1:7860/asr/models/status | head -c 200; echo
```

预期：`/api/status` 返回 `llm_mode`（容器默认 `mock`）；`/asr/models/status` 返回 `state: not_loaded`（容器默认关闭模型加载）。
浏览器打开 `http://127.0.0.1:7860/`，进入工作台后手动输入一句话，确认看板出现节点。

### 构建参数

| 参数 | 默认 | 用途 |
| --- | --- | --- |
| `NODE_IMAGE` | `node:22-slim` | 前端构建基础镜像 |
| `PYTHON_IMAGE` | `python:3.12-slim` | 运行时基础镜像 |
| `APT_MIRROR` | `mirrors.aliyun.com` | Debian 软件源 |
| `PIP_INDEX_URL` | 清华 PyPI | Python 包源 |
| `NPM_REGISTRY` | `registry.npmmirror.com` | npm 源 |

> 依赖版本由 `docker/constraints.txt` 固定，规避部分镜像站元数据缺失导致的解析回退。

---

## 3. 发布到魔搭创空间

1. 登录魔搭，确认已完成**账号绑定与实名认证**（Docker 创空间的前置条件）。
2. 创建 **Docker 类型**创空间，公开访问级别设为评委可访问；对外端口填 `7860`。
3. 上传本仓库必要文件（或关联 Git 仓库）。构建起点为仓库根，使用根目录 `Dockerfile`。
4. 观察构建日志；首次构建需拉取基础镜像与依赖，耗时较长属正常。
5. 构建成功后，用创空间公开链接验证：首页、工作台、`/api/status`、文字输入到看板更新一条链路。
6. 把最终链接补充到 `README.md` 的部署章节与报名说明。

---

## 4. 能力边界（务必如实披露）

| 项 | 容器默认 | 说明 |
| --- | --- | --- |
| 大模型 | `AMO_LLM_ENABLED=0`（MockLLM） | 演示可用且不外发会议文本。启用真实模型：在创空间设**私密环境变量** `AMO_LLM_ENABLED=1`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`。启用后会议文本会发往该服务。 |
| 本地 ASR | `LOCAL_ASR_ENABLE_MODELS=0` | 不加载 FunASR/torch（镜像不含重依赖），语音显示未就绪；**文本/手动输入到看板链路完整可用**。 |
| 数据目录 | `/app/backend/.amo_data` | 容器内可写；实例重启可能重置，重要数据请导出本地快照。 |
| 模型缓存 | `/data/models/*` | 仅启用 ASR 模型时使用。 |

**禁止**把 MockLLM 输出描述为真实模型推理；任何质量数字必须标注来源（真实 LLM / Mock）。

---

## 5. 已验证 / 待验证

- [x] 本地 `docker build` 成功、`docker run -p 7860:7860` 起容器
- [x] `/`、`/workspace.html`、`/api/status`、`/asr/models/status` 可达
- [x] `/ws` 握手 + 一轮文字输入更新看板
- [x] 镜像内不含 `.env` / 密钥 / 会议数据 / 模型缓存
- [ ] 创空间真实构建与公开访问（依赖账号实名与平台配额）

> 勾选项以实际执行结果为准，未执行项不得勾选。
