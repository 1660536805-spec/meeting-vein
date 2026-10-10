#!/usr/bin/env bash
# 会脉 · 创空间容器入口：启动 Nginx + 会议 API + 本地 ASR，任一必需进程退出即整体退出。
set -euo pipefail

log() { echo "[entrypoint] $*"; }

# ---- 可写数据目录（会议看板/原话真相源）----
data_dir="${AMO_DATA_DIR:-/app/backend/.amo_data}"
mkdir -p "$data_dir"
if [ "$data_dir" != "/app/backend/.amo_data" ]; then
  rm -rf /app/backend/.amo_data
  ln -sfn "$data_dir" /app/backend/.amo_data
fi

# ---- ASR 模型缓存目录（仅当启用模型时才使用）----
: "${LOCAL_ASR_ENABLE_MODELS:=0}"
: "${LOCAL_ASR_MODEL_CACHE_DIR:=/data/models/modelscope}"
: "${LOCAL_ASR_STREAMING_CACHE_DIR:=/data/models/streaming}"
: "${AMO_LLM_ENABLED:=0}"
export LOCAL_ASR_ENABLE_MODELS LOCAL_ASR_MODEL_CACHE_DIR LOCAL_ASR_STREAMING_CACHE_DIR AMO_LLM_ENABLED
mkdir -p "$LOCAL_ASR_MODEL_CACHE_DIR" "$LOCAL_ASR_STREAMING_CACHE_DIR"

pids=()
shutdown() {
  log "shutting down children"
  for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap shutdown EXIT INT TERM

# ---- 1. 本地 ASR 服务（语音能力；模型关闭时仍以 API 形式可用）----
log "starting local ASR on :9000 (enable_models=$LOCAL_ASR_ENABLE_MODELS)"
(
  cd /app/local_asr
  PYTHONPATH=backend \
  LOCAL_ASR_FRONTEND_ORIGINS='["http://127.0.0.1:7860","http://localhost:7860"]' \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 9000 --log-level info
) &
pids+=("$!")

# ---- 2. 会议 API（核心；MockLLM 或真实 LLM 由环境变量决定）----
log "starting meeting API on :8000 (AMO_LLM_ENABLED=$AMO_LLM_ENABLED)"
(
  cd /app/backend
  PYTHONPATH=. python -m uvicorn app.server:app --host 127.0.0.1 --port 8000 --log-level info
) &
pids+=("$!")

# ---- 3. Nginx（唯一对外入口，必须成功）----
sleep 1
log "starting nginx on :7860"
nginx -g 'daemon off;' &
pids+=("$!")

# 任一子进程退出 -> 终止容器，便于创空间日志暴露故障
wait -n "${pids[@]}"
code=$?
log "a required service exited (code=$code); terminating container"
exit "$code"
