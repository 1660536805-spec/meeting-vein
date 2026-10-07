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
      { id: "decision", data: { type: "conclusion", label: "先做封闭试点", status: "confirmed", parent_id: "root" } },
      { id: "todo", data: { type: "action", label: "准备试点清单", status: "pending", parent_id: "root" } },
    ]);

    expect(document.getElementById("meeting-summary")?.textContent).toContain("产品路线评审");
    expect(document.getElementById("meeting-summary")?.textContent).toContain("下一步 1");
    expect(document.getElementById("confirmed-count")?.textContent).toBe("1");
    expect(document.getElementById("confirmed-list")?.textContent).toContain("先做封闭试点");
  });
});
