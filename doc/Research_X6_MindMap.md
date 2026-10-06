# 调研文档：AntV X6 前端思维导图 / 结构图组件

> **文档状态**：调研 v1.0
> **最后更新**：2026-09-16
> **关联文档**：`doc/Design.md`（AI Conference Moderator 需求大纲 v0.3）
> **调研目标**：评估 AntV X6（`@antv/x6`，用户口中的「antx6」）能否作为本产品「会议结构图 / 思维导图」可视化与编辑底座，重点考察**编辑拖拽等基础功能接口**与**其他便于互动的功能**。

---

## 0. 结论摘要（TL;DR）

1. **「antx6」= AntV X6**，蚂蚁集团 AntV 团队的图编辑引擎（GitHub 6.5k★），基于 SVG/HTML 渲染，专为**流程图 / 思维导图 / 树形图 / DAG / ER 图**的编排与编辑设计。与本项目「看板结构图」的互动诉求高度匹配。
2. **编辑拖拽能力开箱即用且接口清晰**：
   - 节点移动 / 缩放 / 旋转：`resizing` / `rotating` / `translating` 全局配置即可；
   - 拖拽生成节点：`@antv/x6-plugin-dnd`（`dnd.start(node, e)`，支持 `validateNode` 异步校验、`keepId` 保 ID）；
   - **双击编辑文本：X6 内置 `node-editor` Tool**（contentEditable 覆盖层，无需自造 input），支持 `getText`/`setText` 自定义读写；
   - 画布平移缩放：`@antv/x6-plugin-scroller` + `graph.zoom()`/`zoomToFit()`；
   - 连线交互：`connecting` 配置（自动吸附 `snap`、合法性 `validateConnection`、路由/连接器）。
3. **互动插件生态完整**：Selection（框选）、Snapline（对齐线）、Transform（缩放旋转手柄）、Keyboard（快捷键）、Clipboard（复制粘贴）、History（撤销重做）、MiniMap（小地图）、Stencil（组件面板）、Export（导出 PNG/SVG）等 **10+ 内置插件**，均 `@antv/x6-plugin-*` 独立包。
4. **思维导图特有能力**：树形/放射布局（`@antv/hierarchy` 的 `mindmap`/`dendrogram` 等）、节点折叠/收缩（自定义 Group + `node:collapse` 事件 + `getDescendants().hide()/show()`）、编辑/查看模式切换（`interacting` 开关）、增删子节点（`addNode`/`addEdge`）。
5. **框架友好**：React（`@antv/x6-react-shape`）、Vue（`@antv/x6-vue-shape`）、Angular 节点均支持——意味着**结构图节点可直接用业务组件渲染**（如「要点卡片」带按钮/标签），这是本项目把 Agent 输出渲染成富交互看板的关键优势。
6. **选型注意**：X6 偏「编辑/编排」（SVG、易定制、支持组件节点）；若只需「只读大数据量展示」应看 G6（canvas、性能高）。本项目既要展示又要互动编辑 → **X6 更合适**。版本建议用 **v2.x**（生态成熟、文档多）；v3 将插件合并为内置 consolidated plugins，接口略有差异（见 §7）。

---

## 1. 它是什么 & 版本

| 项 | 说明 |
|---|---|
| 包名 | `@antv/x6`（npm），UMD/CDN 亦可 |
| 渲染 | SVG + HTML（节点可用 React/Vue 组件渲染） |
| 架构 | 数据驱动 MVC（Model/View/Controller），事件驱动 |
| 当前主流版本 | **v2.x**（本文接口以此为准，生态/文档最全） |
| v3 变化 | 插件合并为 11 个内置 `consolidated plugins`，不再单独装 `@antv/x6-plugin-*`；新增虚拟渲染（viewport culling，1000+ cells） |
| 许可 | MIT |
| 与 G6 区别 | G6 偏图分析可视化（canvas、大数据量）；X6 偏图编辑编排（SVG、组件节点、低代码）。**本项目互动编辑诉求 → X6** |

---

## 2. 基础编辑 & 拖拽功能接口（重点考察项）

### 2.1 节点移动 / 缩放 / 旋转

均为 Graph 构造时的全局开关：

```js
const graph = new Graph({
  resizing: { enabled: true, minWidth: 40, maxWidth: 400,
              minHeight: 30, preserveAspectRatio: false, orthogonal: true },
  rotating: { enabled: true, grid: 15 },          // 每次旋转 15°
  translating: { restrict: true },                // 限制节点不出画布
})
```

