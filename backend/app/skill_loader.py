"""x6-graph-ops 技能包渐进式加载器（供生成图的 Syncer Agent 使用）。

设计：SKILL.md §5「本项目约定」为单一事实源，按行首标签分层渐进注入，
而非整份灌入提示词（防上下文膨胀、防与任务无关内容稀释规则）：

- L0 常驻：无标签行 + [常驻] 行 —— 每批必带（渲染契约核心：节点/边语义、label/坐标、树形方向）。
- L1 按需：[边]/[cmd]/[大图] 行 —— 仅当看板上下文命中对应场景才注入：
    * [边]   ：cells 中已存在边（说明图进入连边维护阶段，须尊重用户手动弧线/拖动）；
    * [cmd]  ：cells 中存在 data.cmd 标记节点（灰化/删除线契约）；
    * [大图] ：节点数超过 MAX_LLM_NODES（序列化截断，防引用不可见 id）。

SKILL.md 内容模块级缓存，仅首次读取文件，后续复用内存副本。
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import List

from . import prompts   # 复用 MAX_LLM_NODES 阈值，与 serialize_for_llm 截断保持一致

_SKILL_PATH = Path(__file__).with_name("skills") / "x6-graph-ops" / "SKILL.md"
_SKILL_MD: List[str] | None = None          # 模块级缓存：None=未读取
_TAG_RE = re.compile(r"^- \[([\w-]+)\]\s*(.+)$")   # 任意行首标签；未知标签自动排除出注入层

MAX_SECTION_TITLE = "[图形技能包 x6-graph-ops · L0 常驻规范（渲染层契约，生成 op 须遵守）]"
ON_DEMAND_TITLE = "[图形技能包 x6-graph-ops · L1 按需规范（本批看板命中以下场景）]"


def _read_text(path: Path) -> str:
    """IO 薄封装（便于测试打桩统计真实文件读取次数）。"""
    return path.read_text(encoding="utf-8")


def _skill_lines() -> List[str] | None:
    """带缓存的 SKILL.md 行列表；IO 失败返回 None（失败不缓存，下次重试）。"""
    global _SKILL_MD
    if _SKILL_MD is None:
        try:
            _SKILL_MD = _read_text(_SKILL_PATH).splitlines()
        except OSError:
            return None
    return _SKILL_MD


def _extract_section(lines: List[str], title_prefix: str) -> List[str]:
    """抽取 `## <title_prefix>` 小节的行（到下一个 `## ` 或文件尾为止）。"""
    out, inside = [], False
    for ln in lines:
        if ln.startswith("## "):
            inside = ln.startswith(f"## {title_prefix}")
            continue
        if inside and ln.strip():
            out.append(ln)
    return out


def _tagged(section_lines: List[str]) -> dict:
    """把小节行拆成 {tag: text}（tag∈常驻/边/cmd/大图），无标签行归入 ''。"""
    groups: dict = {}
    for ln in section_lines:
        m = _TAG_RE.match(ln)
        if m:
            groups.setdefault(m.group(1), []).append(m.group(2))
        else:
            groups.setdefault("", []).append(ln.strip(" -").strip())
    return groups


def load_syncer_skill(cells: list, focus: list | None = None) -> str:
    """按看板上下文渐进组装 x6-graph-ops 技能段，注入 Syncer 系统提示词。

    返回多行文本（可能为空串：SKILL.md 缺失/无内容时不注入，不阻断推理）。
    """
    section = _extract_section(_skill_lines() or [], "5. 本项目")
    if not section:
        return ""
    groups = _tagged(section)

    nodes = [c for c in (cells or []) if c.get("shape") != "edge"]
    edges = [c for c in (cells or []) if c.get("shape") == "edge"]
    has_cmd = any(isinstance(c.get("data"), dict) and c["data"].get("cmd") for c in nodes)

    # L0 常驻：无标签行 + [常驻] 行
    l0 = list(groups.get("", [])) + list(groups.get("常驻", []))
    if not l0:
        return ""

    # L1 按需：场景命中才追加对应条目
    l1: list = []
    if edges:
        l1 += groups.get("边", [])
    if has_cmd:
        l1 += groups.get("cmd", [])
    if len(nodes) > prompts.MAX_LLM_NODES:
        l1 += groups.get("大图", [])
    l1 = [t for t in l1 if t]

    out = [MAX_SECTION_TITLE] + [f"- {t}" for t in l0]
    if l1:
        out += ["", ON_DEMAND_TITLE] + [f"- {t}" for t in l1]
    return "\n".join(out)
