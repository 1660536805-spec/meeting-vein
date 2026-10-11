export type TaskStatus = "todo" | "in_progress" | "done";
export interface PlanTask {
  id: string; title: string; owner: string;
  start_date: string | null; due_date: string | null; duration_days: number;
  depends_on: string[]; source_node_id: string; source_refs: string[]; source_quote: string;
  suggested_fields: string[]; rationale: string; status: TaskStatus;
}
export interface Plan {
  mode: "ai" | "demo"; reference_date: string; tasks: PlanTask[]; warnings: string[];
  confirmed_at?: string;
}
export interface Source { meta_id: string; text: string; speaker: string }
export interface PlanDocument {
  meeting_id: string; version: number; draft: Plan | null; confirmed: Plan | null; sources?: Source[];
}
export interface PlanAnswer {
  answer: string; mode: "ai" | "demo"; task_ids: string[]; sources: Source[];
  version: number; scope: "draft" | "confirmed";
}

export function taskPlanApi(meetingId: string) {
  const base = `/api/meetings/${encodeURIComponent(meetingId)}/task-plan`;
  async function request<T>(path = "", body?: unknown): Promise<T> {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 120000);
    try {
      const response = await fetch(base + path, {
        method: body === undefined ? "GET" : "POST", signal: controller.signal,
        headers: body === undefined ? undefined : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        const detail = error?.detail;
        const message = typeof detail === "string" ? detail : detail?.message;
        throw new Error(message || (response.status === 422 ? "任务字段无效，请检查日期和必填项。" : `请求失败（${response.status}），请重试。`));
      }
      return await response.json() as T;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw new Error("请求超时。当前输入已保留，请刷新检查保存状态后重试。");
      throw error;
    } finally { window.clearTimeout(timer); }
  }
  return {
    read: () => request<PlanDocument>(),
    generate: (version: number, reference_date: string) => request<PlanDocument>("/generate", { expected_version: version, reference_date }),
    save: (version: number, tasks: PlanTask[]) => request<PlanDocument>("/draft", { expected_version: version, tasks }),
    confirm: (version: number, tasks: PlanTask[]) => request<PlanDocument>("/confirm", { expected_version: version, tasks }),
    progress: (version: number, id: string, status: TaskStatus) => request<PlanDocument>(`/tasks/${encodeURIComponent(id)}/status`, { expected_version: version, status }),
    ask: (version: number, scope: "draft" | "confirmed", question: string) => request<PlanAnswer>("/ask", { expected_version: version, scope, question }),
    exportUrl: (scope: string) => `${base}/export?scope=${scope}`,
  };
}
