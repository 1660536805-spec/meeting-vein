"""LangGraph 编排器 + 双 Agent 节点函数（对齐 Design_Agent_DataFlow.md §2 / Q2）。

- 用真正的 langgraph.graph.StateGraph + MemorySaver checkpointer（per-meeting thread_id）。
- AgentState 用 pydantic BaseModel（langgraph 兼容，字段与 §2 TypedDict 同义）。
- 节点函数为 async (state) -> dict，langgraph 负责合并 patch；业务代码不感知框架。
- Q2 checkpointer：同 thread_id 跨批次恢复 board_graph/board_loaded，route_init 短路跳过 load_board，
  仅冷启动/新会议才从 Store A 载入，零重复往返（checkpointer 为热缓存，Store A 仍作 durability）。
"""
from __future__ import annotations
import asyncio
from typing import Any, Optional
from pydantic import BaseModel, Field

from .models import MeetingSummary, GraphUpdateOp, NormUtterance, NormCursorEvent
from .candidate_validation import (annotate_graph_operations, validate_graph_update,
                                   validate_insights)
from .storage import StoreA, StoreB
from .llm import LLMClient, MockLLM, build_llm
from .tools.metadata_tools import MetadataTools
from . import prompts
from . import config
from .errors import safe_error_code
from .skill_loader import load_syncer_skill
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver


class AgentState(BaseModel):
    """跨节点共享状态（字段对齐 Design_Agent_DataFlow.md §2.1）。"""
    meeting_id: str = ""
    meeting_title: Optional[str] = None
    expert: str = ""                                          # 会议专家技能（prompts.yaml experts），批次级切换

    # —— 输入（双对等流）——
    raw_utterances: list = Field(default_factory=list)       # ASR 流
    raw_cursor_events: list = Field(default_factory=list)    # 光标流（与 ASR 同级）
    filtered_text: list = Field(default_factory=list)        # 过滤后文本
    filtered_meta_ids: list = Field(default_factory=list)    # 过滤句 meta_id
    filtered_focus: list = Field(default_factory=list)       # 过滤聚合后光标焦点

    # —— 关系图 ——
    board_loaded: bool = False
    board_graph: list = Field(default_factory=list)          # Store A cells（checkpointer 热缓存）

    # —— 提示词与推理（双 Agent）——
    system_prompt: str = ""
    llm_messages_analyze: Optional[list] = None
    meeting_summary: Optional[Any] = None                    # MeetingSummary（分析 Agent 输出）
    llm_messages_sync: Optional[list] = None
    llm_output: Optional[Any] = None                          # GraphUpdateOp（规划 Agent 输出）
    parse_ok: bool = False
    retry_count: int = 0

    # —— 输出 ——
    update_patch: Optional[Any] = None
    mascot_state: str = "idle"
    error: Optional[str] = None
    repair_receipt: Optional[dict] = None


