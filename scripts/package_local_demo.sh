#!/usr/bin/env bash
set -euo pipefail

meeting_root="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$meeting_root"
if [[ -n "$(git status --porcelain)" ]]; then
  echo "请先提交改动，确保发布包与 Git 版本一致。" >&2
  exit 1
fi

revision="$(git rev-parse --short=12 HEAD)"
release_dir="$meeting_root/output/releases"
mkdir -p "$release_dir"
archive="$release_dir/huimai-local-demo-$revision.tar.gz"
git archive --format=tar --prefix=huimai-local-demo/ HEAD | gzip -n > "$archive"
if command -v shasum >/dev/null 2>&1; then
  (cd "$release_dir" && shasum -a 256 "$(basename "$archive")" > "$(basename "$archive").sha256")
else
  (cd "$release_dir" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")
fi
echo "已生成 $archive"
echo "安装：解压后运行 bash scripts/install_local.sh"
echo "启动：AMO_SKIP_ASR=1 bash scripts/start_local.sh"
