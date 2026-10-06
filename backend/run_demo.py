r"""端到端验证 demo（纯 stdlib，无需联网）。

驱动：注入 ASR 批次（含填充词 + 真实观点）+ 光标拖拽批次 → 跑 BoardAgent 编排 →
打印最终看板 cells + 验证 metadata_refs 反查原始论据。

运行：backend> ..\.venv\Scripts\python.exe run_demo.py
"""
from __future__ import annotations
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.storage import StoreA, StoreB
from app.llm import MockLLM
from app.orchestrator import BoardAgent
from app.models import NormUtterance, SpeakerRef, NormCursorEvent, ActorRef, CursorTarget


def _mascot(result) -> str:
    """ainvoke 结果兼容：dict（langgraph 默认）或 pydantic state。"""
    return result.get("mascot_state", "?") if isinstance(result, dict) \
        else getattr(result, "mascot_state", "?")


async def main() -> None:
    root = os.path.join(os.path.dirname(__file__), ".amo_data")
    import shutil
    shutil.rmtree(root, ignore_errors=True)   # demo 确定性：清空旧运行时数据
    sa, sb = StoreA(root), StoreB(root)
    agent = BoardAgent(sa, sb, MockLLM())
    mid = "mtg_demo"

    # —— 批次 1：ASR 三句（含填充词 u2 应被过滤）——
    u1 = NormUtterance(utterance_id="utt_1", meeting_id=mid, session_id=None, seq=1,
        speaker=SpeakerRef(speaker_ref="ent:user_zhang", display_name="张三", is_resolved=True),
        text="我们应该在 Q3 上线渠道自助分析功能", language="zh",
        start_offset_ms=1000, end_offset_ms=3000, received_at_ms=0,
        is_final=True, is_partial=False, source="tencent_meeting")
    u2 = NormUtterance(utterance_id="utt_2", meeting_id=mid, session_id=None, seq=2,
        speaker=SpeakerRef(speaker_ref="ent:user_li", display_name="李四", is_resolved=True),
        text="嗯 那个 对吧", language="zh",
        start_offset_ms=3100, end_offset_ms=3500, received_at_ms=0,
        is_final=True, is_partial=False, source="tencent_meeting")
    u3 = NormUtterance(utterance_id="utt_3", meeting_id=mid, session_id=None, seq=3,
        speaker=SpeakerRef(speaker_ref="ent:user_zhang", display_name="张三", is_resolved=True),
        text="数据口径要对齐，否则报表会错", language="zh",
        start_offset_ms=3600, end_offset_ms=5200, received_at_ms=0,
        is_final=True, is_partial=False, source="tencent_meeting")

    for u in (u1, u2, u3):
        sb.put(u.utterance_id, {"meta_id": u.utterance_id, "kind": "utt", "text": u.text,
                 "speaker_ref": u.speaker.speaker_ref,
                 "start_offset_ms": u.start_offset_ms, "end_offset_ms": u.end_offset_ms,
                 "source": u.source})

    res = await agent.run(mid, [u1, u2, u3], [], "Q3 渠道策略会")
    print(f"[批次1 ASR] 完成 → mascot_state = {_mascot(res)}")

    # —— 批次 2：光标拖拽 n_issue_root（验证 focus 注入 + 软保护）——
    c1 = NormCursorEvent(event_id="c_1", meeting_id=mid, session_id=None, seq=4,
        actor=ActorRef(user_ref="ent:user_zhang"), gesture_type="drag_node",
        target=CursorTarget(node_id="n_issue_root"), pointer={"x": 300, "y": 40},
        start_offset_ms=6000, end_offset_ms=7200, received_at_ms=0,
        is_final=True, is_partial=False)
    res2 = await agent.run(mid, [], [c1])
    print(f"[批次2 光标] 完成 → mascot_state = {_mascot(res2)}")

    # —— 打印最终看板 ——
    cells = sa.load(mid)
    print(f"\n=== 最终看板 cells ({len(cells)}) ===")
    for c in cells:
        d = c.get("data", {})
        print(f"  {c['id']} [{d.get('type')}] {d.get('label')}  refs={d.get('metadata_refs')}")

    # —— 验证 metadata_refs 反查（节点 → 原始论据）——
    print("\n=== metadata_refs 反查（point 节点的论据原文）===")
    for c in cells:
        if c["id"].startswith("n_p_"):
            refs = c.get("data", {}).get("metadata_refs", [])
            for rec in sb.get_many(refs):
                print(f"  {c['id']} ← {rec['meta_id']}: {rec['text']} ({rec['speaker_ref']})")


if __name__ == "__main__":
    asyncio.run(main())
