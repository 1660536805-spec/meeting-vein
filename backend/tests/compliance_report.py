"""设计文档合规性核检报告（可独立运行）。

运行：backend> ..\\.venv\\Scripts\\python.exe tests/compliance_report.py

输出一张「设计要点 → 结果」表，并打印整场 mock 会议的端到端流水，最后给出
PASS/FAIL 汇总与标准符合度结论。纯 stdlib，无需联网。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures import (  # noqa: E402
    make_asr_utterances, make_cursor_events, MEETING_ID, MEETING_TITLE,
)
from tests import harness  # noqa: E402
from tests import checks  # noqa: E402


def _print_board(result):
    sa = result["sa"]
    cells = sa.load(MEETING_ID)
    print(f"\n=== 最终看板 cells ({len(cells)}) ===")
    for c in cells:
        d = c.get("data", {})
        print(f"  {c['id']} [{d.get('type')}] {d.get('label')}  refs={d.get('metadata_refs')}")
    print("\n=== metadata_refs 反查（point 节点论据原文）===")
    sb = result["sb"]
    for c in cells:
        if c["id"].startswith("n_p_"):
            refs = c.get("data", {}).get("metadata_refs", [])
            for rec in sb.get_many(refs):
                print(f"  {c['id']} ← {rec['meta_id']}: {rec['text']} ({rec['speaker_ref']})")


def _print_mascot(recorder):
    seq = []
    for _n, _b, a in recorder:
        if a and (not seq or seq[-1] != a):
            seq.append(a)
    print("\n=== MascotState 状态机变迁 ===")
    print("  " + " → ".join(seq))


def main():
    root = tempfile.mkdtemp(prefix="amo_report_")
    result = harness.run_full_meeting(
        root, MEETING_ID, MEETING_TITLE,
        make_asr_utterances(), make_cursor_events(),
    )

    print("=" * 78)
    print("AI_Meeting_Organizer · 端到端测试 + 设计文档合规性核检")
    print("=" * 78)
    print(f"会议：{MEETING_TITLE}  ({MEETING_ID})")
    print(f"输入：ASR {len(make_asr_utterances())} 句（含 1 句纯填充词）+ 光标 "
          f"{len(make_cursor_events())} 事件（hover/drag_node/dblclick）")

    _print_board(result)
    _print_mascot(result["recorder"])

    # ---- 设计要点核检表（evaluate_design_points 已含 DP-01..DP-15 全部要点）----
    dps = checks.evaluate_design_points(result)

    print("\n" + "=" * 78)
    print("设计文档要点核检表")
    print("=" * 78)
    width = 8
    print(f"{'要点':<8} | {'出处':<16} | {'结果':<5} | 检查项")
    print("-" * 78)
    passed = 0
    for d in dps:
        mark = "PASS" if d["passed"] else "FAIL"
        if d["passed"]:
            passed += 1
        print(f"{d['id']:<8} | {d['doc']:<16} | {mark:<5} | {d['title']}")
        print(f"{'':<8} | {'':<16} | {'':<5} | 证据: {d['evidence']}")

    print("-" * 78)
    total = len(dps)
    print(f"汇总：{passed}/{total} 要点符合标准"
          + ("" if passed == total else f"；{total - passed} 项未达标准"))
    print("=" * 78)

    # 标准符合度结论
    if passed == total:
        print("结论：当前 dev 分支产物符合已核检的设计文档要点标准。")
    else:
        print("结论：存在未达标要点（见上表 FAIL）。建议据 evidence 修复后复跑本脚本。")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
