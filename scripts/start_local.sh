#!/usr/bin/env bash
set -euo pipefail

meeting_root="$(cd "$(dirname "$0")/.." && pwd -P)"
board_python="$meeting_root/.venv/bin/python"
asr_python="$meeting_root/local_asr/.venv/bin/python"

for requirement in ffmpeg node npm lsof; do
  if ! command -v "$requirement" >/dev/null 2>&1; then
    echo "缺少 $requirement；请先安装后再启动。" >&2
    exit 1
  fi
done
for executable in "$board_python" "$asr_python"; do
  if [[ ! -x "$executable" ]]; then
    echo "缺少 Python 环境：$executable；请按 README 安装依赖。" >&2
    exit 1
  fi
done
if [[ ! -x "$meeting_root/frontend/node_modules/.bin/vite" ]]; then
  echo "缺少前端依赖；请先在 frontend 运行 npm ci。" >&2
  exit 1
fi
for port in 5173 8000 9000; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null; then
    echo "端口 $port 已被占用；请先停止对应服务。" >&2
    exit 1
  fi
done

children=()
cleanup() {
  for child in "${children[@]}"; do kill "$child" 2>/dev/null || true; done
  for child in "${children[@]}"; do wait "$child" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

(cd "$meeting_root/backend" && exec env PYTHONPATH=. "$board_python" -m uvicorn app.server:app --host 127.0.0.1 --port 8000) &
children+=("$!")
(cd "$meeting_root/local_asr" && exec env PYTHONPATH=backend "$asr_python" -m uvicorn app.main:app --host 127.0.0.1 --port 9000) &
children+=("$!")
(cd "$meeting_root/frontend" && exec ./node_modules/.bin/vite --host 127.0.0.1 --strictPort) &
children+=("$!")

echo "会议看板：http://127.0.0.1:5173/"
echo "看板 API：http://127.0.0.1:8000/api/status"
echo "本地 ASR：http://127.0.0.1:9000/models/status"
wait "${children[@]}"
