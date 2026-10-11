import { describe, expect, it, vi } from "vitest";
import { mountDecisionPanel } from "./decision-panel";

vi.mock("../api/rest", () => ({ rest: { metadata: vi.fn() } }));

describe("mountDecisionPanel", () => {
  it("summarizes the meeting and lists confirmed conclusions on the first screen", async () => {
    document.body.innerHTML = `
      <div id="meeting-summary"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    const panel = mountDecisionPanel(() => undefined);
    await panel.update([
      { id: "root", data: { type: "issue", label: "产品路线评审", parent_id: null } },
      { id: "decision", data: { type: "conclusion", label: "先做封闭试点", status: "confirmed", manual_override: { status: true }, parent_id: "root" } },
      { id: "todo", data: { type: "action", label: "准备试点清单", status: "pending", parent_id: "root" } },
    ]);

    expect(document.getElementById("meeting-summary")?.textContent).toContain("产品路线评审");
    expect(document.getElementById("meeting-summary")?.textContent).toContain("下一步 1");
    expect(document.getElementById("confirmed-count")?.textContent).toBe("1");
    expect(document.getElementById("confirmed-list")?.textContent).toContain("先做封闭试点");
  });

  it("shows a pending decision with both sides and opens human review", async () => {
    document.body.innerHTML = `
      <div id="meeting-summary"></div><div id="decision-card-list"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    const review = vi.fn();
    const panel = mountDecisionPanel(() => undefined, review);
    await panel.update([
      { id: "decision", data: { type: "conclusion", label: "先试点", status: "proposed" } },
      { id: "for", data: { type: "point", label: "投入较低" } },
      { id: "against", data: { type: "point", label: "隐私风险" } },
      { id: "support", shape: "edge", source: "for", target: "decision", data: { relation: "support" } },
      { id: "oppose", shape: "edge", source: "against", target: "decision", data: { relation: "oppose" } },
    ]);
    const list = document.getElementById("decision-card-list")!;
    expect(list.textContent).toContain("待人工确认");
    expect(list.textContent).toContain("投入较低");
    expect(list.textContent).toContain("隐私风险");
    list.querySelector<HTMLButtonElement>(".decision-review")!.click();
    expect(review).toHaveBeenCalledWith("decision");
  });
});
