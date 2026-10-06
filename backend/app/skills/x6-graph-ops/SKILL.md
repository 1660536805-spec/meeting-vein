---
name: x6-graph-ops
description: AntV X6 图编辑引擎（v2）操作 skill。覆盖建图、节点/边、连线（router/connector/anchor/connectionPoint/marker）、端口、交互工具、高亮、变换、动画、事件、序列化，以及与 antd 蓝白主题集成的规范做法和 v1→v2 归一化坑点。当用户在 AI_Meeting_Organizer 或任何前端项目中使用 @antv/x6 渲染思维导图/论证图/流程图/白板/关系图时使用；也适用于"X6 怎么加节点/怎么连线/边箭头怎么改/怎么接 antd 主题"等具体提问。
agent_created: true
---

# X6 图编辑引擎操作手册（v2 / antd 蓝白集成版）

本 skill 是 X6 常用操作的速查与代码模板库。参考文件：
- `references/cheatsheet.md` —— 能力全景 + 内置项速查
- `references/recipes.md` —— 可复制代码模板（建图/加图元/连线/主题/序列化等）
- `references/api.md` —— **完整接口签名**（本地 `@antv/x6@2.19.2` 类型定义抽取，Graph/Model/Cell/Node/Edge 共 520 个方法，权威且与运行时一致）
- `references/tips.md` —— **社区实战技巧与坑点汇总**（语雀 `sxd_panda/antv/x6` 踩坑总集 + 官方 2.x 常见问题 + 自定义路由实现）

详尽的调研与背景见 `doc/Research_X6_Graph.md`（本项目）。

## 0. 触发与前置
- 已安装 `@antv/x6`（本项目 `^2.18.1`，实际 2.19.2）。**核心库不含插件**，框选/变换/历史/导出需额外装 `@antv/x6-plugin-*`（见 cheatsheet §插件）。
- 导入：`import { Graph, Shape, Registry } from '@antv/x6'`。

## 1. 核心心智（必须遵守）
- **Node/Edge 的视觉 100% 由 `attrs`（SVG 属性映射）决定**；位置用顶层 `x/y/width/height`（v2 扁平结构）；业务数据放 `data`。
- MVP 一律用内置 `shape:'rect'` / `shape:'edge'` + `attrs`，**不要注册自定义 shape**，序列化与渲染最稳。
- 数据驱动：后端推 `{nodes, edges}` 或 `{cells}`，前端 `normalize()` 后 `graph.fromJSON(...)`。

## 2. 常用操作决策树
| 需求 | 做法 |
|------|------|
| 建画布 | `new Graph({ container, autoResize:true, background, grid, panning, mousewheel, interacting, connecting })` |
| 加节点 | `graph.addNode({ id, shape:'rect', x, y, width, height, attrs:{body,label}, data })` |
| 加边 | `graph.addEdge({ shape:'edge', source, target, attrs:{line:{stroke,targetMarker}}, data })` |
| 边绕行 | `connecting.router:'orth'` 或边元数据 `router:'manhattan'` |
| 边圆角/曲线 | `connector:'rounded'` / `'smooth'` |
| 改箭头 | `attrs.line.targetMarker:{ name:'block', width, height }` |
| 连线校验 | `connecting.validateConnection(args)=>boolean` |
| 端口 | 节点 `ports.groups/items` + `magnet:true` |
| 选中/删除按钮 | `node.addTools([{name:'button-remove'}])` |
| 锁编辑 | `interacting:false` 或 `{ nodeMovable:false }` |
| 适配视口 | `graph.fitToContent()` / `graph.centerContent()` |
| 批量改 | `graph.batchUpdate(()=>{...})` |
| 序列化 | `graph.toJSON()` / `graph.fromJSON(data)` / `toJSON({diff:true})` |
| 主题 | CSS 变量 + antd `defaultAlgorithm`（见 §4） |
| 多人光标 | `graph.clientToLocal(clientX, clientY)` 转换坐标 |

