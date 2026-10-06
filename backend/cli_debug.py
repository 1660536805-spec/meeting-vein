"""CLI 调试口：一行键入文本即可驱动双 Agent 管线，无需真实 ASR。

两种模式：
  · 默认（online）：把文本 POST 到运行中的后端 /api/cli/push
        （后端会走 from_cli_text → store_b.put → _drive，并 WS 广播看板更新）
        适合「边输边看前端看板变化」。
  · --offline：本地直接跑 BoardAgent + MockLLM，结果落到独立数据目录并打印
        适合「不依赖后端/前端，单独验证抽取与图同步逻辑」。

运行：
    ..\\.venv\\Scripts\\python.exe cli_debug.py            # 打 localhost:8000
    ..\\.venv\\Scripts\\python.exe cli_debug.py --offline  # 纯本地 Mock 跑
    ..\\.venv\\Scripts\\python.exe cli_debug.py --meeting m1 --speaker cli:user_zhang --name 张三

交互命令（行首冒号）：
    :board            打印当前会议看板的 cells
    :title <标题>     设置/更新会议标题（首次建会用作根节点）
    :meeting <id>     切换到另一个会议（新 id 自动开新会）
    :speaker <ref>    切换说话人 ref（如 cli:user_zhang）
    :name <显示名>    切换说话人显示名
    :reset            清空当前会议看板（仅 offline 模式真正删除；online 提示重启后端）
    :quit             退出

直接输入文本即视为一句发言（utterance），回车发送。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# 确保 backend/ 在 sys.path，使 `import app` 可用（app 包已具备 __init__.py）
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from app.adapters import from_cli_text
from app.orchestrator import BoardAgent
from app.storage import StoreA, StoreB
from app.llm import MockLLM

DEFAULT_URL = "http://localhost:8000"
DEFAULT_MEETING = "mtg_demo"  # 与前端 src/main.ts 的 MEETING_ID 对齐，确保首屏拉到同一会议
OFFLINE_ROOT = os.path.join(_HERE, ".amo_data_cli")


# ---------------------------------------------------------------------------
# online 模式：打后端端点
# ---------------------------------------------------------------------------
def _post(url: str, meeting_id: str, text: str, speaker_ref: str, display_name: str,
          meeting_title) -> dict:
    payload = {
        "meeting_id": meeting_id, "text": text, "speaker_ref": speaker_ref,
        "display_name": display_name, "meeting_title": meeting_title,
    }
    req = urllib.request.Request(
        url + "/api/cli/push",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_board(url: str, meeting_id: str) -> list:
    req = urllib.request.Request(f"{url}/api/board?meeting_id={urllib.parse.quote(meeting_id)}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8")).get("cells", [])


# ---------------------------------------------------------------------------
# offline 模式：本地管线
# ---------------------------------------------------------------------------
class OfflineDriver:
    def __init__(self, root: str):
        self.root = root
        self.store_a = StoreA(root)
        self.store_b = StoreB(root)
        self.agent = BoardAgent(self.store_a, self.store_b, MockLLM())

    def push(self, meeting_id: str, text: str, speaker_ref: str, display_name: str,
             meeting_title) -> list:
        u = from_cli_text(meeting_id, text, speaker_ref=speaker_ref, display_name=display_name)
        self.store_b.put(u.utterance_id, {
            "meta_id": u.utterance_id, "kind": "utt", "text": u.text,
            "speaker_ref": u.speaker.speaker_ref,
            "start_offset_ms": u.start_offset_ms, "end_offset_ms": u.end_offset_ms,
            "source": u.source,
        })
        asyncio.run(self.agent.run(meeting_id, [u], [], meeting_title))
        return self.store_a.load(meeting_id)

    def board(self, meeting_id: str) -> list:
        return self.store_a.load(meeting_id)

    def reset(self, meeting_id: str) -> None:
        p = os.path.join(self.root, f"{meeting_id}.board.json")
        if os.path.exists(p):
            os.remove(p)


# ---------------------------------------------------------------------------
# 展示
# ---------------------------------------------------------------------------
def _print_board(cells: list) -> None:
    if not cells:
        print("  (空看板)")
        return
    nodes = [c for c in cells if c.get("shape") in (None, "amo-node")]
    edges = [c for c in cells if c.get("shape") == "edge"]
    print(f"  cells 总数: {len(cells)}  (节点 {len(nodes)} / 边 {len(edges)})")
    for c in nodes:
        d = c.get("data", {})
        print(f"  • [{d.get('type','?')}] {d.get('label','')}  "
              f"(id={c['id']}, mention={d.get('mention_count',0)})")
    for c in edges:
        d = c.get("data", {})
        print(f"  → {c.get('source')} --{d.get('relation','?')}--> {c.get('target')}")


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------
def repl(mode: str, url: str, meeting_id: str, speaker_ref: str, display_name: str,
         meeting_title):
    driver = OfflineDriver(OFFLINE_ROOT) if mode == "offline" else None
    print("=" * 60)
    print(f"  AI Meeting Organizer · CLI 调试口  [{mode.upper()} 模式]")
    if mode == "online":
        print(f"  后端: {url}   会议: {meeting_id}   说话人: {display_name}({speaker_ref})")
        print("  提示: 后端未启动时本口会报错，可改用 --offline 纯本地跑。")
    else:
        print(f"  数据目录: {OFFLINE_ROOT}   会议: {meeting_id}   说话人: {display_name}({speaker_ref})")
    print("  输入文本=发言；:board/:title/:meeting/:speaker/:name/:reset/:quit 为命令")
    print("=" * 60)

    while True:
        try:
            line = input(f"\n[{mode}] {meeting_id}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n(退出)")
            break
        if not line:
            continue
        if line == ":quit":
            print("再见。")
            break

        # —— 命令 ——
        if line.startswith(":board"):
            cells = _get_board(url, meeting_id) if mode == "online" else driver.board(meeting_id)
            _print_board(cells)
            continue
        if line.startswith(":title "):
            meeting_title = line[7:].strip()
            print(f"  会议标题 → {meeting_title}")
            continue
        if line.startswith(":meeting "):
            meeting_id = line[9:].strip()
            print(f"  切换到会议 → {meeting_id}")
            continue
        if line.startswith(":speaker "):
            speaker_ref = line[9:].strip()
            print(f"  说话人 ref → {speaker_ref}")
            continue
        if line.startswith(":name "):
            display_name = line[6:].strip()
            print(f"  说话人显示名 → {display_name}")
            continue
        if line.startswith(":reset"):
            if mode == "offline":
                driver.reset(meeting_id)
                print(f"  已清空会议 {meeting_id} 看板（offline）")
            else:
                print("  online 模式无法直删后端数据；请重启后端以清空（或 :meeting 切新 id）。")
            continue
        if line.startswith(":"):
            print("  未知命令。可用: :board :title :meeting :speaker :name :reset :quit")
            continue

        # —— 发言 ——
        try:
            if mode == "online":
                resp = _post(url, meeting_id, line, speaker_ref, display_name, meeting_title)
                print(f"  ✓ 已推送 (utterance_id={resp.get('utterance_id')})")
                cells = _get_board(url, meeting_id)
                _print_board(cells)
            else:
                cells = driver.push(meeting_id, line, speaker_ref, display_name, meeting_title)
                _print_board(cells)
        except urllib.error.URLError as e:
            print(f"  ✗ 后端不可达 ({e})。可改用 `cli_debug.py --offline` 纯本地验证。")
        except Exception as e:
            print(f"  ✗ 错误: {type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser(description="AI Meeting Organizer CLI 调试口")
    ap.add_argument("--offline", action="store_true", help="纯本地 Mock 跑管线（不依赖后端）")
    ap.add_argument("--url", default=DEFAULT_URL, help="online 模式后端地址")
    ap.add_argument("--meeting", default=DEFAULT_MEETING, help="会议 id")
    ap.add_argument("--speaker", default="cli:user", help="说话人 ref")
    ap.add_argument("--name", default="CLI调试", help="说话人显示名")
    ap.add_argument("--title", default=None, help="会议标题（首次建会用）")
    args = ap.parse_args()

    repl("offline" if args.offline else "online", args.url, args.meeting,
         args.speaker, args.name, args.title)


if __name__ == "__main__":
    main()
