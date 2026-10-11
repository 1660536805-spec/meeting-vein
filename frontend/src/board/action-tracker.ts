import { meetingsApi, type ActionRecord } from "../api/meetings";
import { projectBoard } from "./projection";

const statusLabels: Record<ActionRecord["status"], string> = {
  open: "待开始", in_progress: "进行中", done: "已完成",
  delayed: "已延期", cancelled: "已取消",
};

export function mountActionTracker(host: HTMLElement, meetingId: string) {
  let cells: any[] = [];
  let records: ActionRecord[] = [];
  let revision = 0;

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
      render();
    } catch {
      if (ownRevision !== revision) return;
      host.textContent = "行动台账暂不可用，请稍后重试。";
    }
  }

  return { update };
}
