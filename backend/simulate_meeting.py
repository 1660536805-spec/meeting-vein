"""会议节奏模拟器：按真实开会的输入流与停顿，向运行中的后端逐句推送对话，
让前端看板实时生长出思维导图。

与 cli_debug.py（人工逐行键入）的差异：本脚本自动模拟「多人轮流发言 + 自然停顿」：
  · 停顿模型：说完一句后停 base + 句长系数（长句说完歇更久），句尾带 ！/？ 多停 1s，
    换人接话再多停 50%——贴近真实会议的呼吸节奏；
  · 说话人：自动解析种子文件的「名字：内容」行首，按名字生成独立 speaker_ref；
  · 逐句推送（每句驱动一次 analyze+sync，前端 WS 实时渲染）或 --batch N 批量推送
    （N 句合并一次 LLM，模拟语音引擎分窗上传，节奏更快）。

运行（保持 uvicorn 与前端已启动）：
    ..\\.venv\\Scripts\\python.exe simulate_meeting.py                       # 豆腐脑辩论赛，逐句
    ..\\.venv\\Scripts\\python.exe simulate_meeting.py --file tests\\中秋月饼讨论会.txt --batch 3
    ..\\.venv\\Scripts\\python.exe simulate_meeting.py --expert debate       # 绑定辩论专家提示词
    ..\\.venv\\Scripts\\python.exe simulate_meeting.py --speed 4             # 4 倍速快进
    ..\\.venv\\Scripts\\python.exe simulate_meeting.py --dry-run            # 只看节奏不推流

打开 http://127.0.0.1:5173/workspace?meeting_id=<打印的会议id> 实时观看导图生成。
Ctrl-C 随时中断（已推送的句子保留在看板中）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

DEFAULT_BASE = "http://127.0.0.1:8000"
DEFAULT_FILE = "tests/豆腐脑辩论赛.txt"
DEFAULT_SEEDS = ["tests/豆腐脑辩论赛.txt", "tests/中秋月饼讨论会.txt"]
SPEAKER_RE = re.compile(r"^([^：:]{1,12})[：:]\s*(.+)$", re.DOTALL)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="模拟真实开会节奏的实时导图生成演示")
    ap.add_argument("--file", default=None, help=f"种子文本（一行一句，支持「名字：内容」）；默认自动选 {DEFAULT_SEEDS} 中存在的第一个")
    ap.add_argument("--meeting", default=None, help="会议 id（默认 sim_<时间戳> 自动生成）")
    ap.add_argument("--title", default=None, help="会议标题（首次建会用作根节点，默认取文件名去扩展名）")
    ap.add_argument("--base-url", default=DEFAULT_BASE, help=f"后端地址（默认 {DEFAULT_BASE}）")
    ap.add_argument("--expert", default=None, help="会议专家 id：debate/proposal/task/general")
    ap.add_argument("--token", default=None, help="后端配置了 AMO_API_TOKEN 时传入")
    ap.add_argument("--batch", type=int, default=1, help="每 N 句合并一批推送（默认 1=逐句最实时）")
    ap.add_argument("--speed", type=float, default=1.0, help="节奏倍速，>1 快进（默认 1）")
    ap.add_argument("--interval", type=float, default=None,
                    help="固定句间发射间隔秒数（如 0.5=每0.5s推一句）；"
                         "此模式为非阻塞发射——不等 LLM 处理完，后端按会议串行排队消化，"
                         "模拟「人按节奏说话、AI 后台跟进」的真实会议流。未指定时用自然停顿模型（同步等待）。")
    ap.add_argument("--dry-run", action="store_true", help="只打印节奏与说话人，不真实推流")
    return ap.parse_args()


def pick_seed(path: str | None) -> str:
    if path:
        return path
    for cand in DEFAULT_SEEDS:
        try:
            with open(cand, encoding="utf-8") as f:
                if f.read().strip():
                    return cand
        except OSError:
            continue
    sys.exit(f"未找到种子文件，请用 --file 指定（候选：{DEFAULT_SEEDS}）")


def load_lines(path: str) -> list[tuple[str, str]]:
    """返回 [(speaker_name_or_empty, text)]；空行与注释行跳过。"""
    items: list[tuple[str, str]] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = SPEAKER_RE.match(line)
            items.append((m.group(1).strip(), m.group(2).strip()) if m else ("", line))
    if not items:
        sys.exit(f"{path} 没有可用句子")
    return items


def pause_after(text: str, speaker_changed: bool) -> float:
    """自然停顿模型：基础呼吸 + 句长系数 + 感叹/疑问加成 + 换人加成。"""
    p = 0.8 + min(len(text) / 12.0, 6.0)
    if text.rstrip().endswith(("！", "？", "!", "?")):
        p += 1.0
    if speaker_changed:
        p *= 1.5
    return min(p, 10.0)


class Pusher:
    def __init__(self, base: str, token: str | None):
        self.base = base.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(self.base + path,
                                     data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers=self.headers, method="POST")
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    args = parse_args()
    seed = pick_seed(args.file)
    lines = load_lines(seed)
    meeting_id = args.meeting or f"sim_{datetime.now().strftime('%m%d_%H%M%S')}"
    title = args.title or re.split(r"[/\\]", seed)[-1].rsplit(".", 1)[0]
    pusher = None if args.dry_run else Pusher(args.base_url, args.token)

    total = len(lines)
    print(f"会议 id: {meeting_id}")
    print(f"种子: {seed}（{total} 句）  标题: {title}  专家: {args.expert or 'general'}")
    print(f"模式: {'dry-run（不推流）' if args.dry_run else ('逐句' if args.batch == 1 else f'{args.batch} 句/批')}  倍速: {args.speed}")
    if not args.dry_run:
        print(f"前端实时观看: http://127.0.0.1:5173/workspace?meeting_id={meeting_id}")
    print("-" * 72)

    t0 = time.time()
    pushed = 0
    sent = 0
    batch_buf: list[dict] = []
    # --interval 模式：非阻塞发射（线程池后台等响应），节奏独立于 LLM 处理速度
    pool = (ThreadPoolExecutor(max_workers=8)
            if (not args.dry_run and args.batch == 1 and args.interval is not None) else None)

    def push_one(idx: int, speaker: str, text: str) -> None:
        nonlocal pushed
        t = time.time()
        tag = f"{speaker}：{text[:34]}{'…' if len(text) > 34 else ''}"
        try:
            pusher.post("/api/cli/push", {
                "meeting_id": meeting_id, "text": text,
                "speaker_ref": f"cli:user_{speaker}", "display_name": speaker,
                "meeting_title": title, "expert": args.expert,
                "utterance_id": f"{meeting_id}-{idx}",
            })
            pushed += 1
            print(f"          {tag} → ✓ LLM {time.time() - t:.1f}s  ({pushed}/{total})")
        except urllib.error.HTTPError as e:
            pushed += 1
            print(f"          {tag} → !! HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:160]}")
        except Exception as e:                       # 连接失败等网络层错误
            pushed += 1
            print(f"          {tag} → !! {type(e).__name__}: {e}")

    def flush_batch() -> None:
        nonlocal batch_buf, pushed
        if not batch_buf:
            return
        speakers = "、".join(sorted({it['display_name'] for it in batch_buf}))
        t = time.time()
        try:
            pusher.post("/api/cli/push_batch", {
                "meeting_id": meeting_id,
                "items": batch_buf,
                "meeting_title": title,
                "expert": args.expert,
            })
            cost = time.time() - t
            pushed += len(batch_buf)
            print(f"[{time.time() - t0:6.1f}s] 批量推 {len(batch_buf)} 句（{speakers}） → ✓ LLM {cost:.1f}s  ({pushed}/{total})")
        except urllib.error.HTTPError as e:
            print(f"  !! 批量推送失败 {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
            pushed += len(batch_buf)
        batch_buf = []

    try:
        for i, (speaker, text) in enumerate(lines):
            speaker = speaker or "发言人"
            if args.dry_run:
                pass
            elif args.batch > 1:
                batch_buf.append({"text": text, "speaker_ref": f"cli:user_{speaker}",
                                  "display_name": speaker, "utterance_id": f"{meeting_id}-{i}"})
                if len(batch_buf) >= args.batch:
                    flush_batch()
            elif pool is not None:
                sent += 1
                print(f"[{time.time() - t0:6.1f}s] 推入 {sent}/{total}: {speaker}：{text[:30]}{'…' if len(text) > 30 else ''}")
                pool.submit(push_one, i, speaker, text)
            else:
                push_one(i, speaker, text)
            # 节奏：说完这句之后，下一位（可能换人）隔一会才开口
            if i < total - 1:
                if args.interval is not None:
                    pause = args.interval            # 非阻塞发射：间隔为绝对值，不受 --speed 缩放
                else:
                    nxt = lines[i + 1][0] or "发言人"
                    pause = pause_after(text, speaker_changed=nxt != speaker) / max(args.speed, 0.1)
                time.sleep(max(pause, 0.05))
        flush_batch()
        if pool is not None:
            pool.shutdown(wait=True)
    except KeyboardInterrupt:
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        flush_batch()
        print("\n(已中断——已推送的句子与导图变更保留在看板中)")

    print("-" * 72)
    print(f"完成：{pushed}/{total} 句，总耗时 {time.time() - t0:.0f}s")
    if not args.dry_run:
        print(f"看板回放: http://127.0.0.1:5173/workspace?meeting_id={meeting_id}")
        print(f"历史页:   http://127.0.0.1:5173/history?meeting_id={meeting_id}")


if __name__ == "__main__":
    main()
