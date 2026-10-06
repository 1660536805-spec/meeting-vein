# 看板娘功能调研文档

> **文档状态**：草稿 v0.1 · 待评审
> **最后更新**：2026-09-16
> **关联文档**：`doc/Design_Agent_DataFlow.md`（LangGraph 节点 = 表情状态触发源）、`doc/Research_X6_MindMap.md`（前端 X6 画布）、`doc/Research_CursorIntent_Capture.md`（`NormCursorEvent` 光标流）
> **目标**：调研「会议秘书看板娘」的可行技术方案，重点回答两个需求——(1) **模型请求过程的不同节点对应看板娘不同表情状态**；(2) **看板娘眼睛始终追踪鼠标光标**。

---

## 1. 功能定位

看板娘（Mascot / 桌宠）是看板的可视化「人格层」，承载两件事：

1. **状态可视化**：把后端 Agent 的 LangGraph 执行进度（尤其是 LLM 请求链路）翻译成用户能瞬间读懂的表情/动作，降低「AI 在干什么」的认知成本。
2. **亲和力与焦点引导**：通过眼睛始终跟随鼠标，制造「角色在看着我」的存在感，把用户注意力引回看板主区域。

**关键边界（结论先行）**：
- 眼睛追踪是**纯表现层**（前端视觉），**不进入 LangGraph、不进 Store A/B、不进 `NormCursorEvent` 意图流**——它与光标意图采集共享同一个 `mousemove` 事件源，但用途完全分离（一个给肉眼看，一个给 LLM 读）。
- 表情状态是**事件驱动**的：由 LangGraph 节点进入/退出事件经 WS/SSE 推送到前端，前端映射到角色表情/动作。**表情状态机是本项目自定的业务状态，与具体渲染引擎（Live2D / SVG）解耦**。

---

## 2. 表情状态机（核心需求一）

### 2.1 状态枚举

把 LangGraph 的执行过程抽象为一组互斥的看板娘状态（同一时刻一个主状态）。每个状态定义三件套：**表情（expression）+ 动作（motion）+ 文案气泡（label）**。

| 状态 `MascotState` | 触发源（LangGraph 节点 / 事件） | 表情语义 | Live2D `expression` | SVG 方案（class） | 文案气泡 label |
|---|---|---|---|---|---|
| `idle` | 无事件 / 会议未开始 / 空闲 | 待机中性 | `neutral`(index 0) | `.m-idle`（呼吸+眨眼） | 「待命中…」 |
| `listening` | `input` / `input_cursor` 接收数据流入 | 专注聆听，眼睛微亮 | `neutral` + 轻微 `ParamEyeBall` 抖动 | `.m-listening`（头部微侧） | 「正在聆听转写…」 |
| `filtering` | `filter` / `filter_cursor` 过滤处理 | 思考过滤中，轻点头 | `thinking`(轻) | `.m-thinking`（点头） | 「筛选有效信息…」 |
| `loading_board` | `load_board` / `gen_initial` 载入或生成图 | 翻阅看板 | `neutral` | `.m-reading` | 「载入当前看板…」 |
| `assembling` | `assemble` 组装提示词（进入 LLM 请求前） | 准备、凝神 | `thinking` | `.m-think-hard`（摸下巴/皱眉微） | 「整理思路、组装提示词…」 |
| `reasoning` | **`reason_node` LLM 推理中（核心）** | 深度思考，眼神聚焦、眉头微蹙 | `thinking`(深) + 可选 `ParamAngleZ` 微倾 | `.m-reasoning`（眼神聚焦 + 转圈 loading 感） | 「深度思考中…」 |
| `parsing` | `parse` 解析与校验 | 审视、眯眼校验 | `thinking`(审视) | `.m-inspect`（眯眼） | 「校验结构化输出…」 |
| `updating` | `update` 写回 Store A | 书写/落笔，点头确认 | `happy`(轻) / `neutral`+手部动作 | `.m-writing`（点头确认） | 「更新看板关系图…」 |
| `success` | `update` 完成且 `parse_ok` | 完成、微笑 | `happy` | `.m-happy`（微笑+小跳） | 「已更新 ✓」 |
| `error` | `parse` 失败且降级 / 异常 | 皱眉、困惑 | `sad` / `angry`(轻) | `.m-error`（皱眉摇头） | 「理解受阻，已降级处理」 |

