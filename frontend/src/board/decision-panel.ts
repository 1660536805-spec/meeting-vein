import { rest, type MetadataRecord } from "../api/rest";
import { projectBoard } from "./projection";
import { findPossibleDuplicates, findPossibleOppositions, findPossibleTopicAssignments, projectDecisionCards } from "./decision-cards";
import { formatSpeakerName } from "./speaker";

const escape = (value: unknown): string => String(value ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]!));

type InsightKind = "focus" | "confirmed" | "conflict" | "action";

export function mountDecisionPanel(onFocus: (id: string) => void,
                                   onReview: (id: string) => void = onFocus,
                                   onMergeDuplicate?: (duplicateId: string, survivorId: string) => void,
                                   onLinkOpposition?: (leftId: string, rightId: string) => void,
                                   onAssignTopic?: (decisionId: string, topicId: string) => void,
                                   meetingId = "default") {
  const metadataCache = new Map<string, MetadataRecord>();
  const dismissalStorageKey = `huimai:decision-hints-dismissed:${encodeURIComponent(meetingId)}`;
  const dismissedCandidates = new Set<string>();
  try {
    const stored = JSON.parse(localStorage.getItem(dismissalStorageKey) ?? "[]");
    if (Array.isArray(stored)) for (const key of stored) if (typeof key === "string") dismissedCandidates.add(key);
  } catch { /* Candidate dismissal is a convenience; a blocked browser store must not break the panel. */ }
  type CandidateKind = "opposition" | "duplicate" | "topic-assignment";
  const candidateKey = (kind: CandidateKind, leftId: string, rightId: string) =>
    `${kind}:${(kind === "topic-assignment" ? [leftId, rightId] : [leftId, rightId].sort()).join("\u0000")}`;
  function persistDismissals(): void {
    try { localStorage.setItem(dismissalStorageKey, JSON.stringify([...dismissedCandidates])); }
    catch { /* Keep the current view usable when browser storage is unavailable. */ }
  }
  function restoreDismissals(kind: CandidateKind, cells: any[], scopeId?: string): void {
    const prefix = scopeId ? `${kind}:${scopeId}\u0000` : `${kind}:`;
    for (const key of dismissedCandidates) if (key.startsWith(prefix)) dismissedCandidates.delete(key);
    persistDismissals();
    renderDecisionCards(cells);
  }
  function candidateDismissButton(kind: CandidateKind, leftId: string, rightId: string, cells: any[]): HTMLButtonElement {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "candidate-dismiss";
    button.textContent = "不再提示";
    button.title = "仅在这场会议中隐藏这条建议；需要时可以恢复。";
    button.addEventListener("click", () => {
      dismissedCandidates.add(candidateKey(kind, leftId, rightId));
      persistDismissals();
      renderDecisionCards(cells);
    });
    return button;
  }
  function candidateRestoreButton(kind: CandidateKind, hiddenCount: number, cells: any[], scopeId?: string): HTMLButtonElement {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "candidate-restore";
    button.textContent = `恢复已隐藏建议（${hiddenCount}）`;
    button.addEventListener("click", () => restoreDismissals(kind, cells, scopeId));
    return button;
  }
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

  function candidateSources(cells: any[], leftId: string, rightId: string): HTMLDivElement {
    const pair = document.createElement("div");
    pair.className = "candidate-source-pair";
    for (const nodeId of [leftId, rightId]) {
      const side = document.createElement("div");
      side.className = "candidate-source-side";
      const node = cells.find((cell) => cell.shape !== "edge" && cell.id === nodeId);
      const refs: unknown[] = Array.isArray(node?.data?.metadata_refs) ? node.data.metadata_refs : [];
      const records = refs.map((id: unknown) => typeof id === "string" ? metadataCache.get(id) : undefined)
        .filter((record): record is MetadataRecord => Boolean(record && record.text?.trim() && record.kind !== "summary"))
        .slice(0, 2);
      if (!records.length) {
        const missing = document.createElement("small");
        missing.className = "candidate-source-empty";
        missing.textContent = "未关联原话";
        side.append(missing);
      }
      for (const record of records) {
        const quote = document.createElement("button");
        quote.type = "button";
        quote.className = "candidate-source-quote";
        quote.textContent = `${formatSpeakerName(record.speaker_ref, record.display_name)}：“${record.text}”`;
        quote.addEventListener("click", () => onFocus(nodeId));
        side.append(quote);
      }
      pair.append(side);
    }
    return pair;
  }

  function renderDecisionCards(cells: any[]) {
    const host = document.getElementById("decision-card-list");
    if (!host) return;
    host.replaceChildren();
    const allOppositionHints = findPossibleOppositions(cells, 0.55, metadataCache);
    const oppositionHints = allOppositionHints.filter((hint) =>
      !dismissedCandidates.has(candidateKey("opposition", hint.leftId, hint.rightId)));
    const hiddenOppositions = allOppositionHints.length - oppositionHints.length;
    if (allOppositionHints.length) {
      const review = document.createElement("section");
      review.className = "possible-opposition-review";
      const heading = document.createElement("h3");
      heading.textContent = `可能存在矛盾 · ${oppositionHints.length}`;
      const note = document.createElement("p");
      note.textContent = "同议题立场冲突会列为候选；原话明确反驳且归属不同的节点也会提示，请核对归属和发言后再确认。";
      review.append(heading, note);
      if (hiddenOppositions) review.append(candidateRestoreButton("opposition", hiddenOppositions, cells));
      for (const hint of oppositionHints) {
        const row = document.createElement("div");
        row.className = "possible-opposition-row";
        const left = document.createElement("button");
        left.type = "button";
        left.textContent = hint.leftLabel;
        left.addEventListener("click", () => onFocus(hint.leftId));
        const right = document.createElement("button");
        right.type = "button";
        right.textContent = hint.rightLabel;
        right.addEventListener("click", () => onFocus(hint.rightId));
        const score = document.createElement("small");
        const basis = hint.signal === "explicit-rebuttal" ? "原话含明确反驳表达"
          : hint.signal === "position" ? "相反办公立场线索"
          : hint.signal === "countermeasure" ? "风险与防护措施线索"
          : hint.signal === "outcome-contrast" ? "员工体验结果差异线索"
          : hint.scoreBasis === "source" ? "原话重合" : "标题重合";
        const terms = hint.matchingTerms.length ? ` · 命中：${hint.matchingTerms.join("、")}` : "";
        const scoreText = hint.signal !== "polarity" ? basis
          : `${basis} · 词项重合分 ${Math.round(hint.score * 100)}/100`;
        const scope = hint.scopeMismatch
          ? `议题归属不同：${hint.leftTopicLabel} ↔ ${hint.rightTopicLabel}` : hint.topicLabel;
        score.textContent = `${scope} · ${scoreText}${terms}`;
        const confirm = document.createElement("button");
        confirm.type = "button";
        confirm.className = "opposition-confirm";
        confirm.textContent = "核对后标为反对关系";
        confirm.disabled = !onLinkOpposition;
        confirm.addEventListener("click", () => onLinkOpposition?.(hint.leftId, hint.rightId));
        row.append(left, document.createTextNode(" ↔ "), right, score,
          candidateSources(cells, hint.leftId, hint.rightId), confirm,
          candidateDismissButton("opposition", hint.leftId, hint.rightId, cells));
        review.append(row);
      }
      host.append(review);
    }
    const allDuplicateHints = findPossibleDuplicates(cells, 0.78, metadataCache);
    const duplicateHints = allDuplicateHints.filter((hint) =>
      !dismissedCandidates.has(candidateKey("duplicate", hint.leftId, hint.rightId)));
    const hiddenDuplicates = allDuplicateHints.length - duplicateHints.length;
    if (allDuplicateHints.length) {
      const review = document.createElement("section");
      review.className = "possible-duplicate-review";
      const heading = document.createElement("h3");
      heading.textContent = `可能重复 · ${duplicateHints.length}`;
      const note = document.createElement("p");
      note.textContent = "比较节点标题和关联原话；线索需核对后再合并。";
      review.append(heading, note);
      if (hiddenDuplicates) review.append(candidateRestoreButton("duplicate", hiddenDuplicates, cells));
      for (const hint of duplicateHints) {
        const row = document.createElement("div");
        row.className = "possible-duplicate-row";
        const left = document.createElement("button");
        left.type = "button";
        left.textContent = hint.leftLabel;
        left.addEventListener("click", () => onFocus(hint.leftId));
        const right = document.createElement("button");
        right.type = "button";
        right.textContent = hint.rightLabel;
        right.addEventListener("click", () => onFocus(hint.rightId));
        const score = document.createElement("small");
        const terms = hint.matchingTerms.length ? ` · 命中：${hint.matchingTerms.join("、")}` : "";
        score.textContent = hint.signal === "shared-measure"
          ? `同议题出现相同指标${terms}`
          : hint.signal === "repeated-position" ? `同一立场在多处重复陈述${terms}`
          : `${hint.scoreBasis === "source" ? "原话相似" : "标题相似"} · 词项重合分 ${Math.round(hint.score * 100)}/100${terms}`;
        const mergeActions = document.createElement("div");
        mergeActions.className = "duplicate-merge-actions";
        const keepLeft = document.createElement("button");
        keepLeft.type = "button";
        keepLeft.className = "duplicate-keep-left";
        keepLeft.textContent = "保留左项并合并右项";
        keepLeft.disabled = !onMergeDuplicate;
        keepLeft.setAttribute("aria-label", `保留${hint.leftLabel}，合并${hint.rightLabel}`);
        keepLeft.addEventListener("click", () => onMergeDuplicate?.(hint.rightId, hint.leftId));
        const keepRight = document.createElement("button");
        keepRight.type = "button";
        keepRight.className = "duplicate-keep-right";
        keepRight.textContent = "保留右项并合并左项";
        keepRight.disabled = !onMergeDuplicate;
        keepRight.setAttribute("aria-label", `保留${hint.rightLabel}，合并${hint.leftLabel}`);
        keepRight.addEventListener("click", () => onMergeDuplicate?.(hint.leftId, hint.rightId));
        mergeActions.append(keepLeft, keepRight);
        row.append(left, document.createTextNode(" ↔ "), right, score,
          candidateSources(cells, hint.leftId, hint.rightId), mergeActions,
          candidateDismissButton("duplicate", hint.leftId, hint.rightId, cells));
        review.append(row);
      }
      host.append(review);
    }
    const cards = projectDecisionCards(cells, metadataCache);
    const assignmentByDecision = new Map<string, ReturnType<typeof findPossibleTopicAssignments>>();
    for (const suggestion of findPossibleTopicAssignments(cells, metadataCache)) {
      const suggestions = assignmentByDecision.get(suggestion.decisionId) ?? [];
      suggestions.push(suggestion);
      assignmentByDecision.set(suggestion.decisionId, suggestions);
    }
    if (!cards.length) {
      if (duplicateHints.length) return;
      const empty = document.createElement("p");
      empty.className = "insight-empty";
      empty.textContent = "出现结论后，可在这里核对支持、反对依据并人工确认。";
      host.append(empty);
      return;
    }
    for (const card of cards) {
      const section = document.createElement("section");
      section.className = "decision-card";
      const title = document.createElement("h3");
      title.textContent = card.label;
      const status = document.createElement("span");
      status.className = `decision-status decision-status-${card.status}`;
      status.textContent = card.status === "confirmed" ? "已确认"
        : card.status === "dismissed" ? "已驳回" : "待人工确认";
      title.append(status);
      section.append(title);
      const topic = document.createElement("button");
      topic.type = "button";
      topic.className = "decision-topic";
      topic.textContent = `归属议题：${card.topic?.label ?? "未识别"}`;
      if (card.topic) topic.addEventListener("click", () => onFocus(card.topic!.id));
      else topic.disabled = true;
      section.append(topic);
      const topicSuggestions = assignmentByDecision.get(card.id) ?? [];
      if (card.reviewFlags.includes("议题归属待核对") || topicSuggestions.length > 0) {
        const reassign = document.createElement("button");
        reassign.type = "button";
        reassign.className = "decision-topic-review";
        reassign.textContent = card.topic ? "复核议题归属" : "调整议题归属";
        reassign.addEventListener("click", () => onReview(card.id));
        section.append(reassign);
        if (topicSuggestions.length) {
          const candidateHost = document.createElement("div");
          candidateHost.className = "decision-topic-suggestions";
          const visibleSuggestions = topicSuggestions.filter((suggestion) =>
            !dismissedCandidates.has(candidateKey("topic-assignment", card.id, suggestion.topicId)));
          const hiddenSuggestions = topicSuggestions.length - visibleSuggestions.length;
          const heading = document.createElement("small");
          heading.textContent = card.topic
            ? "当前归属与其他议题对照后，以下议题的文本证据更匹配；请核对后再保存。"
            : "按结论、原话和议题下内容的文字重合推荐；请核对后再保存。";
          candidateHost.append(heading);
          if (hiddenSuggestions) candidateHost.append(candidateRestoreButton("topic-assignment", hiddenSuggestions, cells, card.id));
          for (const suggestion of visibleSuggestions) {
            const candidate = document.createElement("button");
            candidate.type = "button";
            candidate.className = "topic-assignment-suggestion";
            const terms = suggestion.matchingTerms.join("、");
            candidate.textContent = `建议归属：${suggestion.topicLabel} · 命中：${terms}`;
            candidate.title = `词项重合分 ${Math.round(suggestion.score * 100)}/100；点击后会在编辑器中预选议题，需手动保存。`;
            candidate.disabled = !onAssignTopic;
            candidate.addEventListener("click", () => onAssignTopic?.(card.id, suggestion.topicId));
            candidateHost.append(candidate);
            candidateHost.append(candidateDismissButton("topic-assignment", card.id, suggestion.topicId, cells));
          }
          section.append(candidateHost);
        }
      }
      if (card.reviewFlags.length) {
        const flags = document.createElement("p");
        flags.className = "decision-review-flags";
        flags.textContent = `核对重点：${card.reviewFlags.join(" · ")}`;
        section.append(flags);
      }
      const sourceHeading = document.createElement("p");
      sourceHeading.className = "decision-source-heading";
      sourceHeading.textContent = "结论原话";
      section.append(sourceHeading);
      if (card.sourceQuotes.length) {
        for (const quote of card.sourceQuotes) {
          const quoteButton = document.createElement("button");
          quoteButton.type = "button";
          quoteButton.className = "decision-quote";
          quoteButton.textContent = `${quote.speaker}：“${quote.text}”`;
          quoteButton.addEventListener("click", () => onFocus(card.id));
          section.append(quoteButton);
        }
      } else {
        const missing = document.createElement("p");
        missing.className = "decision-quote-missing";
        missing.textContent = card.sourceRefs.length ? "原话暂未加载，可点下方查看来源。" : "没有直接关联原话。";
        section.append(missing);
      }
      for (const [heading, items] of [["支持依据", card.supports], ["反对依据", card.objections]] as const) {
        const row = document.createElement("p");
        row.className = "decision-evidence";
        row.textContent = `${heading}：${items.length ? items.map((item) => item.label).join("；") : "暂无"}`;
        section.append(row);
        for (const item of items) {
          for (const quote of item.quotes) {
            const quoteButton = document.createElement("button");
            quoteButton.type = "button";
            quoteButton.className = `decision-quote decision-quote-${heading === "支持依据" ? "support" : "oppose"}`;
            quoteButton.textContent = `${quote.speaker}：“${quote.text}”`;
            quoteButton.addEventListener("click", () => onFocus(item.id));
            section.append(quoteButton);
          }
        }
      }
      if (card.missingEvidence) {
        const warning = document.createElement("p");
        warning.className = "decision-warning";
        warning.textContent = "尚无可追溯原话依据";
        section.append(warning);
      }
      const review = document.createElement("button");
      review.type = "button";
      review.className = "decision-review";
      review.textContent = "查看来源并审核";
      review.addEventListener("click", () => onReview(card.id));
      section.append(review);
      host.append(section);
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
      node.data.status === "confirmed" && node.data.manual_override?.status === true).map((node) => node.cell);
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
    renderDecisionCards(cells);
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
    renderDecisionCards(cells);
    const records = ids.map((id) => metadataCache.get(id)).filter((r): r is MetadataRecord => Boolean(r && r.text && r.kind !== "summary")).reverse();
    utteranceCount.textContent = String(records.length);
    records.slice(0, 8).forEach((record) => {
      const speaker = formatSpeakerName(record.speaker_ref, record.display_name);
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
