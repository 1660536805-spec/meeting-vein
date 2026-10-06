# AntV X6 图编辑引擎调研文档（antd 蓝白主题集成版）

> 调研对象：AntV X6（图编辑引擎 / Graph Editing Engine）
> 版本事实：本项目 `frontend` 实际安装 **@antv/x6@2.19.2**（`package.json` 声明 `^2.18.1`），仅引入核心库，**未引入**任何 `@antv/x6-plugin-*` 官方插件包。
> 配套前端：React 19 + antd ^6.6.5 + Vite 5。
> 调研目的：①梳理 X6 支持的全部能力；②给出与 antd 蓝白主题集成的规范做法；③沉淀本项目（AI_Meeting_Organizer 论证图 / 思维导图）的集成模式与坑点。

---

## 1. 核心心智模型

| 概念 | 说明 | 本项目对应 |
|------|------|-----------|
| `Graph` | 画布实例，所有操作的入口 | `frontend/src/board/render.ts::createGraph` |
| `Cell` | 画布上一切图元的基类（Node 与 Edge 的父类） | 后端 Store A 的 cell |
| `Node` | 节点（点状图元） | 论证图节点 point/conclusion/issue/action/evidence |
| `Edge` | 边（连线图元） | 论证关系 support/oppose/derive |
| `Port` | 节点上的连接桩（可多个，分组布局） | 当前未使用 |
| `Tool` | 悬浮在 Cell 上的交互工具（按钮/顶点/边界） | 当前未使用 |
| `Registry` | 全局注册中心，router/connector/anchor/shape 等均可注册扩展 | 当前仅用内置预设 |
| `Plugin` | 独立 npm 包，增强能力（框选/变换/历史等） | 当前未安装 |

X6 的"数据驱动"原则：**Node/Edge 的视觉完全由 `attrs`（SVG 属性映射）描述**，位置/尺寸由 `position/size` 或 `x/y/width/height` 描述，业务数据放 `data`。画布只负责渲染与交互。

---

## 2. 能力全景总表（X6 支持的全部功能）

| 分类 | 能力 | 关键 API / 配置 |
|------|------|----------------|
| 画布 | 创建/销毁/尺寸/自动缩放 | `new Graph(...)` / `autoResize` / `dispose` |
| 画布 | 背景、网格 | `background` / `grid` |
| 画布 | 平移、滚轮缩放、限制缩放范围 | `panning` / `mousewheel` / `scaling{min,max}` |
| 图元 | 10 种内置 shape | rect / ellipse / circle / polygon / polyline / path / image / html / text-block / edge |
| 图元 | 自定义节点/边（注册 shape 或 markup） | `Graph.registerNode` / `Graph.registerEdge` |
| 图元 | 端口 Ports（分组 + 布局 + 标签布局） | `ports.groups` / `PortLayout` / `PortLabelLayout` |
| 连线 | 路由 Router（7 种） | normal / orth / manhattan / metro / oneSide / er / loop |
| 连线 | 连接 Connector（5 种） | normal / rounded / smooth / jumpover / loop |
| 连线 | 锚点 Anchor（节点/边各一套） | center / top / bottom / left / right / bbox / midSide / orth / node-center … |
| 连线 | 连接点 ConnectionPoint | anchor / bbox / rectangle / boundary / ellipse |
| 连线 | 箭头 Marker（边末端） | block / classic / diamond / circle / circlePlus / ellipse / cross / async / path |
| 连线 | 边标签、多边、双向、折线顶点 | `labels` / `vertices` / `source/target` |
| 交互 | 移动/连线/嵌入/选择 | `interacting` / `connecting` / `embedding` |
| 交互 | 交互工具 Tools（节点/边） | NodeTool / EdgeTool 预设 |
| 交互 | 高亮 Highlighter | stroke / class / opacity |
| 变换 | 缩放、平移、适配内容、居中 | `zoom` / `translate` / `fitToContent` / `centerContent` |
| 动画 | 节点/边属性补间动画 | `cell.transition` / `cell.animate` |
| 事件 | 节点/边/画布/空白/端口事件 | `graph.on('node:*' / 'edge:*' / 'blank:*' / 'cell:*')` |
| 序列化 | 导入/导出 JSON、diff 增量 | `fromJSON` / `toJSON({diff:true})` |
| 导出 | 导出 SVG/PNG（需插件） | `@antv/x6-plugin-exporting` |
| 插件生态 | 选择/变换/对齐线/键盘/剪贴板/历史/小地图/拖拽/图元面板/Markdown/Widget | `@antv/x6-plugin-*` |
| 主题 | 通过 `attrs` + CSS 变量对接 antd token | 见 §6 |

