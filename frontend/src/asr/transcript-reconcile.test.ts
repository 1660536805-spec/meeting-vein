import { describe, expect, it } from "vitest";
import { uncoveredTranscriptSegments } from "./transcript-reconcile";

describe("uncovered transcript reconciliation", () => {
  it("returns only ordered transcript spans absent from streamed finals", () => {
    expect(uncoveredTranscriptSegments(
      "先安排预算；现有收入稳定。周五确认负责人。",
      ["现有收入稳定"],
    )).toEqual(["先安排预算", "周五确认负责人"]);
  });

  it("normalizes punctuation and width differences before matching", () => {
    expect(uncoveredTranscriptSegments("预算增加，会议继续。", ["预算增加，会议继续"])).toEqual([]);
  });

  it("refuses to auto-reconcile when a streamed segment is missing or out of order", () => {
    expect(uncoveredTranscriptSegments("预算增加，会议继续。", ["未知片段"])).toBeNull();
    expect(uncoveredTranscriptSegments("先确认预算再安排负责人", ["安排负责人", "确认预算"])).toBeNull();
  });

  it("requires repeated delivered text to occur the same number of times", () => {
    expect(uncoveredTranscriptSegments("好的，好。", ["好的", "好的"])).toBeNull();
  });
});
