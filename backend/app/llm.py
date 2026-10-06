"""统一 LLM 接入接口（Design §6.2：OpenAI 兼容、模型可插拔）。

MVP 用 MockLLM 跑通编排；生产替换为 OpenAIClient（相同签名，模型由配置指定）。
接口只暴露两个语义方法：analyze（分析 Agent）/ sync（图同步 Agent）。

连接方式：在 .env 中置 AMO_LLM_ENABLED=1 并填 OPENAI_API_KEY/OPENAI_BASE_URL/OPENAI_MODEL，
server 启动时 build_llm() 自动切换；未启用真实模型时使用 MockLLM。
真实模型调用或解析失败会交给编排器停止本批更新，保留最后一版看板。
"""
from __future__ import annotations
import json
import logging
import re
import threading
import time
from typing import Callable, Dict, List, Optional

from .models import MeetingSummary, InsightRecord, GraphUpdateOp, GraphOp, stable_hash
from . import config


log = logging.getLogger("amo.llm")


class LLMError(RuntimeError):
    """真实模型调用或结构化解析失败；交给编排器保留最后一版看板。"""


# ---------------------------------------------------------------------------
# 可观测性：线程安全的调用指标（P0③）
# 由 _completion 单一入口累加，/api/status 快照暴露，无需引入 prometheus 依赖。
# ---------------------------------------------------------------------------
class LLMMetrics:
    """LLM 调用累计指标：调用数/成功/失败/尝试/重试/超时/熔断/延迟。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.calls = 0            # 顶层调用次数（analyze/sync 各算一次）
        self.ok = 0               # 成功返回次数
        self.failed = 0           # 重试耗尽后失败次数
        self.attempts = 0         # 实际发起的 HTTP 尝试次数
        self.retries = 0          # 触发重试的次数
        self.timeouts = 0         # 超时类错误次数
        self.circuit_trips = 0    # 熔断打开次数
        self.latency_ms_total = 0.0   # 成功调用累计延迟
        self.latency_ms_max = 0.0     # 峰值延迟
        self.last_error: Optional[str] = None

    def snapshot(self) -> dict:
        with self._lock:
            avg = round(self.latency_ms_total / self.ok, 1) if self.ok else 0.0
            return {
                "calls": self.calls, "ok": self.ok, "failed": self.failed,
                "attempts": self.attempts, "retries": self.retries,
                "timeouts": self.timeouts, "circuit_trips": self.circuit_trips,
                "latency_ms_avg": avg, "latency_ms_max": round(self.latency_ms_max, 1),
                "last_error": self.last_error,
            }

    def _bump(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, getattr(self, k) + v)

    def on_call(self) -> None:
        self._bump(calls=1)

    def on_attempt(self) -> None:
        self._bump(attempts=1)

    def on_retry(self) -> None:
        self._bump(retries=1)

    def on_timeout(self, err) -> None:
        with self._lock:
            self.timeouts += 1
            self.last_error = str(err)[:200]

    def on_circuit_trip(self) -> None:
        self._bump(circuit_trips=1)

    def on_ok(self, latency_ms: float) -> None:
        with self._lock:
            self.ok += 1
            self.latency_ms_total += latency_ms
            if latency_ms > self.latency_ms_max:
                self.latency_ms_max = latency_ms

    def on_fail(self, err) -> None:
        with self._lock:
            self.failed += 1
            self.last_error = str(err)[:200]

    def reset(self) -> None:
        with self._lock:
            self.__init__()


_METRICS = LLMMetrics()


def llm_metrics() -> dict:
    """返回 LLM 指标快照（供 /api/status 使用）。"""
    return _METRICS.snapshot()


def _is_timeout(err) -> bool:
    """判定是否为超时类错误（openai.APITimeoutError / 内建 TimeoutError）。"""
    return isinstance(err, TimeoutError) or "timeout" in type(err).__name__.lower()


# ---------------------------------------------------------------------------
# 上游熔断（P2②）：连续失败达阈值即 open，cooldown 内快速失败，成功一次即复位。
# ---------------------------------------------------------------------------
class _CircuitBreaker:
    """进程级熔断器：保护编排管线不被持续失败的上游拖垮（快速失败而非排队重试）。"""

    def __init__(self, threshold: int, cooldown_s: float):
        self._lock = threading.Lock()
        self.threshold = max(1, int(threshold))
        self.cooldown_s = max(0.0, float(cooldown_s))
        self.failures = 0
        self.opened_at = 0.0
        self.open = False

    def allow(self) -> bool:
        """熔断打开且仍在 cooldown 内 → 拒绝；否则放行（含 cooldown 到期后的半开试探）。"""
        with self._lock:
            if not self.open:
                return True
            if time.monotonic() - self.opened_at >= self.cooldown_s:
                self.opened_at = time.monotonic()   # 半开：放行一次，失败则重新计时
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.open = False

    def record_failure(self) -> bool:
        """记录一次失败；返回本次是否「首次触发」熔断打开。"""
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold and not self.open:
                self.open = True
                self.opened_at = time.monotonic()
                return True
            return False

    def snapshot(self) -> dict:
        with self._lock:
            return {"open": self.open, "failures": self.failures,
                    "threshold": self.threshold, "cooldown_s": self.cooldown_s}


_BREAKER: Optional[_CircuitBreaker] = None


def _get_breaker() -> _CircuitBreaker:
    """取熔断器；配置经 apply_overrides 变更时按新阈值/冷却重建。"""
    global _BREAKER
    cfg = config.CONFIG
    thr = max(1, cfg.llm_circuit_threshold)
    cd = max(0.0, cfg.llm_circuit_cooldown_s)
    if _BREAKER is None or _BREAKER.threshold != thr or _BREAKER.cooldown_s != cd:
        _BREAKER = _CircuitBreaker(thr, cd)
    return _BREAKER


def adaptive_max_tokens(units: int) -> int:
    """按本批规模估算输出预算（P1③）：base + units×per_unit，夹紧到 [base, cap]。

    units：analyze 传本批句数、sync 传本批 insights 数；规模越大给越多预算，
    避免长批次输出被截断、短批次浪费配额。
    """
    cfg = config.CONFIG
    base = max(64, cfg.llm_max_tokens_base)
    per = max(0, cfg.llm_max_tokens_per_unit)
    cap = max(base, cfg.llm_max_tokens_cap)
    try:
        units = int(units)
    except (TypeError, ValueError):
        units = 0
    return max(base, min(cap, base + max(0, units) * per))


class LLMClient:
    """统一 LLM 接入接口。所有调用经此抽象，业务代码不感知具体模型。"""

    def analyze(self, filtered_text: List[str], filtered_meta_ids: List[str],
                meeting_title: Optional[str], messages: Optional[list] = None) -> MeetingSummary:
        raise NotImplementedError

    def sync(self, summary: MeetingSummary, board_cells: list,
             focus: list, messages: Optional[list] = None,
             tool_executor: Optional[Callable[[str, Dict], dict]] = None) -> GraphUpdateOp:
        raise NotImplementedError


class MockLLM(LLMClient):
    """规则驱动的 Mock：把过滤文本逐句变 point，图摘要变 add_node/link。仅用于 MVP 验证。"""

    def analyze(self, filtered_text: List[str], filtered_meta_ids: List[str],
                meeting_title: Optional[str], messages: Optional[list] = None) -> MeetingSummary:
        return _mock_analyze(filtered_text, filtered_meta_ids, meeting_title)

    def sync(self, summary: MeetingSummary, board_cells: list, focus: list,
             messages: Optional[list] = None,
             tool_executor: Optional[Callable[[str, Dict], dict]] = None) -> GraphUpdateOp:
        return _mock_sync(summary, board_cells)


# ---------------------------------------------------------------------------
# 工具：从模型输出里抠出第一个 JSON 对象（兼容 ```json 包裹 / 前缀杂文）
# ---------------------------------------------------------------------------
def _extract_json(text: str):
    """从模型输出里抠出第一个完整 JSON 对象（兼容 ```json 围栏 / 前缀杂文）。

    用 raw_decode 从头扫完整 JSON 值：不像正则 `\\{.*?\\}` 那样在嵌套对象上被第一个 `}` 截断，
    也不像 find/rfind 那样把尾部杂文里的 `}` 误并进来。
    """
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    if start == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(candidate[start:])
        return obj
    except Exception:
        return None


def _clamp01(v, default: float = 0.7) -> float:
    """把模型给的置信度夹紧到 [0,1]：LLM 常返回 1.2 / -0.3 等越界值。"""
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 真实客户端：OpenAI 兼容
# ---------------------------------------------------------------------------
class OpenAIClient(LLMClient):
    """OpenAI 兼容真实客户端。与 MockLLM 同签名（同步），业务代码无感切换。

    支持官方 OpenAI / Azure / 本地 vLLM / 腾讯混元 等任意 OpenAI 兼容端点。
    """

    def __init__(self):
        cfg = config.CONFIG
        self.model = cfg.llm_model
        self.temperature = cfg.llm_temperature
        self.timeout = cfg.llm_timeout
        self.max_tokens = cfg.llm_max_tokens
        self.max_retries = max(0, cfg.llm_max_retries)
        from openai import OpenAI
        # api_key 为空时给占位，避免 openai 抛缺 key（兼容无 key 的本地端点）
        self._client = OpenAI(
            api_key=cfg.llm_api_key or "EMPTY",
            base_url=cfg.llm_base_url,
            timeout=cfg.llm_timeout,
            max_retries=0,  # 关闭 SDK 内部重试（默认 2）：与 _completion 的重试循环叠加会把单次调用放大到 9 次 HTTP
        )

    def _completion(self, messages, **options):
        """调用 chat completions；网络错误/限流/空响应按配置重试，解析错误由调用方处理。

        端点偶发返回 200 但 content 为空（抖动），同样视为失败重试；
        tool_calls 轮次无 content 属正常，豁免空检测。
        总预算封顶：单次调用（含全部重试）的墙钟上限 = timeout × (max_retries + 1)，
        每次尝试的超时取「剩余预算」，避免上游持续超时时重试风暴把单次调用拖长。
        熔断（P2②）：上游连续失败达阈值即 open，cooldown 内快速失败；成功一次即复位。
        指标（P0③）：调用/尝试/重试/超时/成败/延迟全部记入 _METRICS。
        max_tokens（P1③）：可由调用方按批规模传入，缺省用配置基线。
        """
        max_tokens = options.pop("max_tokens", self.max_tokens)
        metrics = _METRICS
        metrics.on_call()
        breaker = _get_breaker()
        if not breaker.allow():
            metrics.on_circuit_trip()
            raise LLMError("LLM circuit open: upstream failing, fast-fail during cooldown")
        last_exc: Optional[Exception] = None
        started = time.monotonic()
        deadline = started + self.timeout * (self.max_retries + 1)
        for attempt in range(self.max_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 1:                 # 预算耗尽：不再发起注定超时的尝试
                break
            metrics.on_attempt()
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=max_tokens,
                    timeout=round(remaining, 2),
                    **options,
                )
                msg = resp.choices[0].message
                has_tools = bool(getattr(msg, "tool_calls", None))
                if not has_tools and not (getattr(msg, "content", "") or "").strip():
                    raise RuntimeError("empty completion content (endpoint hiccup)")
                breaker.record_success()
                metrics.on_ok((time.monotonic() - started) * 1000.0)
                return msg
            except Exception as e:
                last_exc = e
                if _is_timeout(e):
                    metrics.on_timeout(e)
                if attempt < self.max_retries:
                    metrics.on_retry()
                    backoff = min(0.5 * (2 ** attempt), 4.0)
                    if time.monotonic() + backoff >= deadline:
                        break
                    log.warning("[llm] chat attempt %d failed (%r), retry in %.1fs",
                                attempt + 1, e, backoff)
                    time.sleep(backoff)
        assert last_exc is not None
        if breaker.record_failure():
            metrics.on_circuit_trip()
            log.warning("[llm] circuit opened after %d consecutive failures", breaker.failures)
        metrics.on_fail(last_exc)
        raise LLMError(f"LLM request failed: {last_exc}") from last_exc

    def _chat(self, messages, *, response_format=None, max_tokens=None) -> str:
        options = {"response_format": response_format} if response_format else {}
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        return self._completion(messages, **options).content or ""

    def analyze(self, filtered_text: List[str], filtered_meta_ids: List[str],
                meeting_title: Optional[str], messages: Optional[list] = None) -> MeetingSummary:
        from . import prompts
        if not filtered_text:
            return MeetingSummary(meeting_id="?", meeting_title=meeting_title, insights=[])
        sys_p = prompts.ANALYZE_SYSTEM_PROMPT
        user_p = "\n".join(f"{i}. {t}" for i, t in enumerate(filtered_text))
        base = messages or [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": f"[会议标题] {meeting_title or '未命名'}\n[转写]\n{user_p}"},
        ]
        data = None
        for attempt in range(2):   # 端点偶发截断（JSON 半途而废）与空串同属 hiccup：重试一次
            raw = self._chat(base, response_format={"type": "json_object"},
                             max_tokens=adaptive_max_tokens(len(filtered_text)))
            data = _extract_json(raw)
            if isinstance(data, dict):
                break
            log.warning("[llm] analyze attempt %d invalid JSON, retry", attempt + 1)
        if not isinstance(data, dict):
            raise LLMError(f"analyze: invalid JSON output: {raw[:200]!r}")
        try:
            insights = []
            for it in data.get("insights", []):
                idx = it.get("evidence_index")
                ev = [filtered_meta_ids[idx]] if isinstance(idx, int) and 0 <= idx < len(filtered_meta_ids) else []
                insights.append(InsightRecord(
                    type=str(it.get("type", "point")),
                    summary=str(it.get("summary", "")).strip(),
                    confidence=_clamp01(it.get("confidence", 0.7)),
                    importance_hint=it.get("importance_hint"),
                    evidence=ev,
                ))
            if not insights:
                raise LLMError("analyze: non-empty input produced no insights")
            return MeetingSummary(meeting_id="?", meeting_title=meeting_title, insights=insights,
                                  thought=data.get("thought", "[llm] 结构化抽取"))
        except (TypeError, ValueError, KeyError) as e:
            raise LLMError(f"analyze: invalid structured output: {e}") from e

    def sync(self, summary: MeetingSummary, board_cells: list, focus: list,
             messages: Optional[list] = None,
             tool_executor: Optional[Callable[[str, Dict], dict]] = None) -> GraphUpdateOp:
        from . import prompts
        board_summary = prompts.serialize_for_llm(board_cells)
        ins_lines = "\n".join(f"- {i.type}: {i.summary} (refs={i.evidence})" for i in summary.insights)
        focus_note = prompts.serialize_for_cursor(focus) if focus else ""
        sys_p = (prompts.SYSTEM_PROMPT + "\n" + prompts.OUTPUT_SCHEMA)
        base = messages or [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": f"[当前看板]\n{board_summary}\n[会议总结]\n{ins_lines}\n{focus_note}"},
        ]
        data = None
        raw = ""
        for attempt in range(2):   # 端点偶发截断/把 tool_calls 内联进 content 同属 hiccup：重试一次
            conversation = list(base)
            raw = ""
            for _ in range(2):
                options = {"response_format": {"type": "json_object"},
                           "max_tokens": adaptive_max_tokens(len(summary.insights))}
                if tool_executor:
                    options["tools"] = [prompts.FETCH_METADATA_TOOL]
                message = self._completion(conversation, **options)
                calls = getattr(message, "tool_calls", None)
                if not (tool_executor and calls):
                    raw = message.content or ""
                    break
                conversation.append({"role": "assistant", "content": message.content or "",
                                     "tool_calls": [{"id": call.id, "type": "function",
                                                     "function": {"name": call.function.name,
                                                                  "arguments": call.function.arguments or "{}"}}
                                                    for call in calls]})
                for call in calls:
                    args = _extract_json(call.function.arguments or "{}")
                    result = tool_executor(call.function.name, args if isinstance(args, dict) else {})
                    conversation.append({"role": "tool", "tool_call_id": call.id,
                                         "content": json.dumps(result, ensure_ascii=False)})
            else:
                raise LLMError("sync: tool loop exhausted")
            data = _extract_json(raw)
            if isinstance(data, dict) and isinstance(data.get("operations"), list):
                break
            log.warning("[llm] sync attempt %d invalid JSON, retry", attempt + 1)
        if not isinstance(data, dict) or not isinstance(data.get("operations"), list):
            raise LLMError(f"sync: invalid JSON output: {raw[:200]!r}")
        try:
            ops = []
            for op in data.get("operations", []):
                ops.append(GraphOp(
                    op=str(op.get("op", "add_node")),
                    node=op.get("node"),
                    node_type=op.get("node_type"),
                    label=op.get("label"),
                    parent=op.get("parent"),
                    source=op.get("source"),
                    target=op.get("target"),
                    relation=op.get("relation"),
                    importance=op.get("importance"),
                    mark=op.get("mark"),
                    reason=op.get("reason"),
                    meta_ids=list(op.get("meta_ids") or []),
                ))
            return GraphUpdateOp(operations=ops, thought=data.get("thought", "[llm] insights → ops"))
        except (TypeError, ValueError, KeyError) as e:
            raise LLMError(f"sync: invalid structured output: {e}") from e


# ---------------------------------------------------------------------------
# 规则实现（仅在明确使用 MockLLM 时启用）
# ---------------------------------------------------------------------------
_CONFLICT_KW = ("但是", "但", "不过", "反对", "不同意", "不认同", "分歧", "矛盾",
                "冲突", "然而", "优先级没那么高")
_CONCLUSION_KW = ("结论", "决定", "因此", "所以", "综上", "敲定", "定下来", "确定",
                  "一致同意", "达成一致")
_ACTION_KW = ("下周", "明天", "本周", "会后", "行动", "负责", "跟进", "落地", "推进",
              "安排", "执行", "完成", "提交", "写完", "review", "to do", "todo")
_EVIDENCE_KW = ("数据", "根据", "报告", "调研", "因为", "例如", "比如", "举例", "去年",
                "环比", "同比", "%", "增长", "下降", "口径")


def _classify(text: str) -> str:
    """规则分级：把一句话判为 6 类节点之一（顺序即优先级）。"""
    t = text or ""
    if any(k in t for k in _CONFLICT_KW):
        return "conflict"
    if any(k in t for k in _CONCLUSION_KW):
        return "conclusion"
    if any(k in t for k in _ACTION_KW):
        return "action"
    if any(k in t for k in _EVIDENCE_KW):
        return "evidence"
    return "point"


def _importance_hint(node_type: str) -> str:
    """行动项/结论/分歧视为高优先，其余普通。"""
    return "high" if node_type in ("action", "conclusion", "conflict") else "normal"


def _mock_analyze(filtered_text, filtered_meta_ids, meeting_title):
    insights = []
    for i, txt in enumerate(filtered_text):
        meta = filtered_meta_ids[i] if i < len(filtered_meta_ids) else None
        ntype = _classify(txt)
        insights.append(InsightRecord(
            type=ntype, summary=txt.strip(), speaker_ref=None,
            evidence=[meta] if meta else [], confidence=0.7,
            importance_hint=_importance_hint(ntype),
            related_to="n_issue_root",
            relation_to_related="oppose" if ntype == "conflict" else "support"))
    return MeetingSummary(meeting_id="?", meeting_title=meeting_title, insights=insights,
                          thought="[fallback] 规则分级 + 挂到议题根")


def _mock_sync(summary, board_cells):
    ops = []
    existing = {c["id"] for c in board_cells}
    for ins in summary.insights:
        nid = f"n_p_{stable_hash(ins.summary, 10 ** 6)}"   # 文本派生，跨重启稳定→可按 id 合并
        ops.append(GraphOp(op="add_node", node=nid, node_type=ins.type,
                           label=ins.summary[:40], meta_ids=list(ins.evidence)))
        if ins.related_to and ins.related_to in existing:
            ops.append(GraphOp(op="link", source=nid, target=ins.related_to,
                               relation=ins.relation_to_related or "support"))
        hint = ins.importance_hint or _importance_hint(ins.type)
        if hint and hint != "normal":
            ops.append(GraphOp(op="set_importance", node=nid, importance=hint))
    return GraphUpdateOp(operations=ops, thought="[fallback] insights → add_node/link/set_importance")


# ---------------------------------------------------------------------------
# 工厂：按配置选真实客户端或 Mock
# ---------------------------------------------------------------------------
def build_llm() -> LLMClient:
    """默认（未启用）返回 MockLLM；启用且有端点则尝试 OpenAIClient，失败回退 Mock。"""
    cfg = config.CONFIG
    if cfg.llm_enabled and (cfg.llm_api_key or cfg.llm_base_url):
        try:
            return OpenAIClient()
        except Exception:
            return MockLLM()
    return MockLLM()
