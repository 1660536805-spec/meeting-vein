import { rest, type MetadataRecord } from "../api/rest";
import { projectBoard } from "./projection";

const escape = (value: unknown): string => String(value ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]!));

type InsightKind = "focus" | "confirmed" | "conflict" | "action";

export function mountDecisionPanel(onFocus: (id: string) => void) {
  const metadataCache = new Map<string, MetadataRecord>();
  const railHost = document.getElementById("utterance-rail")!;
  const rail = document.getElementById("utterance-list")!;
  const utteranceCount = document.getElementById("utterance-count")!;
  const summaryHost = document.getElementById("meeting-summary")!;
  let revision = 0;

  function group(kind: InsightKind, nodes: any[]) {
    document.getElementById(`${kind}-count`)!.textContent = String(nodes.length);
    const list = document.getElementById(`${kind}-list`)!;
    list.replaceChildren();
    if (!nodes.length) {
      const empty = document.createElement("p"); empty.className = "insight-empty";
      empty.textContent = { focus: "关键观点将在讨论中逐渐浮现。", confirmed: "确认后的结论会汇总在这里。", conflict: "目前没有尚未解决的分歧。", action: "明确的行动项会整理在这里。" }[kind];
      list.append(empty);
    }
    for (const node of nodes) {
      const button = document.createElement("button"); button.type = "button"; button.className = "insight-card";
      button.addEventListener("click", () => onFocus(node.id));
      const label = String(node.data?.label ?? "未命名观点");
      const title = document.createElement("strong"); title.textContent = label; button.append(title);
      const refs = node.data?.metadata_refs ?? [];
      const context = refs.map((id: string) => metadataCache.get(id)?.text).filter(Boolean).join(" ");
      const description = node.data?.description ?? context;
      if (description && description !== label) { const p = document.createElement("p"); p.textContent = String(description); button.append(p); }
      list.append(button);
    }
  }

  async function update(cells: any[]) {
    const ownRevision = ++revision;
    const projection = projectBoard(cells);
    const nodes = projection.nodes.map((item) => item.cell);
    railHost.hidden = nodes.length === 0;
    rail.replaceChildren();
    utteranceCount.textContent = "0";
    const focus = projection.focus ? [projection.focus.cell] : [];
    const root = projection.roots.map((id) => projection.nodeById.get(id)).find(Boolean);
    const confirmed = projection.conclusions.filter((node) =>
      node.data.status === "confirmed" || node.data.resolved === true).map((node) => node.cell);
    const conflicts = projection.unresolved.map((item) => {
      const participants = item.nodeIds.map((id) => projection.nodeById.get(id)).filter(Boolean);
      const representative = participants[0];
      const names = participants.map((node) => String(node!.data.label ?? "未命名观点"));
      const label = item.kind === "dispute" && item.source === "oppose-edge" && names.length > 1
        ? names.join(" ↔ ") : item.label;
      return {
        id: representative?.id ?? item.id,
        data: {
          label,
          description: item.kind === "confirmation" ? "结论尚待确认" : item.label,
          metadata_refs: [...new Set(participants.flatMap((node) => node!.data.metadata_refs ?? []))],
        },
      };
    });
    const actions = projection.actions.map((item) => item.cell);
    summaryHost.textContent = root
      ? `${String(root.data.label ?? "未命名会议")} · 已确认 ${confirmed.length} 项 · 尚未解决 ${projection.unresolved.length} 项 · 下一步 ${actions.length} 项`
      : "发言后会在这里汇总议题、结论和下一步。";
    const renderGroups = () => {
      group("focus", focus); group("confirmed", confirmed); group("conflict", conflicts); group("action", actions);
    };
    renderGroups();
    if (!nodes.length) return;

    const nodeForMeta = new Map<string, string>();
    nodes.forEach((node) => (node.data?.metadata_refs ?? []).forEach((id: string) => nodeForMeta.set(id, node.id)));
    const ids = [...nodeForMeta.keys()];
    const missing = ids.filter((id) => !metadataCache.has(id));
    if (missing.length) {
      try { const result = await rest.metadata(missing); result.records.forEach((record) => metadataCache.set(record.meta_id, record)); }
      catch { /* Keep the board and summary available if evidence lookup fails. */ }
    }
    if (ownRevision !== revision) return;
    renderGroups();
    const records = ids.map((id) => metadataCache.get(id)).filter((r): r is MetadataRecord => Boolean(r && r.text && r.kind !== "summary")).reverse();
    utteranceCount.textContent = String(records.length);
    records.slice(0, 8).forEach((record) => {
      const speaker = record.display_name?.trim() || record.speaker_ref?.replace(/^(web|cli|spk|ent|ms):/, "") || "发言人";
      const time = projection.formatEvidenceTime(record);
      const card = document.createElement("button"); card.type = "button"; card.className = "utterance-card";
      card.innerHTML = `<div class="utterance-head"><span class="speaker-avatar">${escape(speaker[0])}</span><span>${escape(speaker)}</span><small>${escape(time)}</small></div><p>${escape(record.text)}</p>`;
      card.addEventListener("click", () => { const id = nodeForMeta.get(record.meta_id); if (id) onFocus(id); }); rail.append(card);
    });
    if (!records.length) { const p = document.createElement("p"); p.className = "rail-empty"; p.textContent = "关联原始发言后，可在这里回看讨论来源。"; rail.append(p); }
    if (records.length > 8) { const p = document.createElement("p"); p.className = "utterance-more"; p.textContent = `还有 ${records.length - 8} 条相关发言`; rail.append(p); }
  }
  void update([]);
  return { update };
}
