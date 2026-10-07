import { describe, expect, it } from "vitest";
import { cycleNodeId } from "./keyboard-nav";

describe("cycleNodeId", () => {
  it("moves keyboard focus through nodes and wraps in either direction", () => {
    expect(cycleNodeId(["a", "b", "c"], "a", 1)).toBe("b");
    expect(cycleNodeId(["a", "b", "c"], "c", 1)).toBe("a");
    expect(cycleNodeId(["a", "b", "c"], "a", -1)).toBe("c");
    expect(cycleNodeId([], "", 1)).toBeNull();
  });
});
