# 会脉 · 魔搭创空间 Docker 部署
# 单镜像承载前端静态产物 + 会议 API + 本地 ASR，统一由 Nginx 在 7860 对外。
# 约束：不打包 .env / 密钥 / 会议数据 / 模型缓存（见 .dockerignore）。
#
# 基础镜像与软件源可用构建参数覆盖（默认取国内镜像，兼顾 ModelScope 创空间网络环境）：
#   docker build --build-arg NPM_REGISTRY=... --build-arg PIP_INDEX_URL=... -t meeting-vein:local .
# 部署到 x86_64 创空间时追加：--platform linux/amd64

ARG NODE_IMAGE=node:22-slim
ARG PYTHON_IMAGE=python:3.12-slim

# ---------- Stage 1: 构建前端（Vite 多页） ----------
FROM ${NODE_IMAGE} AS frontend
ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm config set registry "$NPM_REGISTRY" && npm ci
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: 运行时 ----------
FROM ${PYTHON_IMAGE}
ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Debian 源换国内镜像（规避 deb.debian.org 不可达；兼容 sources.list 与 deb822 debian.sources）
RUN set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
      if [ -f "$f" ]; then sed -i "s|deb.debian.org|${APT_MIRROR}|g" "$f"; fi; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends nginx ffmpeg ca-certificates; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 会议后端（FastAPI + LangGraph + OpenAI 兼容）
# constraints.txt 固定传递依赖版本，规避部分镜像站的元数据缺失导致的解析回退。
COPY requirements.txt docker/constraints.txt ./
RUN pip install -i "$PIP_INDEX_URL" -c constraints.txt -r requirements.txt
COPY backend/ ./backend/

# 本地 ASR 服务：只装轻量 Web 依赖，不装 FunASR/torch 重依赖。
# 容器默认 LOCAL_ASR_ENABLE_MODELS=0，语音模型不加载，文本与手动输入路径仍可用。
RUN pip install -i "$PIP_INDEX_URL" \
    "fastapi>=0.116,<1" \
    "pydantic-settings>=2.10,<3" \
    "python-multipart>=0.0.20,<1" \
    "uvicorn[standard]>=0.35,<1" \
    "numpy>=2,<3"
COPY local_asr/backend/ ./local_asr/backend/

# 前端静态产物
COPY --from=frontend /build/dist ./frontend_dist

# Nginx 同源代理配置（7860 -> /api /ws /asr）
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
RUN rm -f /etc/nginx/sites-enabled/default

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV AMO_DATA_DIR=/app/backend/.amo_data \
    LOCAL_ASR_ENABLE_MODELS=0 \
    LOCAL_ASR_MODEL_CACHE_DIR=/data/models/modelscope \
    LOCAL_ASR_STREAMING_CACHE_DIR=/data/models/streaming \
    AMO_LLM_ENABLED=0

EXPOSE 7860
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
