import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, getByRole, getByLabelText, waitFor } from "@testing-library/dom";
import { mountTaskPlan, taskLayers } from "./task-plan";
import type { PlanTask, PlanDocument } from "./task-plan-api";

vi.mock("@antv/x6", () => ({ Graph: class {
  on = vi.fn(); addNode = vi.fn(); addEdge = vi.fn(); clearCells = vi.fn();
  zoomToFit = vi.fn(); zoom = vi.fn(); dispose = vi.fn();
} }));

function task(id = "a", deps: string[] = []): PlanTask {
  return { id, title: id === "a" ? "完成方案" : "制作演示", owner: "", start_date: "2026-10-09", due_date: "2026-10-09",
    duration_days: 1, depends_on: deps, source_node_id: id, source_refs: ["u1"], source_quote: "",
    suggested_fields: ["owner", "dates"], rationale: "日期为建议", status: "todo" };
}
function documentWithDraft(): PlanDocument {
  return { meeting_id: "meeting", version: 1, confirmed: null,
    draft: { mode: "demo", reference_date: "2026-10-09", tasks: [task(), task("b")], warnings: [] },
    sources: [{ meta_id: "u1", text: "小李做方案，然后制作演示", speaker: "组长" }] };
}

describe("task plan", () => {
  let root: HTMLElement;
  let panel: ReturnType<typeof mountTaskPlan>;
  let fetchMock: ReturnType<typeof vi.fn>;
  const reply = (body: unknown, status = 200) => ({ ok: status === 200, status, json: async () => body });
  beforeEach(() => {
    document.body.innerHTML = '<div id="plan"></div>';
    root = document.getElementById("plan")!;
    fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(false);
  });
  afterEach(() => { panel?.dispose(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
  async function mount(doc = documentWithDraft()) {
    fetchMock.mockResolvedValueOnce(reply(doc)); panel = mountTaskPlan(root, "meeting"); panel.activate();
    await waitFor(() => expect(root.getAttribute("aria-busy")).toBe("false"));
  }

  it("places parallel prerequisites before a dependent and rejects invalid graphs", () => {
    const layers = taskLayers([task("a"), task("b"), task("c", ["a", "b"])]);
    expect(layers.get("a")).toBe(0); expect(layers.get("b")).toBe(0); expect(layers.get("c")).toBe(1);
    for (const tasks of [[task("a", ["a"])], [task("a", ["missing"])], [task("a", ["b"]), task("b", ["a"])], [task(), task()]]) {
      expect(() => taskLayers(tasks)).toThrow();
    }
  });

  it("labels demo generation and never confirms it automatically", async () => {
    await mount({ meeting_id: "meeting", version: 0, draft: null, confirmed: null, sources: [] });
    expect(root.textContent).toContain("还没有会后任务");
    fetchMock.mockResolvedValueOnce(reply(documentWithDraft()));
    fireEvent.click(getByRole(root, "button", { name: "生成任务草稿" }));
    await waitFor(() => expect(root.textContent).toContain("新草稿已生成"));
    expect(root.textContent).toContain("规则演示"); expect(root.textContent).toContain("待人工确认");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).expected_version).toBe(0);
  });

  it("requires applying field edits and preserves input after a failed save", async () => {
    await mount();
    const owner = getByLabelText(root, "负责人") as HTMLInputElement;
    fireEvent.input(owner, { target: { value: "小李" } });
    expect((getByRole(root, "button", { name: "确认并开始执行" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(getByRole(root, "button", { name: "制作演示 · 负责人待确认" }));
    expect(owner.value).toBe("小李"); // Cancelled discard keeps typed data.
    fireEvent.submit(owner.closest("form")!);
    fetchMock.mockResolvedValueOnce(reply({ detail: "保存失败，原计划已保留" }, 503));
    fireEvent.click(getByRole(root, "button", { name: "保存草稿" }));
    await waitFor(() => expect(root.textContent).toContain("保存失败"));
    expect((getByLabelText(root, "负责人") as HTMLInputElement).value).toBe("小李");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).tasks[0].owner).toBe("小李");
    expect((getByRole(root, "button", { name: "保存草稿" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows citations safely and allows a cited task to be selected", async () => {
    await mount();
    fetchMock.mockResolvedValueOnce(reply({ mode: "ai", answer: "先完成方案", task_ids: ["b"], version: 1, scope: "draft",
      sources: [{ meta_id: "u1", speaker: "组长", text: '<img src=x onerror="alert(1)">原话' }] }));
    fireEvent.input(getByLabelText(root, "计划问题"), { target: { value: "哪些任务先做" } });
    fireEvent.click(getByRole(root, "button", { name: "询问计划" }));
    await waitFor(() => expect(root.textContent).toContain("先完成方案"));
    expect(root.querySelector("img")).toBeNull();
    expect(root.textContent).toContain("原话依据 · 组长");
    fireEvent.click(getByRole(root, "button", { name: "查看任务：制作演示" }));
    expect((getByLabelText(root, "任务名称") as HTMLInputElement).value).toBe("制作演示");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).scope).toBe("draft");
  });

  it("changes progress only on the confirmed plan and retains the replacement draft", async () => {
    const doc = documentWithDraft(); doc.confirmed = structuredClone(doc.draft);
    doc.confirmed!.tasks.forEach(t => { t.owner = "小李"; });
    await mount(doc);
    fireEvent.click(getByRole(root, "button", { name: "执行计划" }));
    const next = structuredClone(doc); next.version = 2; next.confirmed!.tasks[0].status = "done";
    fetchMock.mockResolvedValueOnce(reply(next));
    fireEvent.change(getByLabelText(root, "执行状态"), { target: { value: "done" } });
    await waitFor(() => expect(root.textContent).toContain("任务进度已保存"));
    expect(root.textContent).toContain("已完成 1 / 2 项");
    fireEvent.click(getByRole(root, "button", { name: "草稿", exact: true }));
    expect(root.textContent).toContain("待人工确认");
    expect(root.querySelector('[aria-label="执行状态"]')).toBeNull();
  });
});
