#!/usr/bin/env bash
set -euo pipefail

meeting_root="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$meeting_root"
mode="${1:-}"
if [[ -n "$mode" && "$mode" != "--working-tree" ]]; then
  echo "用法：bash scripts/package_local_demo.sh [--working-tree]" >&2
  exit 2
fi

revision="$(git rev-parse --short=12 HEAD)"
release_dir="$meeting_root/output/releases"
mkdir -p "$release_dir"
if [[ "$mode" == "--working-tree" ]]; then
  archive="$release_dir/huimai-local-demo-$revision-working-tree.tar.gz"
  python3 - "$archive" "$revision" <<'PY'
import os
import pathlib
import subprocess
import sys
import tarfile
from io import BytesIO

archive = pathlib.Path(sys.argv[1])
revision = sys.argv[2]
paths = subprocess.check_output([
    "git", "ls-files", "--cached", "--others", "--exclude-standard", "-z",
]).split(b"\0")
root = pathlib.Path.cwd()
prefix = pathlib.PurePosixPath("huimai-local-demo")
manifest = (
    "会脉本机演示包 · 工作区快照\n"
    f"基线提交：{revision}\n"
    "内容：当前已跟踪文件及未忽略的新增文件，包含尚未提交的改动。\n"
    "此快照不等同于基线提交；安装后运行 bash scripts/install_local.sh。\n"
).encode("utf-8")

with tarfile.open(archive, mode="w:gz", compresslevel=9, format=tarfile.PAX_FORMAT) as bundle:
    for raw in paths:
        if not raw:
            continue
        relative = pathlib.Path(os.fsdecode(raw))
        if relative.is_absolute() or ".." in relative.parts:
            raise SystemExit(f"拒绝仓库外路径：{relative}")
        path = root / relative
        if path.is_file() or path.is_symlink():
            bundle.add(path, arcname=(prefix / pathlib.PurePosixPath(relative.as_posix())).as_posix(),
                       recursive=False)
    info = tarfile.TarInfo((prefix / "WORKTREE-INFO.txt").as_posix())
    info.size = len(manifest)
    info.mode = 0o644
    bundle.addfile(info, BytesIO(manifest))
PY
else
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "工作区有未提交改动；要打包当前源码请使用 --working-tree。" >&2
    exit 1
  fi
  archive="$release_dir/huimai-local-demo-$revision.tar.gz"
  git archive --format=tar --prefix=huimai-local-demo/ HEAD | gzip -n > "$archive"
fi
if command -v shasum >/dev/null 2>&1; then
  (cd "$release_dir" && shasum -a 256 "$(basename "$archive")" > "$(basename "$archive").sha256")
else
  (cd "$release_dir" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")
fi
echo "已生成 $archive"
if [[ "$mode" == "--working-tree" ]]; then
  echo "注意：这是包含当前未提交改动的工作区快照。"
fi
echo "安装：解压后运行 bash scripts/install_local.sh"
echo "启动：AMO_SKIP_ASR=1 bash scripts/start_local.sh"
