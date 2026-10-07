import { describe, expect, it } from "vitest";
import { buildAdjacency, reach } from "./nav";

describe("navigation projection", () => {
  it("traverses semantic relationships while keeping hierarchy out of semantic reach", () => {
    const cells = [
      { id: "issue", data: { parent_id: null } },
      { id: "a", data: { parent_id: "issue" } },
      { id: "b", data: { parent_id: "issue" } },
      { id: "hierarchy", shape: "edge", source: "issue", target: "a", data: { relation: "subordinate" } },
      { id: "dispute", shape: "edge", source: "a", target: "b", data: { relation: "oppose" } },
    ];
    const adjacency = buildAdjacency(cells);

    expect(reach(adjacency, "a")).toEqual(new Set(["b"]));
    expect(adjacency.edges.map((edge) => edge.id)).toEqual(["hierarchy", "dispute"]);
  });
});
