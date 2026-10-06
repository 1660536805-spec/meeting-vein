# 看板娘模块 · 详细设计

> **文档状态**：设计 v0.1 · 待评审
> **最后更新**：2026-09-18
> **说明**：「看板娘」即「Mascot」——前端人格化表现层，与 `doc/Research_KanbanMusume.md`（看板娘调研）为同一对象。
> **关联文档**：
> - `doc/Research_KanbanMusume.md`（调研：状态机 / 眼睛追踪 / 路线对照）
> - `doc/Design_Agent_DataFlow.md` 及 `doc/Design_InputProcessing.md`（LangGraph 节点 = 表情触发源；双 Agent 后节点为 `analyze_node` + `sync_node`）
> - `doc/Research_X6_MindMap.md`（看板容器 / React 组件渲染）
> **模块定位**：看板娘是看板的「人格化表现层」，承载两件事——(1) **状态可视化**：把后端 Agent 流水线执行进度（尤其 LLM 请求链路）翻译成用户秒懂的表情/动作；(2) **亲和力引导**：眼睛始终追踪鼠标光标。两条硬需求。**关键边界**：表情状态是事件驱动（后端 WS 推送）；眼睛追踪是纯前端视觉（本地 `pointermove`），**不进 LangGraph、不进 Store A/B、不进 `NormCursorEvent` 意图流**——与鼠标信号采集模块共享事件源但用途完全隔离。

---

## 1. 状态机 `MascotState`（核心需求一）

### 1.1 枚举（与 LangGraph 节点精确映射）

> 因输入处理模块已拆为**双 Agent**（`Design_InputProcessing.md`），原 `reason_node` 的 `reasoning` 状态在此拆为 `analyzing`（分析 Agent）+ `syncing`（图同步 Agent），更贴合实际进度。

| `MascotState` | 触发源（LangGraph 节点 / 事件） | 表情语义 | SVG class | Live2D expression | 文案气泡 label |
|---|---|---|---|---|---|
| `idle` | 无事件 / 空闲 / 批次间隙 | 待机中性 | `.m-idle`（呼吸+眨眼） | `neutral`(0) | 「待命中…」 |
| `listening` | `input` / `input_cursor` 接收流入 | 专注聆听 | `.m-listening`（头部微侧） | `neutral`+眼亮 | 「正在聆听转写…」 |
| `filtering` | `filter` / `filter_cursor` | 轻点头过滤 | `.m-thinking`（点头） | `thinking`(轻) | 「筛选有效信息…」 |
| `loading_board` | `load_board` / `gen_initial` | 翻阅看板 | `.m-reading` | `neutral` | 「载入当前看板…」 |
| `assembling` | `assemble_analyze` / `assemble_sync` | 凝神准备 | `.m-think-hard`（皱眉微） | `thinking` | 「整理思路、组装提示词…」 |
| `analyzing` | **`analyze_node` 分析 Agent 推理中** | 深度思考、眉头微蹙 | `.m-reasoning`（眼神聚焦） | `thinking`(深) | 「分析会议要点…」 |
| `syncing` | **`sync_node` 图同步 Agent 推理中** | 落笔前的结构映射 | `.m-reasoning2`（比划连线） | `thinking`(深)+`ParamAngleZ` | 「梳理结构图关系…」 |
| `parsing` | `parse` 解析与校验 | 眯眼审视 | `.m-inspect`（眯眼） | `thinking`(审视) | 「校验结构化输出…」 |
| `updating` | `update` 写回 Store A | 点头落笔 | `.m-writing`（点头确认） | `happy`(轻) | 「更新看板关系图…」 |
| `success` | `update` 完成且 `parse_ok` | 微笑完成 | `.m-happy`（微笑+小跳） | `happy` | 「已更新 ✓」 |
| `error` | `parse` 失败降级 / 异常 | 皱眉困惑 | `.m-error`（皱眉摇头） | `sad`/`angry`(轻) | 「理解受阻，已降级处理」 |

> 优先级：`error`/`success` 瞬时终态 → 回 `idle`；`analyzing`/`syncing`/`parsing`/`updating` 过程态；`idle` 默认兜底。典型链：`analyzing → syncing → parsing → updating → success`。

### 1.2 与节点映射图

```
[输入] input / input_cursor        ──▶ listening
  │  filter / filter_cursor        ──▶ filtering
  │  load_board / gen_initial      ──▶ loading_board
  │  assemble_analyze / _sync      ──▶ assembling      （进入 LLM 请求前）
  │  analyze_node                  ──▶ analyzing        （★ 分析 Agent 推理，核心）
  │  sync_node                     ──▶ syncing          （★ 图同步 Agent 推理）
  │  parse                         ──▶ parsing
  ├─ parse_ok=True  ─▶ update      ──▶ updating ──▶ success（终态→idle）
  └─ parse_ok=False ─▶ (重试)assemble_analyze 或 (降级)update ──▶ error（终态→idle）
```

---

## 2. 后端发射点（事件驱动）

