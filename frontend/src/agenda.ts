const marker = /^\s*(?:(?:[-*+])\s+|(?:\d{1,3}[.、)]|[一二三四五六七八九十]+[、.])\s*)/;

export function parseAgenda(text: string): string[] {
  const seen = new Set<string>();
  return (text || "").split(/\r?\n/).flatMap((raw) => {
    let line = raw.trim();
    if (!line || line.startsWith(">") || line.startsWith("```")) return [];
    line = line.replace(/^#{1,6}\s*/, "").replace(marker, "")
      .replace(/\s+#+\s*$/, "").replace(/\*\*(.*?)\*\*|__(.*?)__/g, "$1$2")
      .replace(/`([^`]*)`/g, "$1").trim();
    if (["议程", "会议议程", "agenda", "meeting agenda"].includes(line.toLocaleLowerCase())) return [];
    if (!line || seen.has(line)) return [];
    seen.add(line);
    return [line];
  });
}
