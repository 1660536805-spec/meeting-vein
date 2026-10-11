import { describe, expect, it, vi } from "vitest";
import { mountPreparationPanel } from "./preparation-panel";
import { meetingsApi } from "../api/meetings";

vi.mock("../api/meetings", () => ({ meetingsApi: {
  preparation: vi.fn(), uploadMaterial: vi.fn(), addQuestion: vi.fn(), updateQuestion: vi.fn(), materialSource: vi.fn(),
} }));

describe("mountPreparationPanel", () => {
  it("confirms an agenda suggestion and resolves the persisted question", async () => {
    document.body.innerHTML = '<div id="preparation-panel"></div>';
    const suggestion = { text: "待决：试点范围", source_kind: "agenda" as const,
      source_ref: "agenda:1", source_label: "议程第 1 项" };
    const question = { id: "q1", meeting_id: "mtg_one", event_id: "evt_1",
      text: suggestion.text, source_kind: "agenda" as const, source_ref: "agenda:1",
      status: "pending" as const, version: 1, created_at: "", updated_at: "" };
    vi.mocked(meetingsApi.preparation).mockResolvedValue({ ok: true, materials: [],
      suggestions: [suggestion], questions: [] });
    vi.mocked(meetingsApi.addQuestion).mockResolvedValue({ ok: true, question });
    vi.mocked(meetingsApi.updateQuestion).mockResolvedValue({ ok: true,
      question: { ...question, status: "resolved", version: 2 } });
    const panel = mountPreparationPanel(document.getElementById("preparation-panel")!, "mtg_one");
    await panel.update();
    const host = document.getElementById("preparation-panel")!;
    expect(host.textContent).toContain("议程第 1 项");
    host.querySelector<HTMLButtonElement>(".prep-confirm")!.click();
    await vi.waitFor(() => expect(host.querySelector(".prep-resolve")).not.toBeNull());
    expect(meetingsApi.addQuestion).toHaveBeenCalledWith(
      "mtg_one", expect.any(String), suggestion.text, "agenda", "agenda:1");
    host.querySelector<HTMLButtonElement>(".prep-resolve")!.click();
    await vi.waitFor(() => expect(host.textContent).toContain("已解决"));
    expect(meetingsApi.updateQuestion).toHaveBeenCalledWith("mtg_one", "q1", 1, "resolved");
  });

  it("opens the exact material excerpt for a confirmed question", async () => {
    document.body.innerHTML = '<div id="preparation-panel"></div>';
    vi.mocked(meetingsApi.preparation).mockResolvedValue({ ok: true,
      materials: [{ id: "mat_1", meeting_id: "mtg_one", filename: "plan.md", content_sha256: "x", created_at: "" }],
      suggestions: [], questions: [{ id: "q2", meeting_id: "mtg_one", event_id: "evt_2",
        text: "是否试点？", source_kind: "material", source_ref: "mat_1:line:2",
        status: "pending", version: 1, created_at: "", updated_at: "" }] });
    vi.mocked(meetingsApi.materialSource).mockResolvedValue({ ok: true,
      source_ref: "mat_1:line:2", text: "原文：是否先试点？" });
    const host = document.getElementById("preparation-panel")!;
    await mountPreparationPanel(host, "mtg_one").update();
    host.querySelector<HTMLButtonElement>(".prep-question button")!.click();
    await vi.waitFor(() => expect(host.textContent).toContain("原文：是否先试点？"));
    expect(meetingsApi.materialSource).toHaveBeenCalledWith("mtg_one", "mat_1:line:2");
  });
});