- `resizing`：八向缩放，可设 `min/maxWidth/Height`、`preserveAspectRatio`、`restricted`（是否可超出画布）、`orthogonal`（是否显示中间点）。
- `rotating`：`grid` 旋转步长。
- `translating.restrict`：限制移动范围（布尔 / Rectangle / 函数）。

### 2.2 连线交互（节点间连接）

`connecting` 配置控制连线规则（思维导图常用「父子连线」）：

```js
const graph = new Graph({
  connecting: {
    snap: { radius: 50 },                 // 距节点/桩 50px 自动吸附
    allowBlank: false,                    // 不允许连到空白
    allowLoop: false,                     // 不允许自环
    allowMulti: false,                    // 同起止只一条边
    router: 'manhattan',                  // 路由算法
    connector: { name: 'rounded' },       // 连接器（曲线/圆角…）
    createEdge() { return this.createEdge({ shape: 'edge', attrs: { line: { stroke: '#A2B1C3' } } }) },
    validateConnection({ sourceCell, targetCell }) { return !!sourceCell && !!targetCell && sourceCell !== targetCell },
    validateMagnet() { return true },     // 是否允许从此 magnet 拉线
  },
})
```

> 思维导图父子关系通常用**树形布局自动连线**，手动连线场景（自由分支）才用此配置。

### 2.3 拖拽生成节点（Dnd 插件）

从组件面板 / 任意 HTML 拖节点到画布：

```js
import { Graph, Dnd } from '@antv/x6'
const dnd = new Dnd({ target: graph })

// 拖拽开始时
dnd.start(graph.createNode({ shape: 'rect', width: 100, height: 40 }), e.nativeEvent)

// 进阶：自定义放置节点 + 异步校验 + 保持 ID
new Dnd({
  getDragNode: (node) => node.clone({ keepId: true }),
  getDropNode: (node) => node.clone({ keepId: true }),
  validateNode: async (node) => { /* 调接口校验，返回 boolean */ return true },
})
graph.on('node:added', ({ node }) => { const { x, y } = node.position() })
```

- `validateNode` 支持异步（如落库校验），不通过则回弹。
- 克隆默认重置 ID，`keepId: true` 保持原 ID。

### 2.4 ★ 双击编辑文本（内置 Editor Tool，推荐）

X6 自带 `node-editor` / `edge-editor` Tool，**基于 contentEditable 覆盖层**，无需自己监听 dblclick 造 input：

```js
const node = graph.addNode({
  x: 100, y: 100, width: 120, height: 40, label: '双击编辑我',
  tools: ['node-editor'],                 // 启用节点内联编辑
})

// 自定义读写（如文本存于 node.data.text 而非 label）
node.prop('tools', [{
  name: 'node-editor',
  args: {
    x: 10, y: 10,
    attrs: { fontSize: 16, fontFamily: 'monospace', color: '#333', backgroundColor: '#f0f0f0' },
    getText({ cell }) { return cell.attr('text/text') },
    setText({ cell, value }) { cell.attr('text/text', value) },
  },
}])
```

- 默认双击激活、失焦保存、`getText`/`setText` 自定义数据源。
- 边标签编辑用 `edge-editor`（含 `labelAddable` 新建标签）。
- **本项目可直接用此 Tool 实现「要点节点双击改名 / 改结论」**，省去自研。

### 2.5 画布平移 / 缩放（Scroller）

```js
import { Scroller } from '@antv/x6-plugin-scroller'
graph.use(new Scroller({
  enabled: true, pannable: true,          // 按住（可配 modifiers）拖拽平移
  autoResize: true,
}))
graph.zoom(0.8)                           // 缩放
graph.zoomToFit({ padding: 20, maxScale: 1 })  // 适应视图
graph.centerContent()                     // 居中内容
```

### 2.6 交互开关（编辑 / 查看模式）

用 `interacting` 精细控制每个元素是否可交互，天然支持「编辑模式 vs 查看模式」切换：

```js
const graph = new Graph({
  interacting({ cell }) {
    if (cell.getData()?.readonly) return { nodeMovable: false, magnetConnectable: false }
    return true
  },
})
// 或运行时切换插件开关：graph.disablePlugin('transform') / enablePlugin(...)
```

可用开关：`nodeMovable` / `magnetConnectable` / `edgeMovable` / `edgeLabelMovable` / `arrowheadMovable` / `vertexMovable/Addable/Deletable`。

---

## 3. 其他便于互动的插件（@antv/x6-plugin-*）