---

## 3. 画布 Graph

### 3.1 构造配置（`new Graph(options)`）
全部配置项（来自 `graph/options.d.ts` 的 `Options.Manual`）：

| 配置 | 类型 | 作用 |
|------|------|------|
| `container` | HTMLElement | 挂载容器（必填） |
| `width` / `height` | number | 画布尺寸；不填则自适应容器 |
| `autoResize` | boolean \| Element \| Document | 随容器/窗口自动 resize（**强烈建议开启**） |
| `background` | false \| {color, image, ...} | 画布背景色/图 |
| `grid` | false \| bool \| number \| 对象 | 网格；`type: 'dot'\|'fixedDot'\|'mesh'\|'doubleMesh'` |
| `panning` | bool \| {enabled?} | 拖拽空白平移画布 |
| `mousewheel` | bool \| {enabled, modifiers, factor, minScale, maxScale} | 滚轮缩放；`modifiers:['ctrl']` 表示按住 Ctrl |
| `scaling` | {min, max} | 缩放范围限制 |
| `interacting` | bool \| function \| 对象 | 精细控制节点/边是否可交互（移动、连线、编辑等） |
| `connecting` | 对象 | 连线规则（见 §5） |
| `translating` | {restrict, autoOffset} | 限制节点移动范围 |
| `embedding` | bool \| 对象 | 节点嵌套（父子关系） |
| `highlighting` | 对象 | 各交互态的高亮器 |
| `async` | bool | 异步渲染（大数据量优化） |
| `virtual` | bool | 虚拟渲染（仅渲染视口内元素，超大规模图） |
| `preventDefault*` | bool/function | 屏蔽默认双击/右键/拖拽等行为 |
| `onPortRendered` / `onEdgeLabelRendered` | callback | 端口/边标签自定义渲染钩子 |

本项目 `createGraph` 实际配置：
```ts
new Graph({
  container,
  background: { color: "#f0f5ff" },
  grid: { visible: true, type: "dot", size: 16, args: { color: "#d6e4ff", thickness: 1 } },
  interacting: { nodeMovable: true },     // 查看模式可置 false
  connecting: { allowBlank: false, snap: true },
  panning: true,
  mousewheel: { enabled: true, modifiers: ["ctrl"] },
})
```

### 3.2 实例方法（已核对 `graph.d.ts`）
**数据增删查**：`addNode` / `addNodes` / `addEdge` / `addEdges` / `removeCell` / `removeCells` / `getCellById` / `getCell` / `getCells` / `getNodes` / `getEdges` / `getNodesFromPoint` / `getNodesInArea` / `getNodesUnderNode`。
**序列化**：`fromJSON(data)` / `toJSON({diff?:boolean})`。
**变换**：`zoom()` / `zoom(factor, opts)` / `translate()` / `translate(tx,ty)` / `translateBy(dx,dy)` / `fitToContent(opts)` / `centerContent(opts)` / `transformToFit`。
**坐标转换**：`clientToLocal` / `localToClient` / `pageToLocal` / `localToPage` / `graphToLocal` / `localToGraph`。
**批量**：`batchUpdate(name, fn, data)` —— 合并多次变更、一次性渲染，性能关键。
**生命周期**：`dispose(clean?)` / `disposePlugins`。
**事件**：`on` / `off` / `once` / `emit`（见 §9）。
> 注：导出 SVG/PNG/JPEG、小地图、框选等**不在核心库**，由 `@antv/x6-plugin-*` 提供（见 §10）。

---

## 4. 节点 Node

### 4.1 内置 shape（`lib/shape/index.d.ts` 导出）
`rect` / `ellipse` / `circle` / `polygon` / `polyline` / `path` / `image` / `html` / `text-block` / `edge`。
- `html`：节点内容用 DOM 渲染（可放 React 组件 / antd 组件），适合富交互卡片。
- `text-block`：自动换行的文本块（需指定 `text` 与 `width`）。
- 自定义：不注册也能用——本项目用 `shape:'rect'` 配合 `attrs` 表达所有节点类型，最稳。

