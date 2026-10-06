import { afterEach, describe, expect, it, vi } from "vitest";
import type { Graph } from "@antv/x6";
import { applyBoardUpdate, renderBoard } from "./render";

afterEach(() => vi.restoreAllMocks());

describe("reference workspace design", () => {
  afterEach(() => document.body.classList.remove("decision-workspace"));

  it("places decisions after viewpoints while preserving human positioned nodes and source references", () => {
    document.body.classList.add("decision-workspace");
    const fromJSON = vi.fn();
    const graph = {
      container: { clientWidth: 900 },
      fromJSON,
      zoomToFit: vi.fn(),
      getNodes: () => [],
      getCellById: () => undefined,
      centerCell: vi.fn(),
    } as unknown as Graph;
    renderBoard(graph, [
      { id: "point", shape: "amo-node", data: { type: "point", label: "优先核心渠道", metadata_refs: ["utterance-1"] } },
      { id: "decision", shape: "amo-node", data: { type: "conclusion", label: "先跑试点" } },
      { id: "action", shape: "amo-node", position: { x: 420, y: 360 }, data: { type: "action", label: "完成草案", edit: { position_frozen: true } } },
      { id: "edge", shape: "edge", source: "point", target: "decision", data: { relation: "support" } },
    ]);
    const { nodes, edges } = fromJSON.mock.calls[0][0];
    expect(nodes[1].x).toBeGreaterThan(nodes[0].x);
    expect(nodes[2]).toMatchObject({ x: 420, y: 360 });
    expect(nodes[0].data.metadata_refs).toEqual(["utterance-1"]);
    // 决策页深色主题：point 类型色 #61DDAA 向白色提亮 82%
    expect(nodes[0].attrs.body.stroke).toBe("#7de3b9");
    expect(nodes[1].attrs.body.stroke).toBe("#8b7efd");   // conclusion #7262FD 提亮 82%
    expect(edges[0].labels[0].attrs.label.text).toBe("支持");
    expect(edges[0].connector.name).toBe("smooth");
  });
});

describe("narrow board layout", () => {
  it("fits nodes when the board is narrow even if the window is not", () => {
    vi.spyOn(window, "innerWidth", "get").mockReturnValue(924);
    const graph = {
      container: { clientWidth: 746 },
      fromJSON: vi.fn(),
      zoomToFit: vi.fn(),
    } as unknown as Graph;
    renderBoard(graph, [{ id: "right", shape: "amo-node", x: 580, y: 40, width: 220, height: 64, data: { label: "右侧卡片" } }]);
    expect(graph.zoomToFit).toHaveBeenCalledWith({ padding: 16, maxScale: 1 });
  });

  it("fits nodes into a narrow board", () => {
    const graph = { container: { clientWidth: 390 }, fromJSON: vi.fn(), zoomToFit: vi.fn() } as unknown as Graph;
    renderBoard(graph, [{ id: "root", shape: "amo-node", position: { x: 300, y: 40 }, data: { label: "会议" } }]);
    expect(graph.zoomToFit).toHaveBeenCalledWith({ padding: 16, maxScale: 1 });
  });

  it("keeps the desktop board scale unchanged", () => {
    vi.spyOn(window, "innerWidth", "get").mockReturnValue(1440);
    const graph = { container: { clientWidth: 1266 }, fromJSON: vi.fn(), zoomToFit: vi.fn() } as unknown as Graph;
    applyBoardUpdate(graph, []);
    expect(graph.zoomToFit).not.toHaveBeenCalled();
  });

  it("aligns viewpoints horizontally and stacks their evidence vertically", () => {
    const fromJSON = vi.fn();
    const graph = { container: { clientWidth: 1266 }, fromJSON, zoomToFit: vi.fn() } as unknown as Graph;
    renderBoard(graph, [
      { id: "root", shape: "amo-node", data: { type: "issue", label: "议题" } },
      { id: "pointA", shape: "amo-node", data: { type: "point", label: "观点A" } },
      { id: "pointB", shape: "amo-node", data: { type: "point", label: "观点B" } },
      { id: "evA1", shape: "amo-node", data: { type: "evidence", label: "论据A1" } },
      { id: "evA2", shape: "amo-node", data: { type: "evidence", label: "论据A2" } },
      { id: "evB1", shape: "amo-node", data: { type: "evidence", label: "论据B1" } },
      { id: "e1", shape: "edge", source: "root", target: "pointA", data: { relation: "subordinate" } },
      { id: "e2", shape: "edge", source: "root", target: "pointB", data: { relation: "subordinate" } },
      { id: "e3", shape: "edge", source: "pointA", target: "evA1", data: { relation: "support" } },
      { id: "e4", shape: "edge", source: "pointA", target: "evA2", data: { relation: "support" } },
      { id: "e5", shape: "edge", source: "pointB", target: "evB1", data: { relation: "support" } },
    ]);
    const nodes = fromJSON.mock.calls[0][0].nodes as Array<{ id: string; x: number; y: number }>;
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    // 观点横向并排同 y，根居两列中点上方
    expect(byId.pointA.y).toBe(byId.pointB.y);
    expect(byId.pointA.y).toBeGreaterThan(byId.root.y);
    expect(byId.pointB.x).toBeGreaterThan(byId.pointA.x);
    expect(byId.root.x).toBeGreaterThan(byId.pointA.x);
    expect(byId.root.x).toBeLessThan(byId.pointB.x);
    // 各观点列内论据纵向堆叠（同 x、逐行向下）
    expect(byId.evA1.x).toBe(byId.pointA.x);
    expect(byId.evA2.x).toBe(byId.pointA.x);
    expect(byId.evA1.y).toBeGreaterThan(byId.pointA.y);
    expect(byId.evA2.y).toBeGreaterThan(byId.evA1.y);
    expect(byId.evB1.x).toBe(byId.pointB.x);
    expect(byId.evB1.y).toBeGreaterThan(byId.pointB.y);
  });

  it("auto-fits only once so later board updates keep the user's zoom", () => {
    const graph = { container: { clientWidth: 746 }, fromJSON: vi.fn(), zoomToFit: vi.fn() } as unknown as Graph;
    const cells = [{ id: "n1", shape: "amo-node", x: 40, y: 40, width: 220, height: 64, data: { label: "要点" } }];

    renderBoard(graph, cells);
    expect(graph.zoomToFit).toHaveBeenCalledTimes(1);     // 首屏自适应一次

    applyBoardUpdate(graph, cells);                       // 后端光标事件回推，不得再重置视角
    applyBoardUpdate(graph, cells);
    expect(graph.zoomToFit).toHaveBeenCalledTimes(1);
  });

  it("wraps long meeting utterances inside their board card", () => {
    const fromJSON = vi.fn();
    const graph = { container: { clientWidth: 1266 }, fromJSON, zoomToFit: vi.fn() } as unknown as Graph;
    renderBoard(graph, [{
      id: "utterance",
      shape: "amo-node",
      width: 220,
      height: 64,
      data: { label: "这是一条足够长的会议转写内容，应该在卡片内部自动换行展示，避免遮挡其它节点。" },
    }]);

    const rendered = fromJSON.mock.calls[0][0] as { nodes: Array<{ attrs: { label: { textWrap?: unknown } } }> };
    expect(rendered.nodes[0].attrs.label.textWrap).toEqual({ width: -20, height: -12, ellipsis: "…" });
  });
});