| 插件 | 能力 | 对本产品价值 |
|---|---|---|
| **Selection** | 框选 / 多选（rubberband）、`selection:changed` 事件 | 批量选中要点节点操作 |
| **Snapline** | 拖拽时显示对齐线，自动吸附 | 排版整齐，体验提升 |
| **Transform** | 选中节点显示缩放/旋转手柄 | 手动微调节点尺寸 |
| **Keyboard** | 快捷键（mousetrap），`Cmd/Ctrl+Z/C/V` 等 | 高效编辑（删除/复制/撤销） |
| **Clipboard** | 复制/剪切/粘贴，支持跨图 | 复用要点分支 |
| **History** | 撤销/重做，自动记录、`history:change` 事件、命令分组 | 误编辑可回退（看板编辑必备） |
| **Scroller** | 滚动条 / 拖拽平移 / 滚轮缩放 | 大图导航 |
| **MiniMap** | 缩略图 + 视口指示 + 点击导航 | 大结构图快速定位 |
| **Dnd** | 拖放生成节点（§2.3） | 从面板拉节点 |
| **Stencil** | 组件面板（分组/搜索），依赖 Dnd | 低代码物料库 |
| **Export** | 导出 PNG/JPEG/SVG（含背景色/选区/质量配置） | 导出会议纪要图、分享 |
| **Group**（核心能力） | 父子节点群组、拖入组合、`node:collapse` 展开/折叠 | 子树折叠（§4.2） |

典型启用（来自社区实战）：

```js
graph
  .use(new Transform({ resizing: true, rotating: true }))
  .use(new Selection({ rubberband: true, showNodeSelectionBox: true }))
  .use(new Scroller({ enabled: true, pannable: true }))
  .use(new MiniMap({ container: document.getElementById('minimap'), width: 200, height: 160 }))
  .use(new Snapline()).use(new Keyboard()).use(new Clipboard()).use(new History())
```

---

## 4. 思维导图特定能力

### 4.1 树形 / 放射布局

X6 通过布局库自动排布树结构，无需手算坐标：

- **`@antv/hierarchy`**：专为层级数据，含 `mindmap`（放射）、`dendrogram`（树状曲线）、`indented`（缩进）、`layered`（分层整齐）等布局，`direction: 'LR'|'TB'|'RL'|'BT'`，`getHeight/getWidth/getVGap/getHGap` 可调。
- **`@antv/layout`**：dagre（分层）、grid、force、circular 等，适合 DAG/组织架构。
- **X6 内置**：`graph.layout({ type: 'dendrogram', direction: 'LR', nodeSep: 40, rankSep: 100 })` 或 `type: 'mindmap'`。

```js
// 树/脑图一键切换（社区实战）
const changeLayout = (mode) =>
  mode === 'tree'
    ? graph.layout({ type: 'dendrogram', direction: 'LR', nodeSep: 40, rankSep: 100 })
    : graph.layout({ type: 'mindmap', direction: 'H', getHeight: () => 40 })
```

### 4.2 节点折叠 / 收缩（子树隐藏显示）

官方 Group 方案——自定义 Group 节点 + `node:collapse` 事件 + `getDescendants()`：

```js
graph.on('node:collapse', ({ node }) => {
  node.toggleCollapse()                       // 自定义方法：resize + 切换按钮图标
  const collapsed = node.isCollapsed()
  node.getDescendants().forEach((c) => collapsed ? c.hide() : c.show())
})
```

- Vue 节点版：监听自身 `change:visible`，向子节点递归 `show()/hide()` 蔓延（掘金实战）。
- 折叠后可在节点上标「+N」表示隐藏子节点数（ProcessOn 式交互）。

### 4.3 增删子节点 / 布局方向切换

- 增子节点：`graph.addNode(...)` + `graph.addEdge({ source, target })`，再 `graph.layout()` 重排。
- 删节点：`node.remove()` / `Backspace`（Keyboard 插件绑定）。
- 布局方向：重新 `graph.layout({ type, direction })` 即可 TB/BT/LR/RL 切换。

---

## 5. 框架集成与自定义节点（关键优势）

X6 节点可用 **React / Vue / Angular** 组件渲染，意味着结构图里的「要点卡片」可直接是业务组件（带标签、按钮、发言人头像、状态）：

```js
import { register } from '@antv/x6-vue-shape'
register({ shape: 'point-card', inherit: 'vue-shape', width: 240, height: 90,
           component: PointCardVue,            // 任意 Vue 组件
           ports: { groups: { in: {...}, out: {...} } } })
```

