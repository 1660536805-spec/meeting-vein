import { describe, expect, it } from "vitest";
import { parseAgenda } from "./agenda";

describe("parseAgenda", () => {
  it("keeps ordered top-level topics from pasted markdown", () => {
    expect(parseAgenda("# 议程\n1. 现状\n- 风险评估\n\n2、下一步"))
      .toEqual(["现状", "风险评估", "下一步"]);
  });
});
