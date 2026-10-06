# AntV X6 实战技巧与坑点汇总

> 来源：语雀社区实践帖 —— `sxd_panda/antv/x6`（踩坑总集，原文同步于博客园/CSDN）+ 官方 `antv/x6/tox1ukbz5cw57qfy`（2.x 常见问题）+ 社区自定义路由实现。
> 适用版本：X6 2.x（本项目 `@antv/x6@2.19.2`）。

---

## 1. 自定义拖拽源（Dnd 插件）

**场景**：从左侧树/列表/任意 HTML 元素拖节点进画布，Stencil 内部也是基于 Dnd 实现，直接用 Dnd 更灵活。

**要点**：
- `dnd` 必须在 `graph` 初始化之后创建。
- 拖拽源元素绑定 `mousedown`，在回调里 `graph.createNode(...)` 然后 `dnd.start(node, e)`（`$event` 必传）。
- 自定义节点用 `Shape.HTML.register({ shape, html(cell){...} })`，通过 `cell.getData()` 取业务数据。

```javascript
let dnd = null
dnd = new Dnd({ target: graph, scaled: false, dndContainer: ref.dndContainer })

const startDrag = (e, node, data) => {
  const node = graph.createNode({
    shape: 'cu-data-node', width: 150, height: 104,
    label: data?.label,
    data: { label: data?.label, img: data?.img, desc: data?.desc },
    ports: { ...port, items: [{ group: 'top' }] },
  })
  dnd.start(node, e)
}
// 模板： <div @mousedown="startDrag($event, node, data)">拖拽的节点</div>
```

> ⚠️ 注意：`x6-plugin-dnd` 是独立插件包，核心库不含，需额外安装并在 `Graph` 前 `register`。

---

## 2. 本地图片导出后不显示（toPNG / toSVG）

**原因**：HTML 节点的 `<img>` 必须是 **base64（DataURI）** 格式，`toPNG` 才能把它画进导出图；远程 URL 图片导不出。

**解决**：
1. 用 `DataUri.imageToDataUri(url, (nu, base64) => { img.src = base64 })` 转 base64（第一个参数占位但必填）。
2. 导出时通过 `stylesheet` 补回节点样式（官方已知 bug：导出会丢样式）：

```javascript
graph.toPNG(dataUri => { /* base64 图片 */ }, {
  width: 526, height: 268,
  backgroundColor: 'rgba(25, 87, 121, 0.18)',
  quality: 1,                       // 0-1，默认 0.92
  // copyStyles: false,
  stylesheet: `.cu-container{display:flex;...}.cu-container-title{color:#d3e6f3;...}`,
})
```

> Tip：若甲方不挑交互，`copyStyles: true` 可省去手写 `stylesheet`；否则只能反复调 `stylesheet` 逼近原样式。

---

## 3. 节点图片边框闪动（HTML 节点）

**原因**：`DataUri.imageToDataUri` 是**异步**的，直接把 base64 赋给 `src` 会在渲染瞬间出现边框闪动（拖拽/首次进入页面时明显）。

**解决**：先赋远程地址，回调里再替换成 base64：

```javascript
const img = document.createElement('img')
img.src = remoteUrl                 // 先用普通地址，避免闪动
DataUri.imageToDataUri(remoteUrl, (nu, url) => { img.src = url })  // 再换 base64 便于存后端
```

---

## 4. 接口数据渲染后画布无法居中

**原因**：`centerContent()` 时机已过——先居中、后异步 `fromJSON`/`addNode`，节点按自身坐标排布。

**解决**：等 DOM/数据就绪后再居中。

```javascript
// Vue
import { nextTick } from 'vue'
nextTick(() => graph.centerContent())

// 或在接口回调里
api.then(res => { graph.zoom(-0.1); graph.centerContent() })
```

> 🆕 2.15.1+ 新增 `render:done` 事件，可监听 `fromJSON`/`resetCells` 渲染完成时机后再居中：
> `graph.on('render:done', () => graph.centerContent())`

---

## 5. 自定义右键菜单坐标不准

**原因**：用 `node.position()` 取的是画布坐标，画布平移后该坐标不变，菜单仍停在旧位置。

**解决**：用 `clientToGraph(clientX, clientY)` 把鼠标屏幕坐标转画布坐标（1.9+ 新增）：

```javascript
graph.on('node:contextmenu', ({ e, node }) => {
  const pos = graph.clientToGraph(e.clientX, e.clientY)  // 核心
  createMenuDom({ x: pos.x, y: pos.y, node, type: 0 })
})
```

---

## 6. 历史记录忽略某个属性的修改（History 插件）

**场景**：某些属性变更（如 `attr` 高亮、业务 `data`）不想进 undo/redo 栈。

**解决**：history 配置 `beforeAddCommand` 拦截带 `ignoreHistory` 标记的变更。

```javascript
new Graph({
  history: {
    enabled: true,
    beforeAddCommand(event, args) {
      if (args.options.ignoreHistory) return false
    },
  },
})
// 边： edge.attr('line/strokeDasharray', null, { ignoreHistory: true })
// 节点：node.setData({...}, { ignoreHistory: true })
```

---

## 7. x / y 坐标为字符串报错

**原因**：`x`、`y` 必须是纯数字，字符串会在拖动时报错。

**解决**：接入数据前 `Number(x)`、`Number(y)`。

---

## 8. 限制节点只能在画布内移动

```javascript
new Graph({ translating: { restrict: true } })
```

---

## 9. 注册自定义节点「already registered」报错

**原因**：`Graph.registerNode(name, opts)` 重复注册抛错。

**解决**：第三个参数 `true` 覆盖已有注册：

```javascript
Graph.registerNode('cu-port', options, true)   // ✅
// Graph.registerNode('cu-port', options)       // ❌ 重复报错
```

---

## 10. 获取节点的所有父级/相邻节点

```javascript
const edges = graph.getConnectedEdges(node)                       // 输入+输出边
const edges = graph.getConnectedEdges(node, { incoming: true })     // 仅输入边
const edges = graph.getConnectedEdges(node, { outgoing: true })     // 仅输出边
const edges = graph.getConnectedEdges(node, { deep: true })         // 含子孙节点边
const edges = graph.getConnectedEdges(node, { indirect: true })     // 含间接连接边
// 遍历 edge.getSourceNode() / edge.getTargetNode() 取相邻节点
```

---

## 11. 边连接时校验目标节点（allowMulti / edge:connected）

```javascript
// 同一源只能连一次
new Graph({ connecting: { allowMulti: false } })

