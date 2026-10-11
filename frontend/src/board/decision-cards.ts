import { projectBoard } from "./projection";

export interface DecisionEvidence {
  id: string;
  label: string;
  sourceRefs: string[];
}

export interface DecisionCard {
  id: string;
  label: string;
  status: "pending" | "confirmed" | "dismissed";
  sourceRefs: string[];
  supports: DecisionEvidence[];
  objections: DecisionEvidence[];
  missingEvidence: boolean;
}

const refs = (data: Record<string, any>): string[] =>
  Array.isArray(data.metadata_refs)
    ? data.metadata_refs.filter((value: unknown): value is string => typeof value === "string") : [];

export function projectDecisionCards(cells: any[]): DecisionCard[] {
  const board = projectBoard(cells);
  return board.conclusions.map((decision) => {
    const supports: DecisionEvidence[] = [];
    const objections: DecisionEvidence[] = [];
    for (const edge of board.semanticEdgesByNode.get(decision.id) ?? []) {
      if (edge.relation !== "support" && edge.relation !== "oppose") continue;
      const otherId = edge.source === decision.id ? edge.target : edge.source;
      const other = board.nodeById.get(otherId);
      if (!other) continue;
      const evidence = { id: other.id, label: String(other.data.label ?? "未命名依据"),
        sourceRefs: refs(other.data) };
      (edge.relation === "support" ? supports : objections).push(evidence);
    }
    const sourceRefs = refs(decision.data);
    const humanStatus = decision.data.manual_override?.status === true;
    const status = humanStatus && decision.data.status === "confirmed" ? "confirmed"
      : humanStatus && decision.data.status === "dismissed" ? "dismissed" : "pending";
    return {
      id: decision.id,
      label: String(decision.data.label ?? "待命名结论"),
      status, sourceRefs, supports, objections,
      missingEvidence: sourceRefs.length === 0 &&
        [...supports, ...objections].every((item) => item.sourceRefs.length === 0),
    };
  });
}
