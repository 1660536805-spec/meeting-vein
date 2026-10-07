import { describe, expect, it } from "vitest";
import { renderIssueStructure } from "./issues-view";

describe("renderIssueStructure", () => {
  it("shows a concise meeting summary and explicit confirmed conclusions", () => {
    const container = document.createElement("div");
    renderIssueStructure(container, [
      { id: "root", data: { type: "issue", label: "产品路线评审", parent_id: null } },
      { id: "decision", data: { type: "conclusion", label: "先完成小范围试点", status: "confirmed", parent_id: "root" } },
    ]);

    expect(container.querySelector(".meeting-summary")?.textContent).toContain("产品路线评审");
    expect(container.querySelector(".confirmed-conclusions")?.textContent).toContain("先完成小范围试点");
  });

  it("searches branches, opens a matching branch, and can focus a conclusion in the graph", () => {
    const container = document.createElement("div");
    const cells = [
      { id: "root", data: { type: "issue", label: "路线评审", parent_id: null } },
      { id: "strategy", data: { type: "point", label: "增长战略", parent_id: "root" } },
      { id: "budget", data: { type: "point", label: "预算约束", parent_id: "root" } },
      ...Array.from({ length: 9 }, (_, i) => ({
        id: `detail-${i}`, data: { type: "point", label: `战略细节 ${i}`, parent_id: "strategy" },
      })),
      { id: "decision", data: { type: "conclusion", label: "先完成试点", status: "confirmed", parent_id: "strategy" } },
    ];
    const focused: string[] = [];
    renderIssueStructure(container, cells, { onViewInGraph: (id) => focused.push(id) });

    const strategy = container.querySelector<HTMLElement>('.branch[data-node-id="strategy"]')!;
    const budget = container.querySelector<HTMLElement>('.branch[data-node-id="budget"]')!;
    const blocks = strategy.querySelector<HTMLElement>(".blocks")!;
    expect(blocks.hidden).toBe(true);
    const search = container.querySelector<HTMLInputElement>('[aria-label="搜索议题结构"]')!;
    search.value = "增长战略";
    search.dispatchEvent(new Event("input"));
    expect(strategy.hidden).toBe(false);
    expect(budget.hidden).toBe(true);
    expect(blocks.hidden).toBe(false);
    container.querySelector<HTMLButtonElement>('[data-view-node="decision"]')!.click();
    expect(focused).toEqual(["decision"]);
  });

  it("renders each branch title once while keeping it as the breadcrumb control", () => {
    const container = document.createElement("div");
    renderIssueStructure(container, [
      { id: "root", data: { type: "issue", label: "路线评审", parent_id: null } },
      { id: "branch", data: { type: "point", label: "先做小范围试点", parent_id: "root" } },
    ]);

    const heading = container.querySelector(".branch h2")!;
    expect(heading.textContent).toBe("先做小范围试点");
    expect(heading.querySelector(".branch-path-button")?.textContent).toBe("先做小范围试点");
  });
});