### 4.2 节点元数据（Metadata）关键字段
```ts
{
  id, shape: 'rect',
  x, y, width, height,            // v2 顶层扁平结构
  zIndex,
  attrs: {                        // SVG 属性映射，核心视觉定义
    body:   { fill, stroke, strokeWidth, rx, ry, ... },
    label:  { text, fill, fontSize, fontWeight, refX, refY, textAnchor, textVerticalAnchor, ... },
  },
  ports: { groups: {...}, items: [...] },   // 连接桩
  data: { ... },                  // 业务数据（论证图：type/label/relation 等）
}
```
- **`attrs` 是视觉核心**：body 控制外形、label 控制文字、`text` shape 还有 `text` 字段。
- `refX/refY` 是相对节点宽高的比例（0~1）定位标签；`textAnchor`/`textVerticalAnchor: 'middle'` 居中。

### 4.3 端口 Ports（本项目当前未用，论证图进阶可选）
```ts
ports: {
  groups: {
    top:    { position: 'top', attrs: { circle: { r: 4, magnet: true, fill: '#1677ff' } } },
    bottom: { position: 'bottom', attrs: { circle: { r: 4, magnet: true } } },
  },
  items: [ { id: 'p1', group: 'top' }, { id: 'p2', group: 'bottom' } ],
}
```
- `PortLayout`（位置分布）：`line` / `ellipse` / `absolute` / `side`。
- `PortLabelLayout`（标签分布）：`inner` / `outer` / `radial`。
- `magnet: true` 表示该端口可作为连线起点/落点。

---

## 5. 边 Edge 与连线 connecting

### 5.1 边元数据
```ts
{
  id, shape: 'edge',
  source: { cell, port? }, target: { cell, port? },
  // 或 source/target: { x, y }（空白点）
  vertices: [{ x, y }],      // 折线拐点
  zIndex: 0,
  attrs: {
    line: { stroke, strokeWidth, strokeDasharray, targetMarker: { name, width, height }, ... },
    label:{ text, fill, fontSize, background:{ fill, stroke, padding } },
  },
  labels: [...],             // 多个边标签
  router: 'orth',            // 路由
  connector: 'rounded',      // 连接
  data: { relation: 'support' },
}
```

### 5.2 路由 Router（边如何绕行）
| 名称 | 行为 |
|------|------|
| `normal` | 直线/默认 |
| `orth` | 正交（直角折线），适合流程图 |
| `manhattan` | 曼哈顿（绕开障碍节点的正交路由） |
| `metro` | 地铁式折线路由 |
| `oneSide` | 单侧绕行 |
| `er` | 实体关系图风格 |
| `loop` | 自环 |

### 5.3 连接 Connector（边两端如何连接）
`normal` / `rounded`（圆角） / `smooth`（曲线） / `jumpover`（跨越时跳线） / `loop`。

### 5.4 锚点 / 连接点
- **NodeAnchor**（边连到节点的锚点位置）：`center` / `top` / `bottom` / `left` / `right` / `topLeft` / `topRight` / `bottomLeft` / `bottomRight` / `bbox` / `midSide` / `orth` / `node-center`。
- **EdgeAnchor**（边连到另一条边的锚点）：同族。
- **ConnectionPoint**（锚点与实际路径的交点修正）：`anchor` / `bbox` / `rectangle` / `boundary` / `ellipse`。

### 5.5 箭头 Marker（边末端）
`block` / `classic` / `diamond` / `circle` / `circlePlus` / `ellipse` / `cross` / `async` / `path`。
```ts
targetMarker: { name: 'block', width: 9, height: 9 }
```

### 5.6 connecting 配置（交互连线规则）
来自 `Options.Connecting`：
`snap`(磁吸半径) / `allowBlank` / `allowLoop` / `allowNode` / `allowEdge` / `allowPort` / `allowMulti` / `highlight` / `anchor` / `sourceAnchor` / `targetAnchor` / `connectionPoint` / `router` / `connector` / `createEdge` / `validateMagnet` / `validateEdge` / `validateConnection`。
- `validateConnection(args)` 是连线校验核心回调，决定哪些连接合法（本项目论证图可用它限制 support/oppose 不能自连）。

---

## 6. 与 antd 蓝白主题集成（本项目规范做法）

### 6.1 踩过的坑（已修复，记录防复发）
1. **antd 派生 token 为 undefined 会变黑**：`theme.defaultConfig.token.colorPrimaryBg` 等**派生 token 在默认 token 对象里不存在**（defaultConfig 只含 seed token）。直接写进 SVG/CSS 会得到非法值 → 浏览器回退为黑色（看板娘黑皮肤事故）。
   ✅ 正确做法：`const seed = theme.defaultConfig.token; const tokens = theme.defaultAlgorithm(seed);` 用 `defaultAlgorithm` 展开出完整 token 再取用。