后端在**节点进入时** emit `MascotStateEvent`；`analyzing`/`syncing` 在 LLM 请求发起时进入、收到响应时退出（对齐 `Design.md §6.2` 请求-响应生命周期）。

```python
# 编排框架钩子（伪代码）
def emit_state(state: MascotState, label: str):
    bus.publish({"type": "mascot_state", "state": state, "label": label,
                 "ts_ms": now_ms()}, channel=f"ws/mascot/{meeting_id}")

# 节点装饰器：进入即发
@on_enter("analyze_node")
def _(): emit_state("analyzing", "分析会议要点…")
@on_enter("sync_node")
def _(): emit_state("syncing", "梳理结构图关系…")
@on_enter("update")
def _(): emit_state("updating", "更新看板关系图…")
@on_enter("parse")
def _(ok): emit_state("success" if ok else "error", "已更新 ✓" if ok else "理解受阻，已降级处理")
```

### 2.1 WS 消息协议

```json
{ "type": "mascot_state", "state": "analyzing", "label": "分析会议要点…", "ts_ms": 1726483506123 }
```

与 `NormCursorEvent` 同源 WS，但 `type` 字段区分；前端按 `type` 路由到 `MascotController`（不混入口）。

---

## 3. 前端 `MascotController`

```typescript
type MascotState =
  | 'idle' | 'listening' | 'filtering' | 'loading_board' | 'assembling'
  | 'analyzing' | 'syncing' | 'parsing' | 'updating' | 'success' | 'error';

class MascotController {
  private state: MascotState = 'idle';
  private pupils: NodeListOf<HTMLElement>;
  private idleTimer: number | null = null;

  mount() {
    window.addEventListener('pointermove', this.onPointerMove);   // 眼睛追踪（始终运行）
    ws.on('mascot_state', (m) => this.setState(m.state, m.label)); // 状态订阅
  }

  private onPointerMove = (e: PointerEvent) => {                  // ★ 纯视觉，不进 LangGraph
    const R = 15;
    this.pupils.forEach((p) => {
      const r = p.getBoundingClientRect();
      const a = Math.atan2(e.clientY - (r.top + r.height/2), e.clientX - (r.left + r.width/2));
      p.style.transform = `translate(${Math.cos(a)*R}px, ${Math.sin(a)*R}px)`;
    });
  };

  setState(state: MascotState, label: string) {
    this.state = state;
    this.root.className = `mascot m-${state}`;   // 切换表情 class（CSS 定义脸/动作）
    this.bubble.textContent = label;             // 文案气泡
    if (this.idleTimer) clearTimeout(this.idleTimer);
    if (state === 'success' || state === 'error')
      this.idleTimer = setTimeout(() => this.setState('idle', '待命中…'), IDLE_RETURN_MS);
  }
}
```

---

## 4. 眼睛追踪（核心需求二）：始终追踪鼠标，且与表情叠加共存

**硬约束**：眼睛追踪与表情/动作状态**叠加共存、互不覆盖**（换表情时眼睛照样跟鼠标）。

### 4.1 路线 A：SVG/CSS（推荐 MVP，零授权零 WebGL）

自绘 SVG 角色（眼睛=两圆，瞳孔=内圆）。单个 `pointermove` + `atan2` 算角度 + 限制半径 + `transition` 平滑。

```css
.pupil { transition: transform 0.08s ease-out; }        /* 平滑，否则像机器人跳 */
@keyframes blink { 0%,96%,100% { transform: scaleY(1); } 98% { transform: scaleY(0.1); } }
.eye { animation: blink 4s infinite; }                    /* 眨眼独立于眼睛追踪 */
@media (prefers-reduced-motion: reduce) { .pupil,.eye { animation: none; transition: none; } }
```

> 细节（社区实践）：(1) **限制垂直幅度**（约 85%）防恐怖谷；(2) 加 `transition`；(3) 加**高光点 catchlight** 让眼睛有神；(4) 尊重 `prefers-reduced-motion` 无障碍降级；(5) **鼠标移出窗口**回正（neutral），避免斜视。

### 4.2 路线 B：Live2D Cubism（精致路线，可后续升级）

用原生 `ParamEyeBallX/Y`(-1~1) + 头部 `ParamAngleX/Z` 联动；官方 FocusController 用 `addToParamFloat` 把视线**叠加**在 motion/expression 之上（additive，不覆盖），顺序 `motion → expression → focus → natural → physics`，故换表情时眼睛照样追鼠标：

```javascript
window.addEventListener('mousemove', (e) => {
  const x = (e.clientX / innerWidth) * 2 - 1;
  const y = (e.clientY / innerHeight) * 2 - 1;
  model.internalModel.motionManager.setLookTarget(e.clientX, e.clientY, 5);  // 推荐封装
  // 或手动：model.addParameterValueById('ParamEyeBallX', x);
  //         model.addParameterValueById('ParamEyeBallY', y);
  //         model.addParameterValueById('ParamAngleX', x * 30);
});
```

