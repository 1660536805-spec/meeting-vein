import type { MeetingTopicSuggestion } from "./api/meetings";

export function bindLinkSuggestionSearch(input: HTMLInputElement, onSearch: (query: string) => void): void {
  input.addEventListener("input", () => onSearch(input.value));
}

export function renderLinkSuggestions(
  host: HTMLElement,
  suggestions: MeetingTopicSuggestion[],
  onChoose: (suggestion: MeetingTopicSuggestion) => void,
  onDismiss: (suggestion: MeetingTopicSuggestion, button: HTMLButtonElement) => void,
  query = "",
): void {
  host.replaceChildren();
  if (!suggestions.length) {
    const note = document.createElement("p");
    note.className = "history-link-suggestion-empty";
    note.textContent = query.trim()
      ? "没有找到匹配的历史候选；可以在下方手动关联。"
      : "没有待处理的自动候选；可以在下方手动关联，也可重新查找。";
    host.append(note);
    return;
  }
  for (const suggestion of suggestions) {
    const searchOnly = suggestion.search_only === true;
    const card = document.createElement("section");
    card.className = `history-link-suggestion${searchOnly ? " history-link-search-hit" : ""}`;
    const heading = document.createElement("strong");
    heading.textContent = `${suggestion.source_meeting_title} · ${suggestion.source_topic_label}`;
    const target = document.createElement("p");
    target.textContent = searchOnly
      ? "搜索命中旧会议资料，请在下方选择本会议议题后确认关联。"
      : `可能延续到：${suggestion.target_topic_label}`;
    const terms = document.createElement("small");
    const matched = searchOnly
      ? [`搜索命中旧会议资料：${(suggestion.matched_search_terms ?? []).join("、")}`]
      : [
        suggestion.matching_terms.length ? `标题词：${suggestion.matching_terms.join("、")}` : "",
        suggestion.matching_context_terms.length ? `议题内容/原话词：${suggestion.matching_context_terms.join("、")}` : "",
      ].filter(Boolean);
    const parsedUpdatedAt = suggestion.source_meeting_updated_at
      ? new Date(suggestion.source_meeting_updated_at) : null;
    const updatedAt = parsedUpdatedAt && !Number.isNaN(parsedUpdatedAt.getTime())
      ? parsedUpdatedAt.toLocaleDateString("zh-CN") : "日期未知";
    terms.textContent = `${matched.join(" · ")} · 最近更新 ${updatedAt} · 需人工确认`;
    const evidence = document.createElement("div");
    evidence.className = "history-link-suggestion-evidence";
    for (const [meetingSide, records] of [
      ["上次原话", suggestion.source_evidence ?? []],
      ["本次原话", suggestion.target_evidence ?? []],
    ] as const) {
      for (const record of records) {
        const quote = document.createElement("p");
        quote.textContent = `${meetingSide} · ${record.node_label} · ${record.speaker}：${record.text}`;
        evidence.append(quote);
      }
    }
    const choose = document.createElement("button");
    choose.type = "button";
    choose.className = "history-link-choose";
    choose.textContent = searchOnly ? "填入旧议题" : "填入关联表单";
    choose.addEventListener("click", () => onChoose(suggestion));
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "history-link-dismiss";
    dismiss.textContent = "不再提示";
    dismiss.title = "隐藏这组议题候选；仍可在下方手动关联。";
    dismiss.addEventListener("click", () => onDismiss(suggestion, dismiss));
    const actions = document.createElement("div");
    actions.className = "history-link-suggestion-actions";
    actions.append(choose);
    if (!searchOnly) actions.append(dismiss);
    card.append(heading, target, terms, evidence, actions);
    host.append(card);
  }
}