## 3. 能力清单（速查，详见 cheatsheet）
- 内置 shape：rect / ellipse / circle / polygon / polyline / path / image / html / text-block / edge
- Router：normal / orth / manhattan / metro / oneSide / er / loop
- Connector：normal / rounded / smooth / jumpover / loop
- NodeAnchor：center / top / bottom / left / right / topLeft / topRight / bottomLeft / bottomRight / bbox / midSide / orth / node-center
- Highlighter：stroke / class / opacity
- Grid：dot / fixedDot / mesh / doubleMesh
- Tool：NodeTool(boundary/button/button-remove/node-editor)、EdgeTool(boundary/vertices/segments/button/button-remove/source-anchor/target-anchor/source-arrowhead/target-arrowhead/edge-editor)
- Marker：block / classic / diamond / circle / circlePlus / ellipse / cross / async / path
- ConnectionPoint：anchor / bbox / rectangle / boundary / ellipse
- PortLayout：line / ellipse / absolute / side；PortLabelLayout：inner / outer / radial

## 4. antd 蓝白主题集成（必读，避免黑屏事故）
1. **坑**：`theme.defaultConfig.token.colorPrimaryBg` 等**派生 token 在 defaultConfig 里不存在**（仅 seed token）。直接写会得到 undefined → SVG/CSS 回退黑色（看板娘黑皮肤事故）。
   ✅ `const tokens = theme.defaultAlgorithm(theme.defaultConfig.token)`，再取 `tokens.colorPrimary` 等。
2. **注入 CSS 变量**（main.ts）：`--ant-primary` / `--ant-text` / `--ant-border` 写到 `:root`。
3. **render.ts 用 var 兜底**：
   ```ts
   const PRIMARY = "var(--ant-primary, #1677ff)"
   const TEXT    = "var(--ant-text, rgba(0,0,0,0.88))"
   const BORDER  = "var(--ant-border, #d9d9d9)"
   ```
   节点 `body.stroke=PRIMARY`、边 `line.stroke=PRIMARY`、背景 `#f0f5ff`、网格 `#d6e4ff`。
4. 换肤只需更新 CSS 变量 + `graph.draw()` 或重设 `attrs`。

## 5. 本项目（AI_Meeting_Organizer）约定
- 渲染层：`frontend/src/board/render.ts` —— `createGraph()` + `normalize()`(v1→v2) + `applyBoardUpdate`（首载入口，`renderBoard` 已删）。
- 论证图语义（业务层，非 X6 能力）：节点 `point/evidence/issue/conclusion/action/conflict`；边 `subordinate/support/oppose/duplicate/replace`。
- 数据流：后端 `Store A(cells)` → `GET /api/board` 全量 / `WS board.update` 增量 → 前端 `fromJSON`。
- [前端] 查看为主：默认 `interacting:{nodeMovable:true}`；进入编辑模式再引插件 `selection`+`transform`+`snapline`+`keyboard`+`history`。
- [常驻] `label` 精炼 ≤40 字符（渲染层截断）；正文细节走 `meta_ids`；布局坐标由层级自动派生，op 不携带坐标。渲染层为列式布局：根下不同意见（一级分支）横向排列成列便于对比，每列内支持论据自上而下纵向堆叠——因此观点层宽度即横向列数，论据只会增加列深，深层级优先继续归组到本列。
- [常驻] 树形层级：边箭头 source(父)→target(子)；每个节点仅一条父边。
- [常驻] 排版预算：论证树深度以 3 层为主（issue→point→evidence）；单层平行节点过多时靠归组消化（子议题/既有观点），平铺加宽是反模式；视觉强调只给 issue 根与少数 `importance:high` 节点（焦点 ≤2 原则），全图描边同色会抹掉层级信号。
- [边] 用户可拖弯边（`vertices` 持久化）、拖动节点落位（`position_frozen=true`）；AI 布局不得改写用户手动编辑过的节点/边。
- [cmd] 节点 `data.cmd={mark:"gray"|"strike", reason, by:"user"}`：gray=「暂不考虑」灰化（`#ececec` 底/`·暂不考虑`角标）、strike=删除线软删除，节点均保留在图；未被用户再次指令时，不要主动改写或清除已标记节点的 `cmd`。
- [大图] 节点超过 60 个时低重要度节点会被序列化截断：不得引用[当前看板]中未出现的节点 id，挂接只使用可见节点。
- [前端] 连线风格决策：本项目论证图用 `connector:'smooth'`（弧线）而非正交 elbow 总线——兄弟节点 >5 时正交总线交叉严重，密集子树场景弧线更可读（参见外部 diagram-design 技能的 type-tree 正交约定，仅作对比参照）；改风格前先在大图验证。

