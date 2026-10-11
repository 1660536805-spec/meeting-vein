import { meetingsApi, type ActionRecord } from "../api/meetings";
import { projectBoard } from "./projection";

const statusLabels: Record<ActionRecord["status"], string> = {
  open: "待开始", in_progress: "进行中", done: "已完成",
  delayed: "已延期", cancelled: "已取消",
};

function calendarFeedUrl(meetingId: string): string {
  const localHost = location.hostname === "127.0.0.1" || location.hostname === "localhost";
  const base = localHost ? `${location.protocol}//${location.hostname}:8000` : location.origin;
  const url = new URL(`/api/meetings/${encodeURIComponent(meetingId)}/actions.ics`, base);
  return url.href.replace(/^https?:/, "webcal:");
}

function externalSyncErrorLabel(code: string): string {
  if (code === "remote_unavailable") return "任务工具暂不可用";
  if (code === "https_required") return "远程 Webhook 必须使用 HTTPS";
  if (code === "invalid_webhook_url") return "Webhook 地址无效";
  const status = code.match(/^remote_http_(\d{3})$/)?.[1];
  if (status === "401" || status === "403") return "任务工具拒绝了连接";
  return status ? `任务工具返回 HTTP ${status}` : "任务工具同步失败";
}

function externalSyncFailureMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  if (message.includes("task_sync_not_configured")) return "外部任务同步未配置，请先设置 AMO_TASK_WEBHOOK_URL。";
  if (message.includes("https_required")) return "远程任务 Webhook 必须使用 HTTPS。";
  if (message.includes("remote_unavailable")) return "任务工具连接失败；行动项已保留，可重试。";
  return "任务同步失败；请检查 Webhook 配置和网络后重试。";
}

