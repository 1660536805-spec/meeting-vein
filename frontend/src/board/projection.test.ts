import { describe, expect, it } from "vitest";
import { projectBoard, projectLocalNeighborhood } from "./projection";

describe("projectBoard", () => {
  it("uses v2 parent_id for structure and keeps semantic links out of the tree", () => {
    const projection = projectBoard([
      { id: "issue", data: { type: "issue", parent_id: null } },
      { id: "a", data: { type: "point", parent_id: "issue" } },
      { id: "b", data: { type: "point", parent_id: "issue" } },
      { id: "semantic", shape: "edge", source: "a", target: "b", data: { relation: "oppose" } },
    ]);

    expect(projection.roots).toEqual(["issue"]);
    expect(projection.children.get("issue")).toEqual(["a", "b"]);
    expect(projection.semanticEdges).toHaveLength(1);
    expect(projection.unresolved).toHaveLength(1);
  });

  it("reads v1 hierarchy only from subordinate edges and indexes evidence", () => {
    const projection = projectBoard([
      { id: "issue", data: { type: "issue" } },
      { id: "a", data: { type: "point", metadata_refs: ["u1"] } },
      { id: "b", data: { type: "point" } },
      { id: "sub", shape: "edge", source: "issue", target: "a", data: { relation: "subordinate" } },
      { id: "support", shape: "edge", source: "a", target: "b", data: { relation: "support" } },
    ]);

    expect(projection.parentById.get("a")).toBe("issue");
    expect(projection.parentById.has("b")).toBe(false);
    expect(projection.evidenceById.get("u1")).toEqual(["a"]);
    expect(projection.semanticEdges.map((edge) => edge.id)).toEqual(["support"]);
  });

  it("deduplicates conflicts and chooses focus by state, importance, and recency", () => {
    const projection = projectBoard([
      { id: "issue", data: { type: "issue", status: "in_progress" } },
      { id: "old", data: { type: "point", importance: { level: "high" }, updated_at: "2026-10-01" } },
      { id: "new", data: { type: "point", importance: { level: "high" }, updated_at: "2026-10-05" } },
      { id: "c1", shape: "edge", source: "old", target: "new", data: { relation: "oppose" } },
      { id: "c2", shape: "edge", source: "new", target: "old", data: { relation: "oppose" } },
      { id: "conflict", data: { type: "conflict", resolved: false, participants: ["old", "new"] } },
      { id: "decision", data: { type: "conclusion", status: "proposed" } },
    ]);

    expect(projection.focus?.id).toBe("issue");
    expect(projection.unresolved).toHaveLength(2); // 一组争议 + 一个待确认结论
    expect(projection.unresolved.map((item) => item.kind).sort()).toEqual(["confirmation", "dispute"]);
  });

  it("falls back to the latest high-importance open item and marks unknown evidence time", () => {
    const projection = projectBoard([
      { id: "a", data: { type: "point", importance: { level: "high" }, updated_at: "2026-10-01" } },
      { id: "b", data: { type: "point", importance: { level: "high" }, updated_at: "2026-10-05" } },
      { id: "action", data: { type: "action", status: "pending" } },
      { id: "u", shape: "edge", source: "a", target: "b", data: { relation: "support" } },
    ]);
    expect(projection.focus?.id).toBe("b");
    expect(projection.actions.map((node) => node.id)).toEqual(["action"]);
    expect(projection.formatEvidenceTime({})).toBe("时间未知");
  });

  it("limits the default relationship view to two hops around its focus", () => {
    const cells = [
      { id: "root", data: { type: "issue", parent_id: null } },
      { id: "focus", data: { type: "point", parent_id: "root" } },
      { id: "child", data: { type: "evidence", parent_id: "focus" } },
      { id: "near", data: { type: "point", parent_id: null } },
      { id: "far", data: { type: "action", parent_id: null } },
      { id: "beyond", data: { type: "point", parent_id: null } },
      { id: "semantic", shape: "edge", source: "focus", target: "near", data: { relation: "support" } },
      { id: "far-link", shape: "edge", source: "near", target: "far", data: { relation: "oppose" } },
      { id: "beyond-link", shape: "edge", source: "far", target: "beyond", data: { relation: "support" } },
    ];

    const local = projectLocalNeighborhood(cells, "focus", 2);

    expect(local.filter((cell) => cell.shape !== "edge").map((cell) => cell.id).sort())
      .toEqual(["child", "far", "focus", "near", "root"]);
    expect(local.filter((cell) => cell.shape === "edge").map((cell) => cell.id))
      .toEqual(["semantic", "far-link"]);
    expect(projectLocalNeighborhood(cells, "missing")).toEqual(cells);
  });
});