> 优先级：`error` / `success` 为瞬时终态，`reasoning`/`parsing`/`updating` 为过程态，`idle` 为默认兜底。同一批次内 `reasoning → parsing → updating → success` 是典型链。

### 2.2 与 LangGraph 节点的精确映射

直接对应 `Design_Agent_DataFlow.md` §2.2–§2.3 的节点与条件路由：

```
[输入] input / input_cursor        ──▶ listening
  │
  ▼  filter / filter_cursor        ──▶ filtering
  │
  ▼  load_board / gen_initial      ──▶ loading_board
  │
  ▼  assemble                      ──▶ assembling   （★ 进入 LLM 请求前）
  │
  ▼  reason                        ──▶ reasoning     （★ LLM 推理中，耗时最长，核心表情）
  │
  ▼  parse                         ──▶ parsing
  │
  ├─ parse_ok=True  ─▶ update      ──▶ updating ──▶ success（终态，回 idle）
  │
  └─ parse_ok=False ─▶ (重试)assemble 或 (降级)update ──▶ error（终态，回 idle）
```

> 触发源 = 后端在节点**进入时**推 `{type:"mascot_state", state, label}`；`reasoning` 状态在 `reason_node` 发起 LLM 请求时进入，在收到响应时退出（与 `Design.md §6.2` 统一 LLM 接口的「请求-响应」生命周期对齐）。

### 2.3 事件驱动架构

```
后端 LangGraph 节点（进入/退出）
      │  emit MascotStateEvent
      ▼
WebSocket / SSE 通道  ──（复用 Research_CursorIntent_Capture 的光标流通道或独立 mascot 通道）
      │
      ▼
前端 MascotController
      ├─ setState(state)        ──▶ 映射 expression index / SVG class
      ├─ setBubble(label)       ──▶ 渲染状态文案
      └─ 始终运行的 eyeTracker  ──▶ setLookTarget(mouseX, mouseY)  （见 §3，不受 state 影响）
```

**消息协议（建议）**：
```json
{ "type": "mascot_state", "state": "reasoning", "label": "深度思考中…", "ts_ms": 123456789 }
```
> 与 `NormCursorEvent` 同源 WS 通道，但 `type` 字段区分；前端按 `type` 路由到不同 Controller。**眼睛追踪不走这条消息**——它直接监听浏览器 `mousemove`，见 §3。

---

## 3. 眼睛追踪（核心需求二）

**硬需求：看板娘眼睛始终追踪鼠标光标，且与表情/动作状态叠加共存（不互相覆盖）。**

### 3.1 Live2D Cubism 方案（路线 B，精致）

Live2D 原生支持视线追踪，核心是三个标准参数（见 `docs.live2d.com` 参数手册）：

| 参数 | 范围 | 作用 |
|---|---|---|
| `ParamEyeBallX` | -1.0 ~ 1.0 | 眼球左右 |
| `ParamEyeBallY` | -1.0 ~ 1.0 | 眼球上下 |
| `ParamAngleX/Y/Z` | -30° ~ 30° | 头部朝向（强化「看向」感） |
| `ParamBodyAngleX` | -10° ~ 10° | 身体微倾 |

**实现**：官方 Sample 的 FocusController 用 `addToParamFloat` 把视线**叠加**在 motion/expression 参数之上（additive，不覆盖），顺序为 `motion → expression → focus tracking → natural movement → physics`。因此**即使角色在换表情/播动作，眼睛也始终追鼠标**：

