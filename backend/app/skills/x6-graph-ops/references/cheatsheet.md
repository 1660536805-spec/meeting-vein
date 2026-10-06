# X6 v2 速查表（Cheat Sheet）

> 基于本项目安装版本 `@antv/x6@2.19.2` 核对 `lib` 类型定义与源码 presets。

**配套参考**：
- 完整接口签名（Graph/Model/Cell/Node/Edge 共 520 方法）→ `api.md`
- 社区实战技巧与坑点（语雀踩坑总集 + 官方 2.x 常见问题）→ `tips.md`
- 可复制代码模板 → `recipes.md`

## 1. 内置 shape（节点图元）
`rect` · `ellipse` · `circle` · `polygon` · `polyline` · `path` · `image` · `html` · `text-block` · `edge`
- `html`：DOM 渲染，可嵌 React/antd 组件
- `text-block`：自动换行文本块（需 `text` + `width`）

## 2. 边路由 Router
`normal` · `orth` · `manhattan` · `metro` · `oneSide` · `er` · `loop`

## 3. 连线 Connector
`normal` · `rounded` · `smooth` · `jumpover` · `loop`

## 4. 锚点 Anchor
- NodeAnchor：`center` `top` `bottom` `left` `right` `topLeft` `topRight` `bottomLeft` `bottomRight` `bbox` `midSide` `orth` `node-center`
- EdgeAnchor：同族
- ConnectionPoint（交点修正）：`anchor` `bbox` `rectangle` `boundary` `ellipse`

## 5. 箭头 Marker
`block` · `classic` · `diamond` · `circle` · `circlePlus` · `ellipse` · `cross` · `async` · `path`

## 6. 高亮 Highlighter
`stroke` · `class` · `opacity`

## 7. 网格 Grid
`dot` · `fixedDot` · `mesh` · `doubleMesh`

## 8. 工具 Tools
- NodeTool：`boundary` `button` `button-remove` `node-editor`
- EdgeTool：`boundary` `vertices` `segments` `button` `button-remove` `source-anchor` `target-anchor` `source-arrowhead` `target-arrowhead` `edge-editor`

## 9. 端口布局
- PortLayout：`line` `ellipse` `absolute` `side`
- PortLabelLayout：`inner` `outer` `radial`

## 10. Graph 实例方法（常用，已核对 graph.d.ts）
**增删查**：`addNode` `addNodes` `addEdge` `addEdges` `removeCell` `removeCells` `getCellById` `getCell` `getCells` `getNodes` `getEdges` `getNodesFromPoint` `getNodesInArea` `getNodesUnderNode`
**序列化**：`fromJSON(data)` `toJSON({diff?:boolean})`
**变换**：`zoom()` `zoom(factor,opts)` `translate()` `translate(tx,ty)` `translateBy(dx,dy)` `fitToContent(opts)` `centerContent(opts)` `transformToFit`
**坐标**：`clientToLocal` `localToClient` `pageToLocal` `localToPage` `graphToLocal` `localToGraph`
**批量**：`batchUpdate(name, fn, data)`
**生命周期**：`dispose(clean?)` `disposePlugins`
**事件**：`on` `off` `once` `emit`

## 11. 画布构造配置项
`container`(必) · `width` `height` · `autoResize` · `background` · `grid` · `panning` · `mousewheel` · `scaling{min,max}` · `interacting` · `connecting` · `translating` · `embedding` · `highlighting` · `async` · `virtual` · `preventDefault*`

## 12. connecting 关键子项
`snap` · `allowBlank` · `allowLoop` · `allowNode` · `allowEdge` · `allowPort` · `allowMulti` · `highlight` · `anchor` · `sourceAnchor` · `targetAnchor` · `connectionPoint` · `router` · `connector` · `createEdge` · `validateMagnet` · `validateEdge` · `validateConnection`

## 13. 事件名（高频）
`node:click` `node:dblclick` `node:mouseenter` `node:mouseleave` `edge:connected` `edge:click` `cell:added` `cell:removed` `cell:changed` `blank:click` `blank:mousedown` `blank:mousewheel` `port:click` `graph:*`

## 14. 官方插件生态（`@antv/x6-plugin-*`，核心库不含）
`selection`(框选) · `transform`(缩放旋转) · `snapline`(对齐线) · `keyboard`(快捷键) · `clipboard`(复制粘贴) · `history`(撤销重做) · `exporting`(导出图) · `minimap`(小地图) · `dnd`(拖拽建节点) · `stencil`(图元面板) · `markdown`(MD节点) · `widget`(输入框/菜单/气泡)

## 15. 安装
```bash
npm i @antv/x6
# 插件按需：
npm i @antv/x6-plugin-selection @antv/x6-plugin-transform @antv/x6-plugin-snapline @antv/x6-plugin-keyboard @antv/x6-plugin-history
```
加载：`import { Selection } from '@antv/x6-plugin-selection'; graph.use(new Selection())`
