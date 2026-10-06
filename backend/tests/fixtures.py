"""Mock 会议对话 fixture（端到端测试用）。

模拟一场「Q3 渠道策略复盘会」，覆盖：
- 多说话人（张三 / 李四 / 王五），含 ent: 前缀可解析标识；
- 填充词无效句（应被 filter_node 丢弃）；
- 实质观点 / 行动项 / 分歧（conflict）等多样内容；
- 议程标题 → 初始 issue 节点（gen_initial）；
- 光标流：hover / drag_node（命中节点，触发软保护）/ dblclick（查论据）。

ASR 流与光标流均归一为统一输入结构，供编排器双流平行摄入。
"""
from app.models import NormUtterance, SpeakerRef, NormCursorEvent, ActorRef, CursorTarget

MEETING_ID = "mtg_q3_20260920"
MEETING_TITLE = "Q3 渠道策略复盘会"


def make_asr_utterances():
    """返回 ASR 统一流（多说话人 + 填充词 + 多样语义）。"""
    return [
        NormUtterance(
            utterance_id="utt_1", meeting_id=MEETING_ID, session_id=None, seq=1,
            speaker=SpeakerRef(speaker_ref="ent:user_zhang", display_name="张三", is_resolved=True),
            text="我们应该在 Q3 上线渠道自助分析功能", language="zh",
            start_offset_ms=1000, end_offset_ms=3000, received_at_ms=0,
            is_final=True, is_partial=False, source="tencent_meeting",
        ),
        NormUtterance(
            utterance_id="utt_2", meeting_id=MEETING_ID, session_id=None, seq=2,
            speaker=SpeakerRef(speaker_ref="ent:user_li", display_name="李四", is_resolved=True),
            text="嗯 那个 对吧", language="zh",
            start_offset_ms=3100, end_offset_ms=3500, received_at_ms=0,
            is_final=True, is_partial=False, source="tencent_meeting",
            # ↑ 纯填充词，应被 filter_node 丢弃
        ),
        NormUtterance(
            utterance_id="utt_3", meeting_id=MEETING_ID, session_id=None, seq=3,
            speaker=SpeakerRef(speaker_ref="ent:user_zhang", display_name="张三", is_resolved=True),
            text="数据口径要对齐，否则报表会错", language="zh",
            start_offset_ms=3600, end_offset_ms=5200, received_at_ms=0,
            is_final=True, is_partial=False, source="tencent_meeting",
        ),
        NormUtterance(
            utterance_id="utt_4", meeting_id=MEETING_ID, session_id=None, seq=4,
            speaker=SpeakerRef(speaker_ref="ent:user_wang", display_name="王五", is_resolved=True),
            text="我下周把口径对齐的脚本写完，先给张三 review", language="zh",
            start_offset_ms=5300, end_offset_ms=7600, received_at_ms=0,
            is_final=True, is_partial=False, source="tencent_meeting",
            # ↑ 行动项（action）
        ),
        NormUtterance(
            utterance_id="utt_5", meeting_id=MEETING_ID, session_id=None, seq=5,
            speaker=SpeakerRef(speaker_ref="ent:user_li", display_name="李四", is_resolved=True),
            text="但我觉得自助分析优先级没那么高，先保增长", language="zh",
            start_offset_ms=7700, end_offset_ms=10200, received_at_ms=0,
            is_final=True, is_partial=False, source="tencent_meeting",
            # ↑ 与 utt_1 形成分歧（conflict）
        ),
    ]


def make_cursor_events():
    """返回光标统一流（hover / drag_node / dblclick）。"""
    return [
        NormCursorEvent(
            event_id="c_1", meeting_id=MEETING_ID, session_id=None, seq=6,
            actor=ActorRef(user_ref="ent:user_zhang"), gesture_type="hover",
            target=CursorTarget(node_id="n_issue_root"), pointer={"x": 300, "y": 40},
            start_offset_ms=2000, end_offset_ms=2200, received_at_ms=0,
            is_final=True, is_partial=False,
        ),
        NormCursorEvent(
            event_id="c_2", meeting_id=MEETING_ID, session_id=None, seq=7,
            actor=ActorRef(user_ref="ent:user_zhang"), gesture_type="drag_node",
            target=CursorTarget(node_id="n_issue_root"), pointer={"x": 300, "y": 55},
            start_offset_ms=6000, end_offset_ms=7200, received_at_ms=0,
            is_final=True, is_partial=False,
            # ↑ 命中 n_issue_root，触发 update_node 软保护（不写回该节点）
        ),
        NormCursorEvent(
            event_id="c_3", meeting_id=MEETING_ID, session_id=None, seq=8,
            actor=ActorRef(user_ref="ent:user_li"), gesture_type="dblclick",
            target=CursorTarget(node_id="n_p_xxxx"), pointer={"x": 412, "y": 268},
            intent_hint="查看论据", start_offset_ms=8000, end_offset_ms=8050,
            received_at_ms=0, is_final=True, is_partial=False,
        ),
    ]
