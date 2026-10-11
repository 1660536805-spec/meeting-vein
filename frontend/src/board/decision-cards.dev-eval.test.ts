import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { findPossibleDuplicates, findPossibleOppositions } from "./decision-cards";

function parseCsv(source: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (quoted && char === '"' && source[index + 1] === '"') {
      field += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === "," && !quoted) {
      row.push(field);
      field = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && source[index + 1] === "\n") index += 1;
      row.push(field);
      if (row.some((value) => value.length > 0)) rows.push(row);
      row = [];
      field = "";
    } else {
      field += char;
    }
  }
  if (field.length || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

const cellKey = (left: string, right: string) => [left, right].sort().join("\u0000");

describe("decision review hints on the preliminary debate sample", () => {
  it("measures real utterance candidates against first-pass labels for diagnosis only", () => {
    const path = resolve(process.cwd(), "../backend/tests/annotations/debate-200-gold.csv");
    const parsed = parseCsv(readFileSync(path, "utf8").replace(/^\uFEFF/, ""));
    const [headers, ...records] = parsed;
    const rows = records.map((values) => Object.fromEntries(headers.map((key, index) => [key, values[index] ?? ""])));
    const cells = [
      { id: "n_issue_root", shape: "amo-node", data: { type: "issue", label: "辩论赛", parent_id: null } },
      { id: "n_topic", shape: "amo-node", data: { type: "issue", label: "远程办公与固定坐班", parent_id: "n_issue_root" } },
      ...rows.map((row) => ({
        id: `n_${row.line_no}`, shape: "amo-node", data: {
          type: "point", label: row.gold_label || `发言${row.line_no}`,
          parent_id: "n_topic", metadata_refs: [`u_${row.line_no}`],
        },
      })),
    ];
    const metadata = new Map(rows.map((row) => [`u_${row.line_no}`, { kind: "utt", text: row.utterance }]));
    const oppositionHints = findPossibleOppositions(cells, 0.55, metadata);
    const oppositionAllHints = findPossibleOppositions(cells, 0.55, metadata, 10_000);
    const duplicateCells = [
      ...cells.slice(0, 2),
      ...rows.map((row) => ({
        id: `n_${row.line_no}`, shape: "amo-node", data: {
          type: "point", label: row.utterance,
          parent_id: "n_topic", metadata_refs: [`u_${row.line_no}`],
        },
      })),
    ];
    const duplicateHints = findPossibleDuplicates(duplicateCells, 0.78, metadata);
    const duplicateAllHints = findPossibleDuplicates(duplicateCells, 0.78, metadata, 10_000);
    const oppositionHintPairs = new Set(oppositionHints.map((hint) => cellKey(hint.leftId, hint.rightId)));
    const duplicateHintPairs = new Set(duplicateHints.map((hint) => cellKey(hint.leftId, hint.rightId)));
    const oppositionGold = rows.filter((row) => row.relation_anchor.startsWith("oppose:") && row.relation_target_line_no);
    const duplicateGold = rows.filter((row) => row.relation_anchor.startsWith("duplicate:") && row.relation_target_line_no);
    const pairFor = (row: Record<string, string>) => cellKey(`n_${row.line_no}`, `n_${row.relation_target_line_no}`);
    const oppositionHits = oppositionGold.filter((row) => oppositionHintPairs.has(pairFor(row))).length;
    const duplicateHits = duplicateGold.filter((row) => duplicateHintPairs.has(pairFor(row))).length;
    const oppositionGoldRanks = oppositionGold.map((row) => {
      const rank = oppositionAllHints.findIndex((hint) => cellKey(hint.leftId, hint.rightId) === pairFor(row));
      const hint = rank < 0 ? null : oppositionAllHints[rank];
      return { pair: `${row.line_no}->${row.relation_target_line_no}`, rank: rank < 0 ? null : rank + 1,
        signal: hint?.signal ?? null, terms: hint?.matchingTerms ?? [] };
    });
    console.info("[decision-hint-dev-eval]", JSON.stringify({
      sampleRows: rows.length,
      preliminaryOppositionPairs: oppositionGold.length,
      oppositionCandidates: oppositionAllHints.length,
      oppositionTop20Hits: oppositionHits,
      oppositionGoldRanks,
      oppositionTop20Signals: oppositionHints.reduce<Record<string, number>>((counts, hint) => {
        counts[hint.signal] = (counts[hint.signal] ?? 0) + 1;
        return counts;
      }, {}),
      preliminaryDuplicatePairs: duplicateGold.length,
      duplicateSuggestions: duplicateHints.length,
      duplicateGoldPairsHit: duplicateHits,
      duplicateCandidates: duplicateAllHints.length,
      duplicateTopHints: duplicateHints.map((hint, index) => ({ rank: index + 1,
        signal: hint.signal, score: hint.score, left: hint.leftLabel, right: hint.rightLabel,
        terms: hint.matchingTerms })),
      duplicateGoldRanks: duplicateGold.map((row) => {
        const rank = duplicateAllHints.findIndex((hint) =>
          cellKey(hint.leftId, hint.rightId) === pairFor(row));
        return { pair: `${row.line_no}->${row.relation_target_line_no}`, rank: rank < 0 ? null : rank + 1,
          signal: rank < 0 ? null : duplicateAllHints[rank].signal };
      }),
    }));

    // These are development-set smoke checks, not a model-quality gate. The CSV
    // target rows have not yet received independent annotation and adjudication.
    expect(rows).toHaveLength(200);
    expect(oppositionGold.length).toBeGreaterThan(0);
    expect(duplicateGold.length).toBeGreaterThan(0);
    expect(oppositionHints).toHaveLength(20);
    expect(oppositionHits).toBeGreaterThanOrEqual(4);
    expect(duplicateHits).toBeGreaterThanOrEqual(1);
  });

  it("replays duplicate hints against nodes from the saved real-model run", () => {
    const goldPath = resolve(process.cwd(), "../backend/tests/annotations/debate-200-gold.csv");
    const predictionPath = resolve(process.cwd(), "../doc/reports/predictions-real-debate200-v2.jsonl");
    const [headers, ...records] = parseCsv(readFileSync(goldPath, "utf8").replace(/^\uFEFF/, ""));
    const rows = records.map((values) => Object.fromEntries(headers.map((key, index) => [key, values[index] ?? ""])));
    const predictions = readFileSync(predictionPath, "utf8").trim().split(/\r?\n/).map((line) => JSON.parse(line));
    const predictionByLine = new Map(predictions.map((row: any) => [Number(row.line_no), row]));
    const nodeRows = new Map<string, Array<{ lineNo: number; type: string; utterance: string }>>();
    for (const row of rows) {
      const lineNo = Number(row.line_no);
      const prediction = predictionByLine.get(lineNo) as any;
      if (!prediction?.node_id) continue;
      const group = nodeRows.get(prediction.node_id) ?? [];
      group.push({ lineNo, type: String(prediction.type || "point"), utterance: row.utterance });
      nodeRows.set(prediction.node_id, group);
    }
    const metadata = new Map<string, { kind: string; text: string }>();
    const cells = [
      { id: "n_issue_root", shape: "amo-node", data: { type: "issue", label: "辩论赛", parent_id: null } },
      { id: "n_topic", shape: "amo-node", data: { type: "issue", label: "远程办公与固定坐班", parent_id: "n_issue_root" } },
      ...[...nodeRows.entries()].map(([nodeId, sources]) => {
        for (const source of sources) metadata.set(`u_${source.lineNo}`, { kind: "utt", text: source.utterance });
        return { id: `candidate_${nodeId}`, shape: "amo-node", data: {
          type: sources[0].type,
          label: sources[0].utterance,
          parent_id: "n_topic",
          metadata_refs: sources.map((source) => `u_${source.lineNo}`),
        } };
      }),
    ];
    const allHints = findPossibleDuplicates(cells, 0.78, metadata, 10_000);
    const hints = findPossibleDuplicates(cells, 0.78, metadata);
    const hintPairs = new Set(hints.map((hint) => cellKey(hint.leftId, hint.rightId)));
    const duplicateGold = rows.filter((row) => row.relation_anchor.startsWith("duplicate:") && row.relation_target_line_no);
    const scored = duplicateGold.map((row) => {
      const sourceId = (predictionByLine.get(Number(row.line_no)) as any)?.node_id;
      const targetId = (predictionByLine.get(Number(row.relation_target_line_no)) as any)?.node_id;
      return { sourceId, targetId, alreadySameNode: Boolean(sourceId && sourceId === targetId),
        rank: sourceId && targetId ? allHints.findIndex((hint) =>
          cellKey(hint.leftId, hint.rightId) === cellKey(`candidate_${sourceId}`, `candidate_${targetId}`)) + 1 : null,
        hinted: Boolean(sourceId && targetId && hintPairs.has(cellKey(`candidate_${sourceId}`, `candidate_${targetId}`))) };
    });
    const candidatesFound = scored.filter((item) => item.alreadySameNode || item.hinted).length;

    console.info("[real-prediction-duplicate-replay]", JSON.stringify({
      uniquePredictedNodes: nodeRows.size,
      preliminaryGoldPairs: duplicateGold.length,
      sameNodePairs: scored.filter((item) => item.alreadySameNode).length,
      shownHints: hints.length,
      goldPairsSameNodeOrHinted: candidatesFound,
      pairResults: scored,
      topHints: hints.map((hint, index) => ({ rank: index + 1, signal: hint.signal,
        score: hint.score, left: hint.leftLabel.slice(0, 28), right: hint.rightLabel.slice(0, 28),
        terms: hint.matchingTerms })),
    }));
    expect(duplicateGold).toHaveLength(3);
    expect(hints.length).toBeLessThanOrEqual(20);
  });
});
