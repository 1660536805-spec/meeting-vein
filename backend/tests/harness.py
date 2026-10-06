"""端到端测试驱动（harness）。

把 mock 会议对话注入 langgraph 编排的 `BoardAgent`，完整走完：
  ASR 批次：input→filter→(load_board|gen_initial)→assemble_analyze→analyze
            →assemble_sync→sync→parse→update
  光标批次：与 ASR 流在同一 filter 节点收口（原 input_cursor/filter_cursor
            已并入 filter_node，不再单列），平行汇入，不重建图

并「包裹」每个节点方法，记录 (node, mascot_before, mascot_after)，供 MascotState
状态机核检使用。包裹在 BoardAgent 实例方法层完成（langgraph 编译图无法改节点），
随后重新 _build() 让包裹方法进入图。
"""
from __future__ import annotations
import shutil

from app.storage import StoreA, StoreB
from app.llm import MockLLM
from app.orchestrator import BoardAgent, AgentState

_NODE_NAMES = (
    "input", "filter", "load_board", "gen_initial",
    "assemble_analyze", "analyze", "assemble_sync", "sync", "parse", "update",
)


def _wrap_agent(agent: BoardAgent, recorder: list) -> BoardAgent:
    """包裹实例节点方法以记录 mascot_state 变迁，并重新编译图。"""
    for node in _NODE_NAMES:
        attr = f"{node}_node"
        orig = getattr(agent, attr)

        def make_wrap(orig=orig, node=node):
            async def wrap(s):
                before = getattr(s, "mascot_state", None)
                patch = await orig(s)
                # 节点返回 patch 后才被合并进 channel，此刻读 s.mascot_state 会落后
                # 一个节点；优先取 patch 中声明的 mascot_state，未声明回退当前态。
                after = s.mascot_state
                if isinstance(patch, dict) and "mascot_state" in patch:
                    after = patch["mascot_state"]
                recorder.append((node, before, after))
                return patch
            return wrap

        setattr(agent, attr, make_wrap())
    agent.app = agent._build()          # 重新编译，让包裹后的方法进入图
    return agent


def _as_state(result) -> AgentState:
    """ainvoke 结果归一为 AgentState（langgraph 可能返回 dict 或 pydantic 实例）。"""
    if isinstance(result, AgentState):
        return result
    if isinstance(result, dict):
        return AgentState(**result)
    return result


def run_full_meeting(root: str, meeting_id: str, meeting_title: str,
                     asr_utterances, cursor_events, llm=None):
    """跑完整场会议（ASR 批次 + 光标批次），返回运行时产物供核检。

    返回 dict：
      sa / sb        : StoreA / StoreB 实例
      recorder       : [(node, before, after), ...] mascot 变迁全记录
      states         : [st_asr, st_cursor] 每批次结束后的 AgentState
      meeting_id     : 会议 id
    """
    shutil.rmtree(root, ignore_errors=True)
    sa, sb = StoreA(root), StoreB(root)
    llm = llm or MockLLM()
    recorder: list = []

    # —— 预载元数据（Store B：原始语音文本唯一真相源）——
    for u in asr_utterances:
        sb.put(u.utterance_id, {
            "meta_id": u.utterance_id, "kind": "utt", "text": u.text,
            "speaker_ref": u.speaker.speaker_ref,
            "start_offset_ms": u.start_offset_ms, "end_offset_ms": u.end_offset_ms,
            "source": u.source,
        })

    # —— 批次 1：ASR（含填充词 + 实质观点）——
    agent1 = _wrap_agent(BoardAgent(sa, sb, llm), recorder)
    st_asr = _as_state(asyncio_run(
        agent1.run(meeting_id=meeting_id, raw_utterances=asr_utterances,
                   raw_cursor_events=[], meeting_title=meeting_title)))

    # —— 批次 2：光标（平行汇入，不重建图）——
    agent2 = _wrap_agent(BoardAgent(sa, sb, llm), recorder)
    st_cursor = _as_state(asyncio_run(
        agent2.run(meeting_id=meeting_id, raw_utterances=[],
                   raw_cursor_events=cursor_events, meeting_title=None)))

    return {"sa": sa, "sb": sb, "recorder": recorder,
            "states": [st_asr, st_cursor], "meeting_id": meeting_id}


def asyncio_run(coro):
    """asyncio.run 的窄封装（便于将来注入自定义 loop 策略）。"""
    import asyncio
    return asyncio.run(coro)


def mascot_sequence(recorder: list) -> list:
    """从 recorder 提取 mascot_state 变迁序列（取每节点结束后的 after）。"""
    seq = []
    for _node, _before, after in recorder:
        if after and (not seq or seq[-1] != after):
            seq.append(after)
    return seq


def nodes_by_type(cells: list) -> dict:
    """按 data.type 聚合 cell id 列表。"""
    out: dict = {}
    for c in cells:
        t = c.get("data", {}).get("type")
        out.setdefault(t, []).append(c["id"])
    return out
