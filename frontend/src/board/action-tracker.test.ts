import { describe, expect, it, vi } from "vitest";
import { mountActionTracker } from "./action-tracker";
import { meetingsApi } from "../api/meetings";

vi.mock("../api/meetings", () => ({ meetingsApi: {
  actions: vi.fn(), registerAction: vi.fn(), updateAction: vi.fn(), actionHistory: vi.fn(), actionCalendar: vi.fn(),
  syncActions: vi.fn(),
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

  it("offers a subscribable calendar feed and downloads a refreshed calendar file", async () => {
    document.body.innerHTML = '<div id="action-tracker"></div>';
    vi.mocked(meetingsApi.actions).mockResolvedValue({ ok: true, actions: [{
      id: "act_5", meeting_id: "mtg_one", action_node_id: "n_action", decision_node_id: null,
      event_id: "track:n_action", owner: "小李", due_date: "2026-10-20", status: "open", version: 1,
      created_at: "", updated_at: "",
    }] });
    vi.mocked(meetingsApi.actionCalendar).mockResolvedValue("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n");
    const tracker = mountActionTracker(document.getElementById("action-tracker")!, "mtg_one");
    await tracker.update([{ id: "n_action", data: { type: "action", label: "整理名单" } }]);

    const host = document.getElementById("action-tracker")!;
    expect(host.querySelector(".action-calendar-download")).not.toBeNull();
    const subscribe = host.querySelector<HTMLAnchorElement>(".action-calendar-subscribe")!;
    expect(subscribe.href).toContain("webcal:");
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    host.querySelector<HTMLButtonElement>(".action-calendar-download")!.click();
    await vi.waitFor(() => expect(meetingsApi.actionCalendar).toHaveBeenCalledWith("mtg_one"));
    await vi.waitFor(() => expect(host.textContent).toContain("日历文件已生成"));
    expect(click).toHaveBeenCalled();
    click.mockRestore();
  });

  it("sends changed actions to a configured task webhook and shows accepted state", async () => {
    document.body.innerHTML = '<div id="action-tracker"></div>';
    const action = {
      id: "act_sync", meeting_id: "mtg_one", action_node_id: "n_action", decision_node_id: null,
      event_id: "track:n_action", owner: "小李", due_date: "2026-10-20", status: "open" as const,
      version: 2, created_at: "", updated_at: "",
    };
    vi.mocked(meetingsApi.actions).mockResolvedValue({ ok: true, actions: [action], external_task_sync_configured: true });
    vi.mocked(meetingsApi.syncActions).mockResolvedValue({ ok: true, sync_state: "sent", remote_status: 202,
      synced_count: 1, actions: [{ ...action, external_sync_version: 2,
        external_sync_attempted_at: "2026-10-11T00:00:00Z", external_sync_error: null }] });
    const tracker = mountActionTracker(document.getElementById("action-tracker")!, "mtg_one");
    await tracker.update([{ id: "n_action", data: { type: "action", label: "整理名单" } }]);

    const host = document.getElementById("action-tracker")!;
    const sync = host.querySelector<HTMLButtonElement>(".action-sync")!;
    expect(sync.disabled).toBe(false);
    sync.click();
    await vi.waitFor(() => expect(meetingsApi.syncActions).toHaveBeenCalledWith("mtg_one"));
    await vi.waitFor(() => expect(host.textContent).toContain("已发送 1 项，任务工具已受理"));
    expect(host.textContent).toContain("任务工具已接收此版本");
  });

  it("shows pending sync when board-owned task text changed at the same ledger version", async () => {
    document.body.innerHTML = '<div id="action-tracker"></div>';
    vi.mocked(meetingsApi.actions).mockResolvedValue({ ok: true, external_task_sync_configured: true,
      actions: [{
        id: "act_sync", meeting_id: "mtg_one", action_node_id: "n_action", decision_node_id: null,
        event_id: "track:n_action", owner: "小李", due_date: "2026-10-20", status: "open", version: 2,
        created_at: "", updated_at: "", external_sync_version: 2, external_sync_pending: true,
      }] });
    const tracker = mountActionTracker(document.getElementById("action-tracker")!, "mtg_one");

    await tracker.update([{ id: "n_action", data: { type: "action", label: "修订后的任务标题" } }]);

    const host = document.getElementById("action-tracker")!;
    expect(host.textContent).toContain("行动项已有更新，待同步到任务工具");
    expect(host.textContent).not.toContain("任务工具已接收此版本");
  });
});