2. **硬编码暗色色值变黑脸/黑屏**：面罩曾写死 `#001529`（antd 暗色近黑）。
   ✅ 改为 `var(--ant-primary)`（antd 主蓝）。

### 6.2 推荐集成链路
```
antd ConfigProvider/theme
   └─ main.ts: const tokens = theme.defaultAlgorithm(theme.defaultConfig.token)
   └─ 注入 CSS 变量到 :root:
        --ant-primary: tokens.colorPrimary
        --ant-text:    tokens.colorText
        --ant-border:  tokens.colorBorder
   └─ render.ts 用 var() 兜底:
        const PRIMARY = "var(--ant-primary, #1677ff)"
        const TEXT    = "var(--ant-text, rgba(0,0,0,0.88))"
        const BORDER  = "var(--ant-border, #d9d9d9)"
```
- 节点 `body.stroke = PRIMARY`、`label.fill = TEXT`、边 `line.stroke = PRIMARY`、网格/背景用主蓝浅色 (`#f0f5ff` / `#d6e4ff`)。
- 主题切换/换肤时只需更新 CSS 变量，X6 通过 `graph.draw()` 或重设 `attrs` 刷新。
- 若用 `html` shape 承载 React 节点，可直接用 antd `<Card>`/`<Tag>`，主题天然一致。

---

## 7. 交互 interacting & 工具 Tools

### 7.1 interacting
`interacting: true`（全开） / `false`（全锁，查看模式） / 函数 / 对象：
```ts
interacting: { nodeMovable: true, edgeMovable: false, arrowheadMovable: false, labelMovable: false }
```
常用键：`nodeMovable` / `edgeMovable` / `arrowheadMovable` / `vertexMovable` / `labelMovable` / `addNodeFromTools` 等。

### 7.2 Tools（预设清单，来自 `registry/tool/index.d.ts`）
**NodeTool**：`boundary`（选中边框）/ `button`（自定义按钮）/ `button-remove`（删除按钮）/ `node-editor`（双击编辑文本）。
**EdgeTool**：`boundary` / `vertices`（拖拽拐点） / `segments`（分段拖动） / `button` / `button-remove` / `source-anchor` / `target-anchor` / `source-arrowhead` / `target-arrowhead` / `edge-editor`。
用法：
```ts
node.addTools([{ name: 'button-remove', args: { x: '100%', y: 0 } }])
// 或 graph 级：graph.addCellTools(...)
```

### 7.3 Highlighter
`stroke` / `class` / `opacity` —— 用于 hover/选中/合法连接提示，配合 `highlighting` 配置按交互态自动应用。

---

## 8. 变换 / 动画 / 事件

- **变换**：`zoom` / `translate` / `fitToContent` / `centerContent` / `transformToFit`（适配视口）。
- **动画**：`node.transition('position', {x,y}, {duration})` 或 `cell.animate`；适合节点新增/移动的平滑过渡。
- **事件**（核心，驱动本项目"光标协同/实时更新"）：
  | 事件 | 触发 |
  |------|------|
  | `node:click` / `node:dblclick` / `node:mouseenter` / `node:mouseleave` | 节点交互 |
  | `edge:connected` / `edge:click` | 边交互 |
  | `cell:added` / `cell:removed` / `cell:changed` | 图元变更 |
  | `blank:click` / `blank:mousedown` / `blank:mousewheel` | 空白画布 |
  | `port:click` | 端口 |
  | `graph:*`（batch/scale/translate） | 画布级 |
  本项目 `cursor.ts` 基于 `Graph` 类型做光标协同；后端推送经 `graph.fromJSON` 全量覆盖（MVP），生产可改 `toJSON({diff:true})` 精细 patch。

---

## 9. 序列化（数据契约关键）

- 导出：`graph.toJSON()` → `{ cells: [...] }`（X6 v2 实际结构是 `{ cells }`？见坑点）。
- 导入：`graph.fromJSON(data)`。
- **v1 vs v2 结构坑（本项目已踩并修复）**：后端 Store A 的 cell 早期用 **X6 v1 风格 + 自定义 shape `amo-node`**，与 v2 期望结构不同：
  | 字段 | v1 | v2（X6 期望） |
  |------|----|--------------|
  | 位置/尺寸 | `position{x,y}` / `size{w,h}` | 顶层 `x/y/width/height` |
  | 显示文字 | `data.label` | `attrs.label.text` |
  | 自定义 shape | `amo-node` | 需归一化为内置 `rect` |
  | 包结构 | `{ cells: [...] }` | `{ nodes, edges }` |
  ✅ 本项目 `render.ts::normalize()` 做 v1→v2 归一化，再 `fromJSON({nodes, edges})`，零歧义。