export function mountActionTracker(host: HTMLElement, meetingId: string) {
  let cells: any[] = [];
  let records: ActionRecord[] = [];
  let revision = 0;
  let externalSyncConfigured = false;

  function render(message = "") {
    host.replaceChildren();
    const board = projectBoard(cells);
    const nodes = board.actions;
    const decisions = board.conclusions.filter((item) => item.data.status === "confirmed"
      && item.data.manual_override?.status === true);
    if (!nodes.length) {
      const empty = document.createElement("p");
      empty.className = "insight-empty";
      empty.textContent = "识别出待办后，可在这里指定负责人和期限。";
      host.append(empty);
      return;
    }
    const calendarTools = document.createElement("div");
    calendarTools.className = "action-calendar-tools";
    const downloadCalendar = document.createElement("button");
    downloadCalendar.type = "button";
    downloadCalendar.className = "action-calendar-download";
    downloadCalendar.textContent = "下载日历提醒";
    const subscribeCalendar = document.createElement("a");
    subscribeCalendar.className = "action-calendar-subscribe";
    subscribeCalendar.href = calendarFeedUrl(meetingId);
    subscribeCalendar.textContent = "订阅到本机日历";
    subscribeCalendar.title = "到期前一天和到期日提醒；逾期未完成的事项每日提醒。日历客户端按自身策略刷新，会脉本机服务需保持运行。";
    const syncTasks = document.createElement("button");
    syncTasks.type = "button";
    syncTasks.className = "action-sync";
    syncTasks.textContent = externalSyncConfigured ? "同步到任务工具" : "配置外部任务同步";
    syncTasks.disabled = !externalSyncConfigured;
    syncTasks.title = externalSyncConfigured
      ? "显式发送有变更的行动项；接收方按稳定任务编号更新任务。仅发送任务字段，不发送会议原话。"
      : "在本机服务的 .env 中设置 AMO_TASK_WEBHOOK_URL 后可启用。";
    const calendarMessage = document.createElement("span");
    calendarMessage.className = "action-calendar-message";
    calendarMessage.setAttribute("role", "status");
    if (!externalSyncConfigured) calendarMessage.textContent = "外部任务同步未配置。";
    downloadCalendar.addEventListener("click", async () => {
      downloadCalendar.disabled = true;
      calendarMessage.textContent = "正在生成日历文件…";
      try {
        const calendar = await meetingsApi.actionCalendar(meetingId);
        const blob = new Blob([calendar], { type: "text/calendar;charset=utf-8" });
        const objectUrl = typeof URL.createObjectURL === "function" ? URL.createObjectURL(blob) : null;
        const anchor = document.createElement("a");
        anchor.href = objectUrl ?? `data:text/calendar;charset=utf-8,${encodeURIComponent(calendar)}`;
        anchor.download = `${meetingId}-actions.ics`;
        anchor.click();
        if (objectUrl) window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
        calendarMessage.textContent = "日历文件已生成；逾期事项只提醒一次。需要持续提醒，请订阅日历。";
      } catch {
        calendarMessage.textContent = "日历导出失败，请检查本机服务后重试。";
      } finally {
        downloadCalendar.disabled = false;
      }
    });
    syncTasks.addEventListener("click", async () => {
      if (!externalSyncConfigured) return;
      syncTasks.disabled = true;
      calendarMessage.textContent = "正在发送有变更的行动项…";
      try {
        const result = await meetingsApi.syncActions(meetingId);
        records = result.actions;
        render(result.sync_state === "up_to_date"
          ? "行动项均已同步，没有待发送变更。"
          : `已发送 ${result.synced_count} 项，任务工具已受理。`);
      } catch (error) {
        try {
          const latest = await meetingsApi.actions(meetingId);
          records = latest.actions;
          externalSyncConfigured = Boolean(latest.external_task_sync_configured);
        } catch { /* Keep the current action records visible when status refresh fails. */ }
        render(externalSyncFailureMessage(error));
      }
    });
    calendarTools.append(downloadCalendar, subscribeCalendar, syncTasks, calendarMessage);
    host.append(calendarTools);
    for (const node of nodes) {
      const record = records.find((item) => item.action_node_id === node.id);
      const card = document.createElement("section");
      card.className = "action-tracker-card";
      const title = document.createElement("strong");
      title.textContent = String(node.data.label ?? "未命名待办");
      card.append(title);
      if (!record) {
        const decisionLabel = document.createElement("label");
        decisionLabel.textContent = "来源决议";
        const decisionSelect = document.createElement("select");
        decisionSelect.className = "action-decision";
        decisionSelect.add(new Option("暂不关联", ""));
        for (const decision of decisions) {
          decisionSelect.add(new Option(String(decision.data.label ?? decision.id), decision.id));
        }
        decisionLabel.append(decisionSelect);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "action-register";
        button.textContent = "纳入行动台账";
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            const result = await meetingsApi.registerAction(
              meetingId, node.id, `track:${node.id}`.slice(0, 120), decisionSelect.value || null);
            records = [...records.filter((item) => item.action_node_id !== node.id), result.action];
            render("已纳入行动台账");
          } catch {
            button.disabled = false;
            button.textContent = "加入失败，点击重试";
          }
        });
        card.append(decisionLabel, button);
      } else {
        const syncStatus = document.createElement("p");
        syncStatus.className = "action-external-sync-status";
        const syncedVersion = record.external_sync_version ?? null;
        const syncPending = record.external_sync_pending
          ?? (syncedVersion !== null && syncedVersion !== record.version);
        if (record.external_sync_error) {
          syncStatus.textContent = `外部同步失败：${externalSyncErrorLabel(record.external_sync_error)}，可重试。`;
        } else if (syncPending) {
          syncStatus.textContent = "行动项已有更新，待同步到任务工具。";
        } else if (syncedVersion === record.version) {
          syncStatus.textContent = "任务工具已接收此版本。";
        } else if (syncedVersion !== null) {
          syncStatus.textContent = "行动项已有更新，待同步到任务工具。";
        } else if (externalSyncConfigured) {
          syncStatus.textContent = "尚未发送到外部任务工具。";
        }
        if (syncStatus.textContent) card.append(syncStatus);
        if (record.status !== "done" && record.status !== "cancelled") {
          const warnings: string[] = [];
          if (!record.owner) warnings.push("缺少负责人");
          if (!record.due_date) warnings.push("缺少期限");
          else if (record.due_date < new Date().toISOString().slice(0, 10)) warnings.push("已逾期");
          if (warnings.length) {
            const warning = document.createElement("p");
            warning.className = "action-warning";
            warning.textContent = warnings.join(" · ");
            card.append(warning);
          }
        }
        if (record.decision_node_id) {
          const linked = board.nodeById.get(record.decision_node_id);
          const source = document.createElement("p");
          source.className = "action-source-decision";
          source.textContent = `来源决议：${linked?.data.label ?? "原决议已移除"}`;
          card.append(source);
        }
        const ownerLabel = document.createElement("label");
        ownerLabel.textContent = "负责人";
        const owner = document.createElement("input");
        owner.className = "action-owner";
        owner.maxLength = 120;
        owner.value = record.owner ?? "";
        ownerLabel.append(owner);
        const dueLabel = document.createElement("label");
        dueLabel.textContent = "期限";
        const due = document.createElement("input");
        due.type = "date";
        due.className = "action-due";
        due.value = record.due_date ?? "";
        dueLabel.append(due);
        const statusLabel = document.createElement("label");
        statusLabel.textContent = "状态";
        const status = document.createElement("select");
        status.className = "action-status";
        for (const [value, label] of Object.entries(statusLabels)) {
          status.add(new Option(label, value));
        }
        status.value = record.status;
        statusLabel.append(status);
        const save = document.createElement("button");
        save.type = "button";
        save.className = "action-save";
        save.textContent = "保存跟进";
        save.addEventListener("click", async () => {
          save.disabled = true;
          try {
            const result = await meetingsApi.updateAction(meetingId, record.id, record.version, {
              owner: owner.value.trim() || null,
              due_date: due.value || null,
              status: status.value as ActionRecord["status"],
            });
            records = records.map((item) => item.id === record.id ? result.action : item);
            render("已保存");
          } catch {
            save.disabled = false;
            save.textContent = "保存失败，请核对版本后重试";
          }
        });
        const historyToggle = document.createElement("button");
        historyToggle.type = "button";
        historyToggle.className = "action-history-toggle";
        historyToggle.textContent = "查看变更记录";
        const history = document.createElement("ol");
        history.className = "action-history";
        history.hidden = true;
        historyToggle.addEventListener("click", async () => {
          if (!history.hidden) {
            history.hidden = true;
            historyToggle.textContent = "查看变更记录";
            return;
          }
          historyToggle.disabled = true;
          try {
            const result = await meetingsApi.actionHistory(meetingId, record.id);
            history.replaceChildren();
            for (const event of result.events) {
              const item = document.createElement("li");
              const statusText = statusLabels[event.after.status];
              const eventTime = new Date(event.created_at);
              const localTime = Number.isNaN(eventTime.getTime()) ? event.created_at
                : eventTime.toLocaleString("zh-CN", { hour12: false });
              item.textContent = `${localTime} · ${event.event_type === "registered" ? "纳入台账" : "更新"} · ${statusText} · 负责人：${event.after.owner || "待补充"} · 期限：${event.after.due_date || "待补充"}`;
              history.append(item);
            }
            history.hidden = false;
            historyToggle.textContent = "收起变更记录";
          } catch {
            historyToggle.textContent = "读取失败，点击重试";
          } finally {
            historyToggle.disabled = false;
          }
        });
        card.append(ownerLabel, dueLabel, statusLabel, save, historyToggle, history);
      }
      host.append(card);
    }
    if (message) {
      const feedback = document.createElement("p");
      feedback.setAttribute("role", "status");
      feedback.textContent = message;
      host.prepend(feedback);
    }
  }

  async function update(nextCells: any[]) {
    const ownRevision = ++revision;
    cells = nextCells;
    if (!projectBoard(cells).actions.length) {
      records = [];
      render();
      return;
    }
    try {
      const response = await meetingsApi.actions(meetingId);
      if (ownRevision !== revision) return;
      records = response.actions;
      externalSyncConfigured = Boolean(response.external_task_sync_configured);
      render();
    } catch {
      if (ownRevision !== revision) return;
      host.textContent = "行动台账暂不可用，请稍后重试。";
    }
  }

  return { update };
}