```javascript
// 监听全局鼠标
window.addEventListener("mousemove", (e) => {
  const x = (e.clientX / window.innerWidth) * 2 - 1;   // 归一化到 [-1, 1]
  const y = (e.clientY / window.innerHeight) * 2 - 1;
  model.internalModel.motionManager.setLookTarget(e.clientX, e.clientY, 5); // 推荐封装
  // 或手动：
  // model.addParameterValueById("ParamEyeBallX", x);
  // model.addParameterValueById("ParamEyeBallY", y);
  // model.addParameterValueById("ParamAngleX", x * 30);
  // model.addParameterValueById("ParamBodyAngleX", x * 10);
});
```

> 也可用现成封装：`@greenmansk/react-live2d` 的 `motionManager.setLookTarget(x, y, priority)` + `setBodyOrientationTarget(x, y, 3)`。Cubism Core 是闭源二进制但**免费**；只有用 Cubism Editor 制作/导出自有模型才需授权。

### 3.2 轻量 SVG/CSS 方案（路线 A，推荐 MVP）

自绘 SVG 二次元角色（眼睛=两个圆，瞳孔=内圆），单个 `pointermove` 监听 + `atan2` 算角度 + 限制移动半径 + `transition` 平滑。零 WebGL、零授权、可直接进 React/Vue 组件。

```javascript
const pupils = document.querySelectorAll(".eye .pupil");
const R = 15; // 瞳孔可移动半径（限制，避免出眼眶）
window.addEventListener("pointermove", (e) => {
  pupils.forEach((pupil) => {
    const rect = pupil.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const angle = Math.atan2(e.clientY - cy, e.clientX - cx);
    const dx = Math.cos(angle) * R;
    const dy = Math.sin(angle) * R;
    pupil.style.transform = `translate(${dx}px, ${dy}px)`;
  });
});
// 平滑：.pupil { transition: transform 0.08s ease-out; }
// 眨眼/呼吸：独立 CSS @keyframes，与眼睛追踪 transform 分离（眨眼用 scaleY，不冲突）
```

> 关键细节（来自社区实践）：(1) **限制垂直移动幅度**（约 85%）防止「恐怖谷」；(2) **加 `transition`** 否则像机器人跳；(3) **加高光点（catchlight）** 让眼睛「有神」；(4) 尊重 `prefers-reduced-motion` 媒体查询，无障碍降级。

### 3.3 两条路线的对照

| 维度 | 路线 A：SVG/CSS（推荐 MVP） | 路线 B：Live2D Cubism |
|---|---|---|
| 视觉精度 | 中（自绘二次元，扁平/卡通） | 高（专业骨骼绑定、物理摆动） |
| 集成成本 | 低，纯前端组件 | 中，需加载 Cubism Core + 模型文件 |
| 授权 | 无（自绘或 MIT 素材） | Cubism Core 免费；自制模型需 Cubism Editor |
| 眼睛追踪 | 自算 `atan2` + 半径限制 | 原生 `ParamEyeBallX/Y` + 头部联动 |
| 表情切换 | CSS class 切换 / SVG 重绘 | `expressionManager.setExpression(index)` + 淡入 |
| 与表情叠加 | 眼睛 `transform` 独立于表情 class | `addToParamFloat` 天然叠加（不覆盖） |
| 性能 | 极低 | WebGL，需管理 context |
| 适用 | 功能性看板工具、快速落地 | 重质感二次元产品 |

**选型建议**：MVP 走路线 A（SVG/CSS），把状态机与眼睛追踪先跑通；若后续要更强表现力，升级路线 B（Live2D），状态机定义（`MascotState` 枚举与 WS 协议）**完全复用，只换渲染适配器**。本项目是「会议秘书看板」而非二次元产品，路线 A 性价比最高且不拖累主流程性能。

---

## 4. 与既有文档/模块的关联