## 6. 高频坑点
- **v1→v2 归一化**：后端若是 `position{x,y}/size{w,h}/data.label/amo-node` 老结构，必须归一成 `{nodes,edges}` + 内置 `rect` 再 `fromJSON`，否则节点不渲染/黑屏。
- **`autoResize:true`**：容器尺寸变化后画布才刷新。
- **坐标转换**：屏幕/光标坐标经 `clientToLocal` 转画布坐标，才能正确落点。
- **大图**：千级节点开 `async:true` / `virtual:true`。
- **导出**：`toPNG/toSVG` 不在核心库，需 `@antv/x6-plugin-exporting`。

## 7. 执行步骤（主代理用）
1. 确认需求是"建图 / 加图元 / 改样式 / 连线 / 交互 / 主题 / 序列化"哪一类，查 §2 决策树。
2. 复制 `references/recipes.md` 对应片段，套本项目 `attrs` 蓝白变量。
3. 涉及自定义 shape/端口/工具时，优先用内置预设，避免注册复杂度。
4. 改完在 `frontend` 跑 `npm run dev` 验证渲染（浏览器 `localhost:5173`；注意 vite 绑 `localhost` 非 `127.0.0.1`）。

## 8. 实战技巧与官方 API（社区语雀汇总，必看）
社区踩坑总集（语雀 `sxd_panda/antv/x6`，同步博客园/CSDN）+ 官方 2.x 常见问题，已结构化整理在 `references/tips.md`，高频条目速记：

- **自定义拖拽源**：用 `@antv/x6-plugin-dnd`（Stencil 内部也基于它）；`dnd` 在 `graph` 之后创建，`mousedown` 里 `graph.createNode(...)` + `dnd.start(node, e)`。
- **图片导出不显示**：HTML 节点 `<img>` 必须是 **base64**，`toPNG` 才画得出；导出用 `stylesheet` 补回丢失样式（已知 bug）。
- **节点图片边框闪动**：`DataUri.imageToDataUri` 是异步的，先赋远程地址、回调再换 base64。
- **接口数据后画布不居中**：`centerContent()` 要在 `nextTick`/接口回调里调用；2.15.1+ 可监听 `graph.on('render:done', ...)`（覆盖 `fromJSON`/`resetCells`）。
- **右键菜单坐标错**：用 `graph.clientToGraph(clientX, clientY)` 转画布坐标，别用 `node.position()`。
- **历史忽略某属性**：history 配置 `beforeAddCommand` 拦截带 `{ ignoreHistory:true }` 的变更。
- **注册节点重复报错**：`Graph.registerNode(name, opts, true)`（第三参 `true` 覆盖）。
- **节点只能画布内移动**：`translating:{ restrict:true }`。
- **布局后不对齐**：布局算法返回**中心点**，X6 `x/y` 是**左上角**，需 `n.x -= size.w/2; n.y -= size.h/2`。
- **x/y 必须为数字**：字符串会在拖动时报错。
- 完整 15 类技巧（含自定义路由、获取父级节点、边连接校验、`allowMulti` 等）见 `references/tips.md`。

**全量接口查询**：写代码需要某方法签名/参数时，直接查 `references/api.md`（Graph 163 / Model 60+ / Cell / Node / Edge 共 520 个方法，按类分组 + 返回类型）。例如 `clientToGraph`、`centerContent`、`getConnectedEdges(node,{incoming,deep})`、`toJSON`/`fromJSON`、`addTools` 等均可在此核对精确签名。
