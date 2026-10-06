"""看板实时更新录制器：全新会议 + simulate_meeting.py 推流 + playwright 录屏，
产出约 60 秒的 mp4，记录思维导图随会议推进逐步生长的过程。

前置：uvicorn(8000) 与 vite(5173) 已启动；venv 已安装 playwright + imageio-ffmpeg；
      系统装有 Chrome（channel="chrome"，免下载浏览器）。

运行（在 backend 目录）：
    ..\\.venv\\Scripts\\python.exe record_board_video.py
    ..\\.venv\\Scripts\\python.exe record_board_video.py --seconds 90 --batch 3
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
WORKSPACE_URL = "http://127.0.0.1:5173/workspace?meeting_id={mid}"
DEFAULT_OUT = ROOT / "board_recording_mooncake_60s.mp4"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="录制看板实时生长 60 秒视频")
    ap.add_argument("--file", default="tests/中秋月饼讨论会.txt")
    ap.add_argument("--title", default="中秋月饼讨论会")
    ap.add_argument("--meeting", default=None, help="会议 id（默认 rec_<时间戳>）")
    ap.add_argument("--batch", type=int, default=2, help="推流批次大小（默认 2 句/批）")
    ap.add_argument("--interval", type=float, default=None,
                    help="单句非阻塞发射间隔秒数（如 3=每3s推一句，batch 参数被忽略）")
    ap.add_argument("--seconds", type=float, default=60.0, help="录制总时长（默认 60s）")
    ap.add_argument("--settle", type=float, default=4.0,
                    help="开页后到开始推流的留空时间（默认 4s，展示空白看板）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出 mp4 路径")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=900)
    return ap.parse_args()


def convert_to_mp4(webm: Path, mp4: Path) -> None:
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [ff, "-y", "-i", str(webm), "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)],
        check=True, capture_output=True)


def main() -> None:
    args = parse_args()
    from playwright.sync_api import sync_playwright

    meeting_id = args.meeting or f"rec_{datetime.now().strftime('%m%d_%H%M%S')}"
    url = WORKSPACE_URL.format(mid=meeting_id)
    video_dir = BACKEND / "_rec_video"
    video_dir.mkdir(exist_ok=True)

    print(f"会议 id : {meeting_id}")
    print(f"页面    : {url}")
    print(f"录制    : {args.seconds:.0f}s @ {args.width}x{args.height}，留空 {args.settle:.0f}s 后开始推流")
    print("-" * 72)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": args.width, "height": args.height},
            record_video_dir=str(video_dir),
            record_video_size={"width": args.width, "height": args.height},
        )
        page = context.new_page()
        t0 = time.time()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector("#board, canvas, svg", timeout=15000)

        time.sleep(max(args.settle, 0))
        print(f"[{time.time() - t0:5.1f}s] 启动推流（batch={args.batch}）")

        sim_args = [str(VENV_PY), "simulate_meeting.py",
                    "--file", args.file, "--title", args.title,
                    "--meeting", meeting_id]
        if args.interval is not None:
            sim_args += ["--interval", str(args.interval)]
        else:
            sim_args += ["--batch", str(args.batch)]
        pusher = subprocess.Popen(sim_args, cwd=str(BACKEND),
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace")

        while time.time() - t0 < args.seconds:
            time.sleep(0.5)
        print(f"[{time.time() - t0:5.1f}s] 录制结束，停止推流")

        pusher.terminate()
        try:
            out = pusher.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            pusher.kill()
            out = pusher.communicate()[0]
        print("--- 推流脚本输出（尾部） ---")
        print("\n".join(out.strip().splitlines()[-12:]))
        print("---------------------------")

        video_path = page.video.path()          # 关 context 前拿到路径
        context.close()
        browser.close()

    webm = Path(video_path)
    mp4 = Path(args.out)
    print(f"转换 {webm.name} → {mp4.name}")
    convert_to_mp4(webm, mp4)
    webm.unlink(missing_ok=True)
    video_dir.rmdir()

    print("-" * 72)
    print(f"完成: {mp4}  ({mp4.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"看板回放: http://127.0.0.1:5173/workspace?meeting_id={meeting_id}")


if __name__ == "__main__":
    sys.exit(main())
