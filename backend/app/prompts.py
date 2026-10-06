"""从 prompts.yaml 加载双 Agent 提示词与工具 schema，并序列化看板上下文。

- SYSTEM_PROMPT            ：绘图需求描述 + 绘图规范（Design_Agent_DataFlow §2.1 (a)）
- OUTPUT_SCHEMA            ：GraphUpdateOp 输出 schema（5 种 op 字段表 + 受控词表 + few-shot）
- serialize_for_llm(cells) ：关系图压成缩进大纲注入 LLM（Storage §6.2，防上下文膨胀）
- serialize_for_cursor(focus)：光标焦点注记注入提示词（Design_CursorCapture §7 / Research §6）
"""
from __future__ import annotations
import yaml
from pathlib import Path
from typing import List, Optional

from .models import NormCursorEvent

with Path(__file__).with_name("prompts.yaml").open("r", encoding="utf-8") as _file:
    YAML = yaml.safe_load(_file) or {}


def _dig(*path: str):
    node = YAML
    for part in path:
        if not isinstance(node, dict) or part not in node:
            raise RuntimeError(f"prompts.yaml 缺少关键节点 {'.'.join(path)}")
        node = node[part]
    return node

DEFAULT_MEETING_ID = str(_dig("defaults", "meeting_id"))
DEFAULT_MEETING_TITLE = str(_dig("defaults", "meeting_title"))
FILLER_WORDS = tuple(_dig("filter", "filler_words"))
FETCH_METADATA_TOOL = _dig("tools", "fetch_metadata")

SYSTEM_PROMPT = str(_dig("prompts", "syncer_system"))


# 分析 Agent 系统提示（MeetingSummary 输出）。
ANALYZE_SYSTEM_PROMPT = str(_dig("prompts", "analyzer_system"))



OUTPUT_SCHEMA = str(_dig("prompts", "syncer_output_schema"))

EXPERTS = _dig("experts")     # 会议专家技能包：id → {label, prompt}（prompts.yaml）


def expert_addendum(expert_id: Optional[str]) -> str:
    """按所选会议专家返回提示词补充段；未选/未知专家返回空串（回退全能模式）。"""
    if not expert_id:
        return ""
    entry = EXPERTS.get(str(expert_id))
    return str(entry.get("prompt") or "") if isinstance(entry, dict) else ""


MAX_LLM_NODES = 60          # 注入 LLM 的最大节点数（超出按重要度截断，防上下文膨胀）
MAX_LLM_EDGES = 80          # 注入 LLM 的最大边数（超出按端点是否入选截断）
MAX_LLM_REFS = 8            # 每节点最多列出的论据引用数
_IMPORTANCE_RANK = {"high": 2, "normal": 1, "low": 0}


def _estimate_tokens(text: str) -> int:
    """粗估 token 数：CJK 约 1 字/token，其余约 4 字符/token。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4


def _edge_endpoint(endpoint) -> str:
    """边端点兼容 {"cell": id} 与裸字符串两种写法。"""
    if isinstance(endpoint, dict):
        return str(endpoint.get("cell"))
    return str(endpoint)


def serialize_for_llm(cells: list) -> str:
    """把关系图 cells 压成紧凑大纲，供 LLM 提示词（Storage §6.2）。

    P1①（提示词瘦身 + 稳定前缀）：
    - 节点：先按 importance / mention_count 选出 top-N，再按 id 稳定排序输出——
      新内容追加时既有节点相对顺序不变，利于上游 KV 缓存命中，降低长会输入成本；
    - 边：截断到 MAX_LLM_EDGES，并优先保留「两端节点都在入选集内」的关系
      （否则 287 节点会议会把 287 条边全量灌入，是 sync 变慢/膨胀的主因）；
    - 每行压缩：去掉恒为 `-` 的 speaker 字段，mentions 仅在 >1 时输出。
    附 token 估算，便于监控提示词体积。
    """
    if not cells:
        return "(空图：无预载入节点)"

    nodes = [c for c in cells if c.get("shape") != "edge"]
    edges = [c for c in cells if c.get("shape") == "edge"]

    def rank(c: dict):
        d = c.get("data", {})
        lvl = (d.get("importance") or {}).get("level")
        return (_IMPORTANCE_RANK.get(lvl, 1), d.get("mention_count", 0))

    ordered = sorted(nodes, key=rank, reverse=True)
    kept = ordered[:MAX_LLM_NODES]
    dropped = max(0, len(ordered) - MAX_LLM_NODES)
    kept.sort(key=lambda c: str(c.get("id")))          # 稳定前缀：按 id 输出
    kept_ids = {str(c.get("id")) for c in kept}

    lines = []
    for c in kept:
        d = c.get("data", {})
        refs_list = d.get("metadata_refs", []) or []
        refs = ",".join(str(r) for r in refs_list[:MAX_LLM_REFS])
        if len(refs_list) > MAX_LLM_REFS:
            refs += f",+{len(refs_list) - MAX_LLM_REFS}"
        fields = [f"id={c.get('id')}"]
        mentions = d.get("mention_count")
        if mentions and mentions > 1:
            fields.append(f"mentions={mentions}")
        if refs:
            fields.append(f"refs={refs}")
        lines.append(f"- [{d.get('type')}] {d.get('label')} ({', '.join(fields)})")

    body = "\n".join(lines) if lines else "(无节点)"

    if edges:
        triples = [(_edge_endpoint(e.get("source")), _edge_endpoint(e.get("target")),
                    (e.get("data") or {}).get("relation", "?")) for e in edges]
        triples.sort(key=lambda x: (x[0] not in kept_ids, x[1] not in kept_ids, x[0], x[1]))
        dropped_edges = max(0, len(triples) - MAX_LLM_EDGES)
        triples = triples[:MAX_LLM_EDGES]
        if triples:
            body += "\n[关系]\n" + "\n".join(f"- {s} --{rel}--> {t}" for s, t, rel in triples)
        if dropped_edges:
            body += f"\n(已省略 {dropped_edges} 条关系)"
    if dropped:
        body += f"\n(已省略 {dropped} 个低重要度节点)"
    return f"{body}\n(token≈{_estimate_tokens(body)})"


def serialize_for_cursor(focus: List[NormCursorEvent]) -> str:
    """光标焦点注记（Design_CursorCapture §7）。无焦点返回空串。"""
    if not focus:
        return ""
    parts = []
    for e in focus:
        tgt = e.target.node_id if e.target else None
        dur = (e.end_offset_ms - e.start_offset_ms) if (e.end_offset_ms and e.start_offset_ms) else 0
        parts.append(f"- 手势 {e.gesture_type} @ {tgt}（持续 {dur}ms）")
    return "[用户当前焦点 / 光标交互]\n" + "\n".join(parts) + \
        "\n建议：新要点优先挂接至焦点节点附近；请勿移动或改写用户正在操作的节点。"
