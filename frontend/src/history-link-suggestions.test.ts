import { describe, expect, it, vi } from "vitest";
import { bindLinkSuggestionSearch, renderLinkSuggestions } from "./history-link-suggestions";

describe("renderLinkSuggestions", () => {
  it("shows why a cross-meeting candidate matched and lets the user dismiss it", () => {
    const host = document.createElement("div");
    const choose = vi.fn();
    const dismiss = vi.fn();
    renderLinkSuggestions(host, [{
      source_meeting_id: "mtg_old", source_meeting_title: "旧会",
      source_topic_node_id: "old_topic", source_topic_label: "客户支持策略",
      target_topic_node_id: "new_topic", target_topic_label: "数据产品升级",
      score: 0.72, matching_terms: [], matching_context_terms: ["华东", "客户", "工单"],
      source_evidence: [{ meta_id: "old_quote", node_label: "一线响应安排", speaker: "甲",
        text: "每周五复核华东区客户工单。" }],
      target_evidence: [{ meta_id: "new_quote", node_label: "页面体验修复", speaker: "乙",
        text: "复盘华东区客户工单的首次响应时间。" }],
      requires_confirmation: true,
    }], choose, dismiss);

    expect(host.textContent).toContain("议题内容/原话词：华东、客户、工单");
    expect(host.textContent).toContain("上次原话 · 一线响应安排 · 甲：每周五复核华东区客户工单。");
    expect(host.textContent).toContain("本次原话 · 页面体验修复 · 乙：复盘华东区客户工单的首次响应时间。");
    host.querySelector<HTMLButtonElement>(".history-link-choose")!.click();
    host.querySelector<HTMLButtonElement>(".history-link-dismiss")!.click();
    expect(choose).toHaveBeenCalledTimes(1);
    expect(dismiss).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("不再提示");
  });

  it("keeps manual association available when no candidates remain", () => {
    const host = document.createElement("div");
    renderLinkSuggestions(host, [], vi.fn(), vi.fn());
    expect(host.textContent).toContain("可以在下方手动关联");
  });

  it("lets a user select an old topic found by evidence search without inventing a target match", () => {
    const host = document.createElement("div");
    const choose = vi.fn();
    renderLinkSuggestions(host, [{
      source_meeting_id: "mtg_old", source_meeting_title: "老客服会",
      source_topic_node_id: "old_topic", source_topic_label: "一线客服支持",
      target_topic_node_id: "", target_topic_label: "",
      score: 0, matching_terms: [], matching_context_terms: [],
      matched_search_terms: ["华东区"], search_only: true,
      source_evidence: [{ meta_id: "old_quote", node_label: "工单响应", speaker: "甲",
        text: "每周五复核华东区客户工单的首次响应时间。" }],
      target_evidence: [], requires_confirmation: true,
    }], choose, vi.fn(), "华东区");

    expect(host.textContent).toContain("搜索命中旧会议资料：华东区");
    expect(host.textContent).toContain("请在下方选择本会议议题");
    expect(host.textContent).toContain("上次原话 · 工单响应 · 甲：每周五复核华东区客户工单的首次响应时间。");
    expect(host.querySelector<HTMLButtonElement>(".history-link-choose")!.textContent).toBe("填入旧议题");
    expect(host.querySelector(".history-link-dismiss")).toBeNull();
    host.querySelector<HTMLButtonElement>(".history-link-choose")!.click();
    expect(choose).toHaveBeenCalledTimes(1);
  });

  it("sends search text to the all-history suggestion query", () => {
    const input = document.createElement("input");
    const search = vi.fn();
    bindLinkSuggestionSearch(input, search);
    input.value = "客户支持";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    expect(search).toHaveBeenCalledWith("客户支持");
  });
});