| 维度 | 路线 A（SVG/CSS，MVP） | 路线 B（Live2D） |
|---|---|---|
| 视觉精度 | 中（卡通自绘） | 高（骨骼+物理） |
| 集成成本 | 低，纯组件 | 中（Cubism Core+模型） |
| 授权 | 无 | Cubism Core 免费；自制模型需 Editor |
| 眼睛追踪 | `atan2`+半径 | 原生 `ParamEyeBallX/Y`+头部联动 |
| 与表情叠加 | 瞳孔 `transform` 独立 | `addToParamFloat` 天然叠加 |
| 性能 | 极低 | WebGL context |

**选型**：MVP 走路线 A，状态机与 WS 协议**完全复用**，后续升级 Live2D 只换渲染适配器。

---

## 5. 渲染契约

### 5.1 路线 A（SVG 组件结构）

```html
<div class="mascot m-idle" id="mascotRoot">
  <svg viewBox="0 0 120 120">
    <g class="face">…</g>
    <g class="eye left"><circle class="pupil"/></g>
    <g class="eye right"><circle class="pupil"/></g>
    <circle class="catchlight"/>     <!-- 高光 -->
  </svg>
  <div class="bubble" id="mascotBubble">待命中…</div>
</div>
```

- 表情由 `root.className` 切换，CSS 定义各 `.m-*` 的脸/眉/嘴/动作（keyframes）。
- `pupils` 引用两个 `.pupil` 元素，眼睛追踪改写其 `transform`（与表情 class 不冲突：眨眼用 `scaleY`、追踪用 `translate`）。

### 5.2 路线 B（Live2D 模型参数）

需模型含标准参数：`ParamEyeBallX/Y`、`ParamAngleX/Y/Z`、`ParamBodyAngleX`，并预置 `neutral/thinking/happy/sad` 等 expression。状态切换调 `expressionManager.setExpression(index)`。

---

## 6. 集成与生命周期

| 项 | 约定 |
|---|---|
| 容器位置 | 悬浮右下角固定层（默认）或嵌入 X6 画布侧边（见 `Research_X6_MindMap.md`）；默认悬浮层，低耦合 |
| 挂载 | 看板页加载时 `MascotController.mount()`；卸载 `removeEventListener` + `ws.off` |
| 空闲回退 | `success`/`error` 后 `IDLE_RETURN_MS`（默认 1500ms）回 `idle` |
| `error` 分级 | 「解析失败重试中」(轻困惑) vs 「降级终态」(明确皱眉)，由后端 `parse` 反馈区分 |
| 鼠标移出 | 眼睛回正 neutral |

---

## 7. 服务/接口清单

| 接口 | 方向 | 说明 |
|---|---|---|
| `WS /ws/mascot/{meeting_id}` | 后端→前端 | 推送 `mascot_state` 事件（可复用 ASR/光标 WS 同一连接，按 `type` 路由） |
| `GET /api/mascot/config` | 后端→前端 | 下发 `IDLE_RETURN_MS` 等运行参数 |

---

## 8. 与既有文档一致性

| 本模块 | 关联文档 |
|---|---|
| 表情触发源 = LangGraph 节点 | `Design_Agent_DataFlow.md` §2 / `Design_InputProcessing.md` §6（双 Agent） |
| `analyzing`/`syncing` 对齐 LLM 请求生命周期 | `Design.md` §6.2 |
| 状态推送走 WS | `Research_CursorIntent_Capture.md`（同源通道） |
| 眼睛追踪 = `NormCursorEvent` 同源事件源但用途隔离 | `Research_CursorIntent_Capture.md` §4（务必区分：表现层 vs 意图层） |
| 容器进 X6 画布旁 | `Research_X6_MindMap.md` |

---

## 9. 待确认 / 开放项

- Q1（已闭合）：**采纳路线 A（SVG 2D 矢量 + CSS/SMIL 动画）**——轻量、易嵌入、跨端一致，MVP 优先；路线 B（Cubism 3D Live2D）为后续增强（见 Q7）。
- Q2（已闭合）：**暂不细分**——`analyzing`/`syncing` 用单档表情；待 LLM 流式进度事件（首 token / 流式中）能力在接入层确认后再评估细分，不影响当前状态机。
- Q3（已闭合）：`IDLE_RETURN_MS=1500ms`（默认），成功/错误态停留 1.5s 后回 `idle`，避免表情频繁跳变。
- Q4（已闭合）：**区分两档视觉**——`error` 细分「重试中」（旋转/等待态）与「降级终态」（警示态），让用户区分可恢复与终态，呼应 `Design.md` §9 降级策略。
- Q5（已闭合）：**鼠标移出窗口时眼睛回正**（居中），避免「盯着空处」的怪异感；回正带缓动过渡。
- Q6（已闭合）：**悬浮右下角**（低耦合、不挤占 X6 画布/侧栏），与 `Design_FrontendBoard.md` §9 Q5 一致。
- Q7（已闭合，路线 B 暂缓）：**路线 A 优先上线**，路线 B（Cubism 3D Live2D）暂缓；若启动 B，先用官方免费示例模型（`haru`/`shizuku`）占位、资产待定（授权/自制在立项时评估），当前不阻塞 MVP。
