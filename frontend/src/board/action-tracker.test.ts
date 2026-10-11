import { describe, expect, it, vi } from "vitest";
import { mountActionTracker } from "./action-tracker";
import { meetingsApi } from "../api/meetings";

vi.mock("../api/meetings", () => ({ meetingsApi: {
  actions: vi.fn(), registerAction: vi.fn(), updateAction: vi.fn(), actionHistory: vi.fn(),
} }));

describe("mountActionTracker", () => {
  it("registers a board action and lets a person assign an owner", async () => {
    document.body.innerHTML = '<div id="action-tracker"></div>';
    vi.mocked(meetingsApi.actions).mockResolvedValue({ ok: true, actions: [] });
    vi.mocked(meetingsApi.registerAction).mockResolvedValue({ ok: true, action: {
      id: "act_1", meeting_id: "mtg_one", action_node_id: "n_action", decision_node_id: null,
      event_id: "track:n_action", owner: null, due_date: null, status: "open", version: 1,
      created_at: "", updated_at: "",
    } });
    vi.mocked(meetingsApi.updateAction).mockResolvedValue({ ok: true, action: {
      id: "act_1", meeting_id: "mtg_one", action_node_id: "n_action", decision_node_id: null,
      event_id: "track:n_action", owner: "小李", due_date: null, status: "in_progress", version: 2,
      created_at: "", updated_at: "",
    } });
    const tracker = mountActionTracker(document.getElementById("action-tracker")!, "mtg_one");
    await tracker.update([{ id: "n_action", data: { type: "action", label: "整理名单" } }]);
    const host = document.getElementById("action-tracker")!;
    host.querySelector<HTMLButtonElement>(".action-register")!.click();
    await vi.waitFor(() => expect(host.querySelector<HTMLInputElement>(".action-owner")).not.toBeNull());
    host.querySelector<HTMLInputElement>(".action-owner")!.value = "小李";
    host.querySelector<HTMLSelectElement>(".action-status")!.value = "in_progress";
    host.querySelector<HTMLButtonElement>(".action-save")!.click();
    await vi.waitFor(() => expect(host.textContent).toContain("已保存"));
    expect(host.querySelector<HTMLInputElement>(".action-owner")!.value).toBe("小李");
  });

  it("links an action only to a human-confirmed decision", async () => {
    document.body.innerHTML = '<div id="action-tracker"></div>';
    vi.mocked(meetingsApi.actions).mockResolvedValue({ ok: true, actions: [] });
    vi.mocked(meetingsApi.registerAction).mockResolvedValue({ ok: true, action: {
      id: "act_2", meeting_id: "mtg_one", action_node_id: "n_action", decision_node_id: "human",
      event_id: "track:n_action", owner: null, due_date: null, status: "open", version: 1,
      created_at: "", updated_at: "",
    } });
    const tracker = mountActionTracker(document.getElementById("action-tracker")!, "mtg_one");
    await tracker.update([
      { id: "n_action", data: { type: "action", label: "整理名单" } },
      { id: "ai", data: { type: "conclusion", label: "AI 候选", status: "confirmed" } },
      { id: "human", data: { type: "conclusion", label: "人工决议", status: "confirmed", manual_override: { status: true } } },
    ]);
    const host = document.getElementById("action-tracker")!;
    const select = host.querySelector<HTMLSelectElement>(".action-decision")!;
    expect(Array.from(select.options).map((option) => option.value)).toEqual(["", "human"]);
    select.value = "human";
    host.querySelector<HTMLButtonElement>(".action-register")!.click();
    await vi.waitFor(() => expect(meetingsApi.registerAction).toHaveBeenCalledWith(
      "mtg_one", "n_action", "track:n_action", "human"));
  });

  it("shows missing owner and overdue warnings for an open action", async () => {
    document.body.innerHTML = '<div id="action-tracker"></div>';
    vi.mocked(meetingsApi.actions).mockResolvedValue({ ok: true, actions: [{
      id: "act_3", meeting_id: "mtg_one", action_node_id: "n_action", decision_node_id: null,
      event_id: "track:n_action", owner: null, due_date: "2020-01-01", status: "open", version: 1,
      created_at: "", updated_at: "",
    }] });
    const tracker = mountActionTracker(document.getElementById("action-tracker")!, "mtg_one");
    await tracker.update([{ id: "n_action", data: { type: "action", label: "整理名单" } }]);
    expect(document.getElementById("action-tracker")?.textContent).toContain("缺少负责人");
    expect(document.getElementById("action-tracker")?.textContent).toContain("已逾期");
  });

  it("shows a saved action audit trail", async () => {
    document.body.innerHTML = '<div id="action-tracker"></div>';
    const action = {
      id: "act_4", meeting_id: "mtg_one", action_node_id: "n_action", decision_node_id: null,
      event_id: "track:n_action", owner: "小李", due_date: null, status: "in_progress" as const, version: 2,
      created_at: "2026-10-10T10:00:00Z", updated_at: "2026-10-10T11:00:00Z",
    };
    vi.mocked(meetingsApi.actions).mockResolvedValue({ ok: true, actions: [action] });
    vi.mocked(meetingsApi.actionHistory).mockResolvedValue({ ok: true, events: [{
      id: 1, meeting_id: "mtg_one", action_id: "act_4", event_type: "updated",
      before: { ...action, owner: null, status: "open", version: 1 }, after: action,
      created_at: "2026-10-10T11:00:00Z",
    }] });
    const tracker = mountActionTracker(document.getElementById("action-tracker")!, "mtg_one");
    await tracker.update([{ id: "n_action", data: { type: "action", label: "整理名单" } }]);
    document.querySelector<HTMLButtonElement>(".action-history-toggle")!.click();
    await vi.waitFor(() => expect(document.querySelector(".action-history")?.textContent).toContain("小李"));
    expect(meetingsApi.actionHistory).toHaveBeenCalledWith("mtg_one", "act_4");
  });
});
