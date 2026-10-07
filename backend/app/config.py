"""全局配置：参数集中管理，可由 GET /api/cursor/config 下发给前端（Design_CursorCapture §9 / §10）。"""
from __future__ import annotations
import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path
from . import prompts

try:                                # 可选依赖：未装 python-dotenv 时仍可用纯环境变量
    from dotenv import load_dotenv
    # 加载 backend/.env（config.py 位于 backend/app/ 下，parents[1] = backend/）
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass


@dataclass(frozen=True)
class Config:
    # —— 光标采集（Design_CursorCapture.md）——
    throttle_ms: int = 200                 # mousemove 采集端节流（§2.2 默认 200ms）
    hover_settle_ms: int = 250             # hover 有效停留阈值
    drag_px_threshold: int = 8             # 位移多少 px 算 drag 而非 click
    view_mode_sample_rate: float = 0.3     # 查看模式下降采样比例
    cursor_independent_trigger: bool = False  # 光标随下一批发言合并，不独立触发推理
    cursor_fusion_window_ms: int = 1000

    # —— 无意义过滤（mock filter_node）——
    max_retry: int = 2                      # 校验失败重试上限
    filler_words: tuple = prompts.FILLER_WORDS

    # —— 实时流合批（P1②）——
    # ASR 实时路径把多句在窗口内合并为一批再驱动编排，摊薄每句 2 次串行 LLM 调用；
    # 0 关闭（逐句即时驱动）。CLI/batch 调试口不受影响（脚本需即时确定性反馈）。
    realtime_debounce_ms: int = 600
    # CLI 逐句推送的自适应合批上限（块）：推流快于 LLM 消化时，等锁积压的句子
    # 自动合并为一批（≤上限）一次驱动；推得慢时每批 1 句，行为与逐句一致。
    cli_max_block: int = 12
    cli_coalesce_ms: int = 600             # 每块取前的小等待窗：把紧邻的下一句并进同块

    # —— 失败批次补偿（P0①）：LLM 失败的批次记 pending，后台周期重驱动 ——
    pending_retry_max_attempts: int = 3     # 单批次最大重放次数（超过后保留记录待人工处理）
    pending_retry_interval_s: float = 30.0  # 后台补偿轮询间隔（秒）

    # —— 存储 ——
    storage_root: str = ".amo_data"         # 本地 json 真相源（生产换 jsonb）
    local_asr_base_url: str = field(
        default_factory=lambda: os.getenv("AMO_LOCAL_ASR_BASE_URL", "http://127.0.0.1:9000"))

    # —— 会议 ——
    default_meeting_id: str = field(
        default_factory=lambda: os.getenv("AMO_DEFAULT_MEETING", prompts.DEFAULT_MEETING_ID))
    default_meeting_title: str = field(
        default_factory=lambda: os.getenv("AMO_DEFAULT_MEETING_TITLE", prompts.DEFAULT_MEETING_TITLE))

    # —— 大模型接入（Design §6.2：OpenAI 兼容、模型可插拔）——
    # 默认关闭（用内置 MockLLM），置 AMO_LLM_ENABLED=1 并填 key 即切真实模型。
    # 配置全部走环境变量，集中放 .env（已被 .gitignore 忽略，不进版本库）。
    llm_enabled: bool = field(
        default_factory=lambda: os.getenv("AMO_LLM_ENABLED", "0").lower() in ("1", "true", "yes"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    llm_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("OPENAI_TEMPERATURE", "0.3")))
    llm_timeout: float = field(default_factory=lambda: float(os.getenv("OPENAI_TIMEOUT", "30")))
    llm_max_tokens: int = field(default_factory=lambda: int(os.getenv("OPENAI_MAX_TOKENS", "1024")))
    llm_max_retries: int = field(default_factory=lambda: int(os.getenv("OPENAI_MAX_RETRIES", "2")))

    # —— max_tokens 自适应（P1③）：按本批规模动态给预算，避免长输出被截断 / 短输出浪费 ——
    llm_max_tokens_base: int = field(default_factory=lambda: int(os.getenv("OPENAI_MAX_TOKENS_BASE", "512")))
    llm_max_tokens_per_unit: int = field(default_factory=lambda: int(os.getenv("OPENAI_MAX_TOKENS_PER_UNIT", "96")))
    llm_max_tokens_cap: int = field(default_factory=lambda: int(os.getenv("OPENAI_MAX_TOKENS_CAP", "4096")))

    # —— 上游熔断（P2②）：连续失败达阈值即打开熔断，cooldown 内快速失败，成功一次即复位 ——
    llm_circuit_threshold: int = field(default_factory=lambda: int(os.getenv("OPENAI_CIRCUIT_THRESHOLD", "5")))
    llm_circuit_cooldown_s: float = field(default_factory=lambda: float(os.getenv("OPENAI_CIRCUIT_COOLDOWN_S", "30")))

    # —— 看板自愈（P2①）：commit 时自动把游离节点挂回议题树，避免孤立点堆积 ——
    auto_repair_orphans: bool = field(
        default_factory=lambda: os.getenv("AMO_AUTO_REPAIR_ORPHANS", "1").lower() in ("1", "true", "yes"))

    # —— 写接口鉴权（可选）：置 AMO_API_TOKEN 后写接口需带 Authorization: Bearer <token> ——
    api_token: str = field(default_factory=lambda: os.getenv("AMO_API_TOKEN", ""))


CONFIG = Config()


def apply_overrides(**kw) -> dict:
    """以不可变方式覆写全局配置：重建 Config 并替换单例，返回实际生效的字段。

    仅接受已声明字段；Config 为 frozen，杜绝运行期任意 setattr 造成的隐式全局状态。
    """
    global CONFIG
    valid = {k: v for k, v in kw.items() if k in Config.__dataclass_fields__}
    if valid:
        CONFIG = dataclasses.replace(CONFIG, **valid)
    return valid