describe("board editing affordances", () => {
  it("gives every node four connectable ports so users can draw new edges", () => {
    const fromJSON = vi.fn();
    const graph = { container: { clientWidth: 1266 }, fromJSON, zoomToFit: vi.fn() } as unknown as Graph;
    renderBoard(graph, [{ id: "n1", shape: "amo-node", data: { label: "要点", type: "point" } }]);

    const rendered = fromJSON.mock.calls[0][0] as {
      nodes: Array<{ ports: { items: Array<{ id: string; group: string }>; groups: Record<string, unknown> } }>;
    };
    const ports = rendered.nodes[0].ports;
    expect(ports.items.map((p) => p.id)).toEqual(["left", "top", "right", "bottom"]);
    expect(Object.keys(ports.groups).sort()).toEqual(["bottom", "left", "right", "top"]);
  });

  it("attaches no tools to edges (no red ⊗ clutter; delete via hover + Delete key)", () => {
    const fromJSON = vi.fn();
    const graph = { container: { clientWidth: 1266 }, fromJSON, zoomToFit: vi.fn() } as unknown as Graph;
    renderBoard(graph, [{ id: "e1", shape: "edge", source: "a", target: "b", data: { relation: "oppose" } }]);

    const rendered = fromJSON.mock.calls[0][0] as { edges: Array<{ tools?: unknown }> };
    expect(rendered.edges[0].tools).toBeUndefined();
  });
});

describe("user command marks (data.cmd)", () => {
  const graphOf = () => {
    const fromJSON = vi.fn();
    const graph = { container: { clientWidth: 1266 }, fromJSON, zoomToFit: vi.fn() } as unknown as Graph;
    return { graph, fromJSON };
  };

  it("grays out nodes marked gray（暂不考虑→灰化）", () => {
    const { graph, fromJSON } = graphOf();
    renderBoard(graph, [{
      id: "n1", shape: "amo-node",
      data: { label: "五仁月饼太老", type: "point", cmd: { mark: "gray", reason: "用户指令暂时不考虑", by: "user" } },
    }]);

    const node = (fromJSON.mock.calls[0][0] as {
      nodes: Array<{ attrs: { body: { fill: string; stroke: string }; typeLabel: { text: string; fill: string }; label: { fill: string; textDecoration: string } } }>;
    }).nodes[0];
    expect(node.attrs.body.fill).toBe("#ececec");
    expect(node.attrs.body.stroke).toBe("#b3b3b3");
    expect(node.attrs.typeLabel.text).toContain("暂不考虑");
    expect(node.attrs.label.fill).toBe("#9c9c9c");
    expect(node.attrs.label.textDecoration).toBe("none");
  });

  it("strikes through labels of nodes marked strike（删除→删除线，软删除不灰化）", () => {
    const { graph, fromJSON } = graphOf();
    renderBoard(graph, [{
      id: "n2", shape: "amo-node",
      data: { label: "榴莲月饼", type: "point", cmd: { mark: "strike", reason: "大家不接受", by: "user" } },
    }]);

    const node = (fromJSON.mock.calls[0][0] as {
      nodes: Array<{ attrs: { body: { fill: string }; label: { textDecoration: string } } }>;
    }).nodes[0];
    expect(node.attrs.label.textDecoration).toBe("line-through");
    expect(node.attrs.body.fill).not.toBe("#ececec");   // strike 只画删除线，不变灰
  });
});
