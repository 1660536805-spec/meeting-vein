import { describe, expect, it } from "vitest";
import { projectDecisionCards } from "./decision-cards";

describe("projectDecisionCards", () => {
  it("keeps an AI conclusion pending and separates supporting and opposing evidence", () => {
    const cards = projectDecisionCards([
      { id: "decision", data: { type: "conclusion", label: "先试点", status: "proposed", metadata_refs: ["utt_1"] } },
      { id: "for", data: { type: "point", label: "成本较低", metadata_refs: ["utt_2"] } },
      { id: "against", data: { type: "point", label: "仍有隐私风险", metadata_refs: ["utt_3"] } },
      { id: "support-edge", shape: "edge", source: "for", target: "decision", data: { relation: "support" } },
      { id: "oppose-edge", shape: "edge", source: "against", target: "decision", data: { relation: "oppose" } },
    ]);
    expect(cards).toEqual([{
      id: "decision", label: "先试点", status: "pending", sourceRefs: ["utt_1"],
      supports: [{ id: "for", label: "成本较低", sourceRefs: ["utt_2"] }],
      objections: [{ id: "against", label: "仍有隐私风险", sourceRefs: ["utt_3"] }],
      missingEvidence: false,
    }]);
  });

  it("marks unsupported conclusions as missing evidence instead of confirmed", () => {
    const cards = projectDecisionCards([
      { id: "decision", data: { type: "conclusion", label: "发布", status: "confirmed" } },
    ]);
    expect(cards[0].status).toBe("pending");
    expect(cards[0].missingEvidence).toBe(true);
  });

  it("requires an audited human status change before showing a confirmed decision", () => {
    const cards = projectDecisionCards([{
      id: "decision", data: { type: "conclusion", label: "发布", status: "confirmed",
        manual_override: { status: true } },
    }]);
    expect(cards[0].status).toBe("confirmed");
  });
});