| 本设计环节 | 关联文档 |
|---|---|
| 表情触发源 = LangGraph 节点 | `Design_Agent_DataFlow.md` §2.2–§2.3（节点/边） |
| `reasoning` 状态对齐 LLM 请求生命周期 | `Design.md` §6.2（统一 LLM 接入接口） |
| 状态推送走 WS 通道 | `Research_CursorIntent_Capture.md`（光标流同源通道） |
| 眼睛追踪光标 = `NormCursorEvent` 同源事件源 | `Research_CursorIntent_Capture.md`（但用途分离：表现层 vs 意图层） |
| 看板娘容器进 X6 画布旁/悬浮层 | `Research_X6_MindMap.md`（React/Vue 节点/组件渲染） |
| `success/error` 终态回写与图更新同源 | `Design_StructureGraph_Storage.md`（Store A 更新） |

> **务必区分的两个「光标」**：`NormCursorEvent` 是把用户鼠标手势作为**Agent 输入意图流**（进 LangGraph、影响图推理）；而看板娘眼睛追踪是**纯前端视觉**（出 LangGraph、不进任何存储）。二者共享 `mousemove` 事件源，但数据流方向相反、用途隔离。文档 §2.4 的 `MascotController` 只消费本地 `mousemove`，不消费 `NormCursorEvent`。

---

## 5. 前端 MascotController 伪代码（路线 A 为例）

```typescript
class MascotController {
  private state: MascotState = "idle";
  private pupils: NodeListOf<Element>;

  mount() {
    // 眼睛追踪：始终运行，独立于表情状态
    window.addEventListener("pointermove", this.onPointerMove);
    // 状态订阅：来自后端 WS
    ws.on("mascot_state", (msg) => this.setState(msg.state, msg.label));
  }

  private onPointerMove = (e: PointerEvent) => {
    const R = 15;
    this.pupils.forEach((p) => {
      const rect = p.getBoundingClientRect();
      const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
      const a = Math.atan2(e.clientY - cy, e.clientX - cx);
      (p as HTMLElement).style.transform =
        `translate(${Math.cos(a) * R}px, ${Math.sin(a) * R}px)`;
    });
  };

  setState(state: MascotState, label: string) {
    this.state = state;
    this.root.className = `mascot m-${state}`;   // 切换表情 class（CSS 定义各状态脸/动作）
    this.bubble.textContent = label;             // 文案气泡
  }
}
```

---

## 6. 待确认

- Q1：**路线选型**——MVP 走 SVG/CSS（路线 A）还是直接 Live2D（路线 B）？建议 A，待老鸽拍板。
- Q2：`reasoning` 状态是否要细分「首 token 前 / 流式输出中」两档表情？取决于 LLM 流式接口是否暴露进度事件（见 `Design.md §6.2`）。
- Q3：看板娘在**事件触发空闲期**（两次批次之间）应保持 `idle` 还是记忆上次 `success`？建议回 `idle`。
- Q4：`error` 状态是否区分「解析失败重试中」与「降级终态」两种视觉？
- Q5：眼睛追踪在**鼠标移出窗口**时回正（neutral）还是保持最后位置？建议回正（避免斜视）。
- Q6：看板娘容器位置——悬浮右下角固定层 vs 嵌入 X6 画布侧边？影响与 `Research_X6_MindMap` 布局的耦合。
- Q7（路线 B）：是否需自备 Cubism 模型资产（授权/制作），还是用免费示例模型（如官方 `haru`/`shizuku`）占位？

---

## 7. 结论

- **表情状态机完全可行且低成本**：把 `Design_Agent_DataFlow.md` 的 10 个 LangGraph 节点映射为 10 个 `MascotState`，经 WS 事件驱动切换，与具体渲染引擎解耦。
- **眼睛追踪两种路线都成熟**：Live2D 用原生 `ParamEyeBallX/Y`（additive 叠加，天然与表情共存）；SVG 用 `atan2`+半径限制（零成本）。「始终追踪」靠全局 `pointermove` 监听独立运行，不受表情状态影响。
- **推荐 MVP = 路线 A（SVG/CSS）**：零授权零 WebGL，先把状态机+眼睛追踪跑通；状态机定义可无缝迁移到 Live2D。
- **边界已厘清**：眼睛追踪是表现层，与 `NormCursorEvent` 意图流共享事件源但用途隔离，不污染 Agent 数据流。
