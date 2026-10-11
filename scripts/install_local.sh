#!/usr/bin/env bash
set -euo pipefail

meeting_root="$(cd "$(dirname "$0")/.." && pwd -P)"
for requirement in uv node npm; do
  if ! command -v "$requirement" >/dev/null 2>&1; then
    echo "缺少 $requirement；请先安装后重试。" >&2
    exit 1
  fi
done

cd "$meeting_root"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
(cd frontend && npm ci)

if [[ "${AMO_INSTALL_ASR:-0}" == "1" ]]; then
  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "语音模式需要 ffmpeg；请安装后重试。" >&2
    exit 1
  fi
  (cd local_asr && uv sync --python 3.12 --extra asr)
fi

echo "安装完成。文字演示：AMO_SKIP_ASR=1 bash scripts/start_local.sh"
if [[ "${AMO_INSTALL_ASR:-0}" == "1" ]]; then
  echo "语音模型首次启动还需下载权重；完整模式：bash scripts/start_local.sh"
fi