graph.on('edge:connected', ({ isNew, edge, currentCell }) => {
  if (currentCell.data?.type === 0) {            // 业务校验
    graph.removeEdge(edge?.id)
  }
})
```

---

## 12. 常用方法集锦

| 需求 | API |
|------|-----|
| 清空画布 | `graph.clearCells()` |
| 缩小/放大 | `graph.zoom(-0.5)` / `graph.zoom(0.5)` |
| 取当前缩放 | `graph.zoom()` |
| 所有节点 | `graph.getNodes()` |
| 所有边 | `graph.getEdges()` |
| 节点移动范围 | `translating: { restrict: true }` |
| 动态设节点位置 | `node.position(x, y)` |
| 取节点连接边 | `graph.getConnectedEdges(node)` |
| 序列化 | `graph.toJSON()` / `graph.fromJSON(data)` |
| 导出图 | `graph.toPNG(cb, opts)` / `graph.toSVG(cb, opts)` |
| 屏幕→画布坐标 | `graph.clientToGraph(x, y)` |
| 内容居中 | `graph.centerContent()` |

---

## 13. 官方 2.x 常见问题（antv/x6/tox1ukbz5cw57qfy）

1. **x6-react-shape React 版本支持**：React17 → `2.0.8 < x6-react-shape < 2.1.1`；React18 → `x6-react-shape > 2.1.1`（不支持同时兼容）。
2. **监听画布渲染完成**：2.15.1+ 用 `graph.on('render:done', ...)`（覆盖 `fromJSON`/`resetCells`）。
3. **布局后节点不对齐**：布局算法返回的是**中心点**坐标，X6 节点 `x/y` 是**左上角**坐标，需转换：
   ```javascript
   const data = gridLayout.layout(origin)
   data.nodes.forEach(n => { n.x -= n.size.width / 2; n.y -= n.size.height / 2 })
   ```
4. **vite 下引入 `@antv/x6-plugin-keyboard` 构建报错**：参考 issue #3418 解决（通常是 ESM/CJS 兼容或 vite optimizeDeps 配置问题）。

---

## 14. 进阶：手动实现自定义路由（manhattan 不理想时的兜底）

**场景**：数据模型要求直角连线，`manhattan` 在近距离出现不理想路径、`orth` 也有问题。

**思路**：连线后重算路径点，平行/近距离用 `manhattan`，其余场景按源/目标锚点方向手算最多 4 个拐点。

```javascript
const { x: spx, y: spy } = edge.getSourcePoint()
const { x: tpx, y: tpy } = edge.getTargetPoint()
if (spx === tpx || spy === tpy) {            // 平行：清路由与路径点
  edge.removeRouter()
  edge.getVertices().forEach(() => edge.removeVertexAt(0))
  return
}
if (Math.abs(tpx - spx) < 60 || Math.abs(tpy - spy) < 70) {  // 太近：manhattan
  edge.getVertices().forEach(() => edge.removeVertexAt(0))
  edge.setRouter({ name: 'manhattan', args: { step: 10, padding: 10 } })
  return
}
// 否则按源/目标方向手算 vertices（见源帖），并清除重复点避免拖动工具异常
```

> 完整实现见语雀 `u34618840/psoa5c/egfb5tv4fnb9p67t`。

---

## 15. 本项目（AI_Meeting_Organizer）已踩 + 已用的坑

- **v1→v2 序列化差异**：后端老结构用 `fromJSON({ cells })` + 自定义 `amo-node`；X6 v2 必须 `fromJSON({ nodes, edges })` 且用内置 `rect`/`edge`。已在 `frontend/src/board/render.ts` 做归一化（摊平 `position/size`→`x/y/width/height`、`data.label`→`attrs.label.text`）。
- **antd 蓝白主题**：① 派生 token（`colorPrimaryBg` 等）须用 `theme.defaultAlgorithm(seed)` 展开，直接读 `defaultConfig.token` 会是 `undefined` 导致 SVG 变黑；② 别硬编码暗色 `#001529`（看板娘黑脸）。详见 `references/cheatsheet.md`。
- **会议 id 对齐**：CLI 调试口与前端必须共用同一 `meeting_id`，否则前端白板不刷新。