- 后端→前端数据契约：建议统一为 `{ nodes:[...], edges:[...] }` 或 `{ cells:[...] }`（二选一并写进 `Design_StructureGraph_Storage`），前端归一化层保持一致即可。

---

## 10. 插件生态（`@antv/x6-plugin-*`，本项目当前未安装）

| 插件 | 能力 | 本项目是否需要 |
|------|------|--------------|
| `selection` | 点选/框选/多选/拖拽多选 | 编辑模式需 |
| `transform` | 节点缩放/旋转手柄 | 编辑模式需 |
| `snapline` | 对齐辅助线 | 编辑模式推荐 |
| `keyboard` | 键盘快捷键（Delete/Ctrl+Z） | 编辑模式需 |
| `clipboard` | 复制/粘贴 | 可选 |
| `history` | 撤销/重做 | 编辑模式需 |
| `exporting` | 导出 SVG/PNG/JPEG | 分享图需 |
| `minimap` | 小地图 | 大图推荐 |
| `dnd` | 拖拽创建节点 | 图元面板需 |
| `stencil` | 图元面板（左侧素材库） | 编辑模式需 |
| `markdown` | Markdown 节点 | 暂不需要 |
| `widget` | 输入框/上下文菜单/气泡等 widgets | 节点内联编辑需 |

> 本项目当前为"查看为主 + CLI 推送驱动"模式，纯核心库即可；进入可视化编辑（拖拽建节点、撤销重做）时应引入 `selection`+`transform`+`snapline`+`keyboard`+`history`。

---

## 11. 本项目（AI_Meeting_Organizer）集成现状

- **已用**：`Graph` 实例化（background/grid/panning/mousewheel/interacting/connecting）、`fromJSON` 全量渲染、v1→v2 归一化、`attrs` 蓝白主题、边箭头 `block`。
- **未用**：插件、端口、工具、自定义 shape 注册、动画、序列化 diff、导出。
- **论证图语义层**（业务，非 X6 能力）：节点类型 `point/conclusion/issue/action/evidence`，边类型 `support/oppose/derive`（见 `Design_FrontendBoard` / `Design_StructureGraph_Storage`）。
- **数据流**：ASR+光标 → Analysis/Planning Agent → `GraphUpdateOp` → `Store A(cells)` → `GET /api/board` 全量 / `WS board.update` 增量 → 前端 `renderBoard`/`applyBoardUpdate`。

---

## 12. 最佳实践与坑点速查

1. **v1→v2 归一化**：后端 cell 若是老结构（`position/size/data.label/amo-node`），必须归一化为 `{nodes,edges}` + 内置 `rect` 再 `fromJSON`，否则节点不渲染或黑屏。
2. **避免自定义 shape 注册负担**：MVP 一律用内置 `rect`/`edge` + `attrs`，不 `registerNode`，最稳、最易序列化。
3. **主题用 CSS 变量 + `defaultAlgorithm` 展开**，不要直接读 `defaultConfig.token` 的派生项（会 undefined→黑）。
4. **`autoResize` 开启**：避免容器尺寸变化后画布不刷新。
5. **批量更新用 `batchUpdate`**：多次增删改包一层，减少重渲染。
6. **查看模式锁交互**：`interacting:false` 或精细关掉 `nodeMovable`，避免误拖。
7. **大图用 `async`/`virtual`**：千级节点开启虚拟渲染。
8. **连线校验用 `validateConnection`**：论证图限制非法关系（如自连、类型冲突）。
9. **坐标转换用 `clientToLocal`**：处理鼠标/光标协同（多人光标）时必须，屏幕坐标≠画布坐标。
10. **导出需插件**：`toPNG` 等不在核心库，引入 `@antv/x6-plugin-exporting`。

---

## 13. 参考
- 官方文档：https://x6.antv.antgroup.com/ （SPA，WebFetch 抓不到，需浏览器查阅）
- 本项目代码：`frontend/src/board/render.ts`、`frontend/src/board/cursor.ts`、`frontend/src/main.ts`（antd token 注入）
- 设计文档：`doc/Design_FrontendBoard.md`、`doc/Design_StructureGraph_Storage.md`、`doc/Research_KanbanMusume.md`
- 依赖事实：`frontend/node_modules/@antv/x6@2.19.2`（已核对 `index.d.ts` / `options.d.ts` / `shape` / `registry` / `graph/graph.d.ts`）
