# X6 v2 代码模板（可直接复制）

> 所有视觉用 antd 蓝白 CSS 变量兜底；本项目约定见 `doc/Research_X6_Graph.md` §6。

## A. 建图（ants 蓝白）
```ts
import { Graph } from "@antv/x6";

const PRIMARY = "var(--ant-primary, #1677ff)";
const TEXT    = "var(--ant-text, rgba(0,0,0,0.88))";
const BORDER  = "var(--ant-border, #d9d9d9)";

export function createGraph(container: HTMLElement): Graph {
  return new Graph({
    container,
    autoResize: true,                          // 容器变化自动刷新（必开）
    background: { color: "#f0f5ff" },          // 主蓝浅底
    grid: { visible: true, type: "dot", size: 16, args: { color: "#d6e4ff", thickness: 1 } },
    interacting: { nodeMovable: true },         // 查看模式置 false
    connecting: { allowBlank: false, snap: true, router: "orth", connector: "rounded" },
    panning: true,
    mousewheel: { enabled: true, modifiers: ["ctrl"] },
  });
}
```

## B. 加节点（rect + 蓝白）
```ts
graph.addNode({
  id: "n1",
  shape: "rect",
  x: 80, y: 80, width: 220, height: 64,
  zIndex: 10,
  attrs: {
    body:  { fill: "#fff", stroke: PRIMARY, strokeWidth: 1.5, rx: 6, ry: 6 },
    label: { text: "结论节点", fill: TEXT, fontSize: 13, fontWeight: 500,
             textAnchor: "middle", textVerticalAnchor: "middle" },
  },
  data: { type: "conclusion" },
});
```

## C. 加边（箭头 + 蓝线）
```ts
graph.addEdge({
  id: "e1", shape: "edge",
  source: { cell: "n1" }, target: { cell: "n2" },
  zIndex: 0,
  attrs: {
    line: { stroke: PRIMARY, strokeWidth: 1.6,
             targetMarker: { name: "block", width: 9, height: 9 } },
    label: { text: "support", fill: TEXT, fontSize: 11,
             background: { fill: "#fff", stroke: BORDER, strokeWidth: 0.5, padding: 2 } },
  },
  data: { relation: "support" },
});
```

## D. v1→v2 归一化（后端老结构兼容）
```ts
function normalize(cells: any[]): { nodes: any[]; edges: any[] } {
  const nodes: any[] = [], edges: any[] = [];
  for (const c of cells || []) {
    if (c.shape === "edge") {
      edges.push({
        id: c.id, shape: "edge",
        source: c.source, target: c.target, zIndex: 0,
        attrs: {
          line: { stroke: PRIMARY, strokeWidth: 1.6,
                  targetMarker: { name: "block", width: 9, height: 9 } },
          label: { text: c.data?.relation ?? "", fill: TEXT, fontSize: 11,
                   background: { fill: "#fff", stroke: BORDER, strokeWidth: 0.5, padding: 2 } },
        },
        data: c.data,
      });
    } else {
      const x = c.x ?? c.position?.x ?? 0;
      const y = c.y ?? c.position?.y ?? 0;
      const w = c.width ?? c.size?.width ?? 220;
      const h = c.height ?? c.size?.height ?? 64;
      nodes.push({
        id: c.id, shape: "rect", x, y, width: w, height: h, zIndex: 10,
        attrs: {
          body:  { fill: "#fff", stroke: PRIMARY, strokeWidth: 1.5, rx: 6, ry: 6 },
          label: { text: c.data?.label ?? "", fill: TEXT, fontSize: 13, fontWeight: 500,
                   textAnchor: "middle", textVerticalAnchor: "middle" },
        },
        data: c.data,
      });
    }
  }
  return { nodes, edges };
}
// 使用：graph.fromJSON(normalize(backendCells))
```

## E. 交互工具（删除按钮 + 顶点编辑）
```ts
node.addTools([{ name: "button-remove", args: { x: "100%", y: 0 } }]);
edge.addTools(["vertices", "segments", { name: "button-remove" }]);
```

## F. 连线校验（论证图限制非法关系）
```ts
const graph = new Graph({
  // ...
  connecting: {
    allowBlank: false,
    validateConnection({ sourceCell, targetCell }) {
      if (!sourceCell || !targetCell) return false;
      if (sourceCell === targetCell) return false;        // 禁自连
      return true;
    },
  },
});
```

## G. antd token 注入（main.ts）
```ts
import { theme } from "antd";
const seed = theme.defaultConfig.token;          // 仅 seed token
const tokens = theme.defaultAlgorithm(seed);     // 展开完整 token（关键！）
const root = document.documentElement.style;
root.setProperty("--ant-primary", tokens.colorPrimary);
root.setProperty("--ant-text", tokens.colorText);
root.setProperty("--ant-border", tokens.colorBorder);
```

## H. 序列化与增量
```ts
const data = graph.toJSON();                 // { cells: [...] }
graph.fromJSON(data);                        // 全量
// 生产增量：后端 toJSON({diff:true}) 精细 patch，前端局部 addNode/removeCell
```

## I. 坐标转换（多人光标协同）
```ts
graph.on("blank:mousemove", ({ clientX, clientY }) => {
  const p = graph.clientToLocal(clientX, clientY);  // 屏幕→画布坐标
  // 广播 p 给其他客户端
});
```

## J. 批量更新（性能）
```ts
graph.batchUpdate("sync-board", () => {
  graph.removeCells(graph.getCells());
  graph.addNodes(nodes);
  graph.addEdges(edges);
});
```
