/** 发言人展示名的唯一来源。
 *
 * 会议发言经三路入口归一化（web / cli / spk / ent / ms），speaker_ref 会带技术前缀
 * （如 `cli:user_主持人`、`spk:3`）。前端任何位置展示发言人都应经由此函数，
 * 优先取后端已解析的 display_name，其次剥离前缀，避免把 `cli:user_主持人` 直接抛给用户。
 */

const SPEAKER_PREFIX = /^(web|cli|spk|ent|ms):/i;
const ROLE_PREFIX = /^(user|host|guest|speaker)_/i;

/** 剥离 speaker_ref 的技术前缀与角色占位前缀，返回可读名字（无有效内容时为空串）。 */
export function stripSpeakerRef(ref?: string | null): string {
  return String(ref ?? "").replace(SPEAKER_PREFIX, "").replace(ROLE_PREFIX, "").trim();
}

/** 发言人展示名：display_name 优先，回退剥离前缀后的 speaker_ref，最后兜底“发言人”。 */
export function formatSpeakerName(ref?: string | null, displayName?: string | null): string {
  const named = String(displayName ?? "").trim();
  if (named) return named;
  return stripSpeakerRef(ref) || "发言人";
}