- `@antv/x6-react-shape` / `@antv/x6-vue-shape` / `@antv/x6-angular-shape`。
- 自定义节点支持端口（ports/magnet）做连线锚点、Tool 做内联操作按钮。
- **对本项目价值**：Agent 产出的「要点节点」可渲染为富交互卡片（点击展开详情、标记状态、关联到原文时间戳），远超静态 SVG 文本。

---

## 6. 数据驱动 & 序列化 & 事件

```js
graph.toJSON()                              // 序列化整图 → 存服务端
graph.fromJSON(json)                        // 反序列化渲染
graph.addNode({ id, x, y, label, data: {...} })   // data 存业务字段
node.prop('tools', [...]) / node.attr('text/text', v)
```

常用事件（驱动 Agent 联动）：

| 事件 | 触发 | 用途 |
|---|---|---|
| `node:dblclick` | 双击节点 | 触发编辑 / 看详情 |
| `node:added` / `node:removed` | 增删节点 | 同步业务状态 |
| `node:collapse` | 折叠/展开 | 子树显隐 |
| `cell:click` | 点击单元 | 选中联动面板 |
| `selection:changed` | 选区变化 | 更新工具栏 |
| `history:change` | 撤销栈变化 | 更新撤销/重做按钮可用态 |
| `edge:connected` | 连线完成 | 建立父子关系 |

---

## 7. 性能与边界

- **渲染**：SVG 为主，节点数 **数百~上千** 流畅；更大规模（万级）建议 G6 或 v3 虚拟渲染。
- **v3 vs v2**：v3 插件内置、新增 viewport culling（1000+ cells 虚拟渲染），但生态文档以 v2 为主。新项目建议先 v2 落地，关注 v3 迁移成本。
- **编辑 vs 展示**：X6 强在编辑/编排；若某视图只展示不编辑且数据大，可降级用 G6 或只读模式（`interacting: false` + `graph.disablePlugin`）。
- **心智负担**：插件多但各自独立，按需引入即可，无强制全装。

---

## 8. 对本项目（AI Conference Moderator 看板）的适配建议

1. **选型**：用 X6（v2.x）作为「会议结构图」编辑与展示底座，React/Vue 节点渲染 Agent 产出的要点卡片。
2. **编辑能力直接复用**：`node-editor` 做要点改名、`History` 撤销重做、`Selection`+`Keyboard` 批量操作、`Export` 导出纪要图。
3. **互动增强**：`MiniMap`+`Scroller` 大图导航、`Snapline` 排版、`Transform` 微调——均开箱即用。
4. **结构图自动化**：Agent 输出 → 映射为 `NormUtterance` 衍生的节点/边数据 → `graph.fromJSON()` 渲染 → `@antv/hierarchy` 自动布局；用户手动增删改后 `graph.toJSON()` 回存。
5. **折叠/收缩**：用 Group `node:collapse` 做分支收起，配合「+N」标记，适配长会议的深层级结构。
6. **编辑/查看模式**：会议进行中用「查看模式」（`interacting:false` 防误触），复盘/协作时切「编辑模式」。
7. **待验证**：v2/v3 版本锁定、大规模节点（>500）实测帧率、富交互卡片（React 节点）与 Agent 实时更新的性能。

---

## 9. 参考链接

| 内容 | 链接 |
|---|---|
| X6 官网 / 快速上手 | https://x6.antv.antgroup.com / https://x6.antv.antgroup.com/en/tutorial/getting-started |
| Dnd 拖拽插件 | https://x6.antv.antgroup.com/tutorial/plugins/dnd |
| 节点和边交互（resizing/rotating/connecting） | https://x6.antv.vision/zh/docs/tutorial/basic/interacting/ |
| Editor Tool（双击编辑） | https://deepwiki.com/antvis/X6/5.2-editor-tool |
| 群组 / 折叠展开 | https://x6.antv.antgroup.com/tutorial/intermediate/group |
| 插件系统总览 | https://deepwiki.com/fireflyshen/X6/3-plugin-system |
| 核心插件详解（Stencil/Transform/Selection/...） | https://juejin.cn/post/7553947486881644554 |
| @antv/hierarchy 布局 | https://blog.csdn.net/gitblog_00383/article/details/155529189 |
| X6 实现 ProcessOn 式思维导图 | http://youthcamp.bytedance.com/post/7256615625882599482 |
| X6 思维导图节点收缩 | https://juejin.cn/post/7283798844235808803 |
| Figma 风格 X6 编辑器（快捷键参考） | https://github.com/WorlesEnric/figma-x6-editor |