class BoardAgent:
    """会议结构图 Agent：持有存储与 LLM，构建并编译 LangGraph。"""

    def __init__(self, store_a: StoreA, store_b: StoreB, llm: LLMClient):
        self.store_a = store_a
        self.store_b = store_b
        self.llm = llm
        # checkpointer 提升为实例字段：每次 _build 复用同一实例，且可被 server 主动回收
        self.checkpointer = MemorySaver()
        self.app = self._build()

    def evict_thread(self, meeting_id: str) -> None:
        """回收某会议的 checkpointer 热缓存（看板已落 Store A，仍可冷启动重载）。

        MemorySaver 按 thread_id 无界累积；会议数增长时需主动淘汰，避免内存单调上涨。
        """
        cp = getattr(self, "checkpointer", None)
        if cp is None:
            return
        for attr in ("storage", "writes", "blobs"):     # 兼容不同 langgraph 版本内部结构
            d = getattr(cp, attr, None)
            if isinstance(d, dict):
                d.pop(meeting_id, None)

    # —— 节点函数（async (state) -> dict）——
    async def input_node(self, s: AgentState) -> dict:
        return {"mascot_state": "listening"}

    async def filter_node(self, s: AgentState) -> dict:
        fillers = config.CONFIG.filler_words
        text, meta = [], []
        for u in s.raw_utterances:
            t = u.text.strip()
            if not t:
                continue
            stripped = t
            for w in fillers:                 # 去掉填充词后无实质内容 → 丢弃（纯规则，Q1）
                stripped = stripped.replace(w, "")
            if len(stripped.strip()) <= 1:
                continue
            text.append(t)
            meta.append(u.utterance_id)
        # 光标流与语音流在同一次过滤中一并收口（原 input_cursor/filter_cursor 节点因无入边而永不执行）
        focus = [e for e in s.raw_cursor_events if e.is_final
                 and (e.gesture_type != "hover" or
                      (e.end_offset_ms or 0) - (e.start_offset_ms or 0)
                      >= config.CONFIG.hover_settle_ms)]
        return {"filtered_text": text, "filtered_meta_ids": meta,
                "filtered_focus": focus, "mascot_state": "filtering"}

    async def load_board_node(self, s: AgentState) -> dict:
        cells = self.store_a.load(s.meeting_id)
        return {"board_graph": cells, "board_loaded": True, "mascot_state": "loading_board"}

    async def gen_initial_node(self, s: AgentState) -> dict:
        from .models import NodeData, make_node_cell
        title = s.meeting_title or self.store_a.title(s.meeting_id) or config.CONFIG.default_meeting_title
        nd = NodeData(type="issue", label=title, metadata_refs=[])
        cell = make_node_cell("n_issue_root", nd, x=300, y=40)
        self.store_a.save(s.meeting_id, [cell])
        return {"board_graph": [cell], "board_loaded": True, "mascot_state": "loading_board"}

    async def assemble_analyze_node(self, s: AgentState) -> dict:
        # checkpointer 已恢复 board_graph 时跳过 Store A 往返（Q2 短路）
        if not s.board_loaded:
            cells = self.store_a.load(s.meeting_id)
            if cells:
                s.board_graph, s.board_loaded = cells, True
            else:
                return await self.gen_initial_node(s)
        focus_note = prompts.serialize_for_cursor(s.filtered_focus)
        system = prompts.ANALYZE_SYSTEM_PROMPT
        addendum = prompts.expert_addendum(s.expert)
        if addendum:
            system += "\n" + addendum
        msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": "[新流入转写]\n" + "\n".join(
                f"{i}. {line}" for i, line in enumerate(s.filtered_text)) +
                ("\n" + focus_note if focus_note else "")},
        ]
        return {"llm_messages_analyze": msgs, "mascot_state": "assembling"}

    async def analyze_node(self, s: AgentState) -> dict:
        # LLM 接口是同步阻塞实现（OpenAI SDK）：放进线程执行，避免卡死事件循环
        try:
            summary = await asyncio.to_thread(
                self.llm.analyze, s.filtered_text, s.filtered_meta_ids,
                s.meeting_title, s.llm_messages_analyze)
        except Exception as exc:
            return {"error": f"llm.analyze failed ({safe_error_code(exc)})", "mascot_state": "error"}
        validation_errors = validate_insights(summary.insights, s.filtered_meta_ids)
        if validation_errors:
            return {"error": "analyzer candidate validation failed", "mascot_state": "error"}
        return {"meeting_summary": summary, "mascot_state": "analyzing"}

    async def assemble_sync_node(self, s: AgentState) -> dict:
        if s.error:
            return {}
        board_summary = prompts.serialize_for_llm(s.board_graph)
        focus_note = prompts.serialize_for_cursor(s.filtered_focus)
        system = prompts.SYSTEM_PROMPT
        skill_add = load_syncer_skill(s.board_graph, s.filtered_focus)   # x6-graph-ops 渐进注入
        if skill_add:
            system += "\n" + skill_add
        addendum = prompts.expert_addendum(s.expert)
        if addendum:
            system += "\n" + addendum
        msgs = [
            {"role": "system", "content": system + "\n" + prompts.OUTPUT_SCHEMA},
            {"role": "user", "content": f"[当前看板]\n{board_summary}"},
            {"role": "user", "content": f"[会议总结 MeetingSummary]\n{s.meeting_summary.thought}\n"
             + "\n".join(f"- {i.type}: {i.summary} (refs={i.evidence}; confidence={i.confidence}; "
                          f"ownership_index={i.ownership_index}; relation={i.relation_to_related}; "
                          f"parent_index={i.parent_index}; "
                          f"importance={i.importance_hint}/{i.importance_rationale})"
                          for i in s.meeting_summary.insights)},
        ]
        if focus_note:
            msgs.append({"role": "user", "content": focus_note})
        return {"llm_messages_sync": msgs, "mascot_state": "assembling"}

    async def sync_node(self, s: AgentState) -> dict:
        if s.error:
            return {}
        # 同上：同步 LLM 调用移出事件循环
        try:
            op = await asyncio.to_thread(
                self.llm.sync, s.meeting_summary, s.board_graph, s.filtered_focus,
                s.llm_messages_sync, self._make_tool_executor())
        except Exception as exc:
            return {"error": f"llm.sync failed ({safe_error_code(exc)})", "mascot_state": "error"}
        return {"llm_output": op, "parse_ok": True, "mascot_state": "syncing"}

    def _make_tool_executor(self):
        metadata = MetadataTools(self.store_a, self.store_b)

        def execute(name: str, args: dict) -> dict:
            if name == "fetch_metadata":
                result = metadata.fetch_metadata(list((args or {}).get("meta_ids") or []))
                return {"ok": result.ok, "records": (result.data or {}).get("records", [])}
            return {"ok": False, "error": f"unknown tool: {name}"}

        return execute

    async def parse_node(self, s: AgentState) -> dict:
        """校验 GraphUpdateOp schema（§2.2）；失败则累计 retry_count，路由决定重试或放弃。"""
        if s.error:
            return {"parse_ok": False, "mascot_state": "error"}
        op = s.llm_output
        ok = bool(op) and hasattr(op, "operations") and isinstance(op.operations, list)
        if ok:
            trusted_refs = {ref for insight in (s.meeting_summary.insights or [])
                            for ref in (insight.evidence or [])}
            validation_errors = validate_graph_update(
                op.operations, s.board_graph, trusted_refs,
                allow_mock_ids=isinstance(self.llm, MockLLM))
            substantive = {"issue", "point", "evidence", "conclusion", "action", "dispute", "question"}
            if not op.operations and any(insight.type in substantive
                                         for insight in (s.meeting_summary.insights or [])):
                validation_errors.append("substantive analysis produced no board operation")
            ok = not validation_errors
        else:
            validation_errors = ["invalid GraphUpdateOp structure"]
        if ok:
            return {"parse_ok": True, "retry_count": 0, "mascot_state": "syncing"}
        retry = s.retry_count + 1
        error = None if retry <= config.CONFIG.max_retry else (
            "sync candidate validation failed: " + "; ".join(validation_errors))
        return {"parse_ok": False, "retry_count": retry, "mascot_state": "analyzing",
                "error": error}

    def _route_parse(self, s: AgentState) -> str:
        """解析失败且未达重试上限 → 回到 sync 重新规划；否则进 update（失败时 update 跳过写回）。

        retry_count 记录累计解析失败次数，允许重试 max_retry 次（即最多 max_retry+1 次 sync）。
        """
        if s.error or s.parse_ok or s.retry_count > config.CONFIG.max_retry:
            return "update"
        return "sync"

    async def update_node(self, s: AgentState) -> dict:
        if s.error:
            return {"mascot_state": "error"}
        if s.parse_ok and s.llm_output:
            annotate_graph_operations(s.llm_output.operations, s.meeting_summary.insights)
            cells, receipt = await asyncio.to_thread(
                self.store_a.commit_graph_update, s.meeting_id, s.llm_output)
            if not receipt["ok"]:
                return {"mascot_state": "error", "error": "; ".join(receipt["errors"]),
                        "repair_receipt": receipt, "board_graph": cells}
            return {"mascot_state": "success", "repair_receipt": receipt,
                    "board_graph": cells}
        return {"mascot_state": "success"}

    # —— 路由 ——
    def _route_init(self, s: AgentState) -> str:
        # checkpointer 已恢复 board_loaded → 跳过 load_board（Q2 短路，零 Store A 往返）
        if s.board_loaded or (self.store_a.exists(s.meeting_id) and bool(self.store_a.load(s.meeting_id))):
            return "load_board"
        return "gen_initial"

    # —— 对外入口 ——
    async def run(self, meeting_id: str, raw_utterances: list, raw_cursor_events: list,
                  meeting_title: Optional[str] = None, expert: Optional[str] = None) -> dict:
        """驱动一场会议的单批次。thread_id = meeting_id 启用跨批次 checkpointer。"""
        initial = {
            "meeting_id": meeting_id,
            "meeting_title": meeting_title,
            "expert": expert or "",
            "raw_utterances": raw_utterances,
            "raw_cursor_events": raw_cursor_events,
            "error": None,
            "repair_receipt": None,
        }
        config = {"configurable": {"thread_id": meeting_id}}
        return await self.app.ainvoke(initial, config=config)

    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("input", self.input_node)
        g.add_node("filter", self.filter_node)
        g.add_node("load_board", self.load_board_node)
        g.add_node("gen_initial", self.gen_initial_node)
        g.add_node("assemble_analyze", self.assemble_analyze_node)
        g.add_node("analyze", self.analyze_node)
        g.add_node("assemble_sync", self.assemble_sync_node)
        g.add_node("sync", self.sync_node)
        g.add_node("parse", self.parse_node)
        g.add_node("update", self.update_node)

        g.set_entry_point("input")
        g.add_edge("input", "filter")
        g.add_conditional_edges("filter", self._route_init,
                                {"load_board": "load_board", "gen_initial": "gen_initial"})
        g.add_edge("load_board", "assemble_analyze")
        g.add_edge("gen_initial", "assemble_analyze")
        g.add_edge("assemble_analyze", "analyze")
        g.add_edge("analyze", "assemble_sync")
        g.add_edge("assemble_sync", "sync")
        g.add_edge("sync", "parse")
        g.add_conditional_edges("parse", self._route_parse,
                                {"update": "update", "sync": "sync"})
        return g.compile(checkpointer=self.checkpointer)
