interface NormalizedText {
  value: string;
  starts: number[];
  ends: number[];
}

function normalizeWithOffsets(text: string): NormalizedText {
  const value: string[] = [];
  const starts: number[] = [];
  const ends: number[] = [];
  for (let offset = 0; offset < text.length;) {
    const point = String.fromCodePoint(text.codePointAt(offset)!);
    const nextOffset = offset + point.length;
    for (const normalized of point.normalize("NFKC").toLocaleLowerCase()) {
      if (/[\p{P}\p{Z}\s]/u.test(normalized)) continue;
      value.push(normalized);
      starts.push(offset);
      ends.push(nextOffset);
    }
    offset = nextOffset;
  }
  return { value: value.join(""), starts, ends };
}

function trimPunctuation(text: string): string {
  return text.replace(/^[\p{P}\p{Z}\s]+|[\p{P}\p{Z}\s]+$/gu, "").trim();
}

/**
 * Returns complete-transcript spans not already represented by streamed finals.
 * A null result means an exact ordered reconciliation was not safe enough to automate.
 */
export function uncoveredTranscriptSegments(
  completeText: string,
  deliveredTexts: readonly string[],
): string[] | null {
  const whole = normalizeWithOffsets(completeText);
  if (!whole.value) return null;
  const ranges: Array<{ start: number; end: number }> = [];
  let cursor = 0;
  for (const text of deliveredTexts) {
    const target = normalizeWithOffsets(text).value;
    if (target.length < 2) return null;
    const matchAt = whole.value.indexOf(target, cursor);
    if (matchAt < 0) return null;
    ranges.push({
      start: whole.starts[matchAt],
      end: whole.ends[matchAt + target.length - 1],
    });
    cursor = matchAt + target.length;
  }

  const uncovered: string[] = [];
  let rawCursor = 0;
  for (const range of ranges) {
    const gap = trimPunctuation(completeText.slice(rawCursor, range.start));
    if (normalizeWithOffsets(gap).value.length >= 2) uncovered.push(gap);
    rawCursor = range.end;
  }
  const tail = trimPunctuation(completeText.slice(rawCursor));
  if (normalizeWithOffsets(tail).value.length >= 2) uncovered.push(tail);
  return uncovered;
}
