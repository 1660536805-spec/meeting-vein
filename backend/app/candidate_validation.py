"""Pure validation for untrusted analyzer and graph-sync candidates."""
from __future__ import annotations

import re


INSIGHT_TYPES = {"issue", "point", "evidence", "conclusion", "action",
                 "dispute", "question", "command", "other", "conflict"}
NODE_TYPES = {"issue", "point", "evidence", "conclusion", "action", "conflict"}
RELATIONS = {"subordinate", "support", "oppose", "duplicate", "replace", "derive"}
OPERATIONS = {"add_node", "link", "merge_as_duplicate", "replace", "set_importance",
              "mark_node", "move_node", "relayout"}
NEW_ID = re.compile(r"n_(?:issue|point|evidence|conclusion|action|conflict)_[A-Za-z0-9_-]{1,80}$")
# Historical MockLLM ids are stable hashes and predate typed ids.
MOCK_ID = re.compile(r"n_p_[0-9]{1,8}$")
EDGE_ID = re.compile(r"e_[A-Za-z0-9_-]{1,95}$")
IMPORTANCE_REASONS = {"adopted", "affects_action", "repeated", "evidence", "unspecified"}


def _value(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def validate_insights(insights, allowed_metadata_refs) -> list[str]:
    """Validate analysis candidates and ensure evidence only points at this input batch."""
    errors = []
    allowed = set(allowed_metadata_refs or [])
    parent_indexes = {}
    for i, insight in enumerate(insights or []):
        kind = _value(insight, "type")
        summary = _value(insight, "summary")
        confidence = _value(insight, "confidence", 0.5)
        evidence = _value(insight, "evidence", []) or []
        if not isinstance(kind, str) or kind not in INSIGHT_TYPES:
            errors.append(f"insight[{i}] invalid type")
        if not isinstance(summary, str) or not summary.strip():
            errors.append(f"insight[{i}] empty summary")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            errors.append(f"insight[{i}] invalid confidence")
        if not isinstance(evidence, (list, tuple)) or any(
                not isinstance(ref, str) or ref not in allowed for ref in evidence):
            errors.append(f"insight[{i}] untrusted metadata reference")
        related_index = _value(insight, "related_to_index")
        if related_index is None:
            related_index = _value(insight, "ownership_index")
        if related_index is not None and (not isinstance(related_index, int) or isinstance(related_index, bool)
                                          or related_index < 0 or related_index >= len(insights)
                                          or related_index == i):
            errors.append(f"insight[{i}] invalid related insight index")
        relation = _value(insight, "relation_to_related")
        if relation is not None and (not isinstance(relation, str) or relation not in RELATIONS):
            errors.append(f"insight[{i}] invalid relation suggestion")
        parent_index = _value(insight, "parent_index")
        if parent_index is not None:
            if (not isinstance(parent_index, int) or isinstance(parent_index, bool)
                    or parent_index < 0 or parent_index >= len(insights) or parent_index == i):
                errors.append(f"insight[{i}] invalid parent insight index")
            elif _value(insights[parent_index], "type") not in {"issue", "point"}:
                errors.append(f"insight[{i}] parent must be an issue or point")
            else:
                parent_indexes[i] = parent_index
        rationale = _value(insight, "importance_rationale")
        if rationale is not None and (not isinstance(rationale, str)
                                      or rationale not in IMPORTANCE_REASONS):
            errors.append(f"insight[{i}] invalid importance rationale")
    for start in parent_indexes:
        seen, cursor = set(), start
        while cursor in parent_indexes:
            if cursor in seen:
                errors.append("insight parent indexes contain a cycle")
                break
            seen.add(cursor)
            cursor = parent_indexes[cursor]
    return errors


def validate_graph_update(operations, existing_cells, allowed_metadata_refs,
                          *, allow_mock_ids: bool = False) -> list[str]:
    """Validate references and controlled values before StoreA applies an LLM batch.

    The local symbol table grows in operation order, so a link may target a node
    introduced earlier in the same batch but cannot rely on a dangling future ID.
    """
    errors = []
    allowed_meta = set(allowed_metadata_refs or [])
    cell_by_id = {cell.get("id"): cell for cell in (existing_cells or [])
                  if isinstance(cell, dict) and isinstance(cell.get("id"), str)}
    known = set(cell_by_id)
    known_nodes = {cell_id for cell_id, cell in cell_by_id.items()
                   if cell.get("shape") != "edge"}
    node_types = {cell_id: (cell.get("data") or {}).get("type")
                  for cell_id, cell in cell_by_id.items() if cell_id in known_nodes}
    opposed = set()
    for cell in (existing_cells or []):
        if not isinstance(cell, dict) or cell.get("shape") != "edge":
            continue
        if ((cell.get("data") or {}).get("relation") == "oppose"):
            source = cell.get("source")
            target = cell.get("target")
            source = source.get("cell") if isinstance(source, dict) else source
            target = target.get("cell") if isinstance(target, dict) else target
            opposed.add(frozenset((source, target)))
    for i, op in enumerate(operations or []):
        name = _value(op, "op")
        if not isinstance(name, str) or name not in OPERATIONS:
            errors.append(f"operation[{i}] unknown operation")
            continue
        refs = _value(op, "meta_ids", []) or []
        if not isinstance(refs, (list, tuple)) or any(
                not isinstance(ref, str) or ref not in allowed_meta for ref in refs):
            errors.append(f"operation[{i}] untrusted metadata reference")
        if name == "add_node":
            node_id = _value(op, "node")
            node_type = _value(op, "node_type")
            label = _value(op, "label")
            if node_id != "n_issue_other" and allowed_meta and not refs:
                errors.append(f"operation[{i}] content node missing source reference")
            if not isinstance(node_type, str) or node_type not in NODE_TYPES:
                errors.append(f"operation[{i}] invalid node type")
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 40:
                errors.append(f"operation[{i}] invalid node label")
            if not isinstance(node_id, str):
                errors.append(f"operation[{i}] invalid generated node id")
            elif node_id not in known and not (NEW_ID.fullmatch(node_id)
                                                or (allow_mock_ids and MOCK_ID.fullmatch(node_id))):
                errors.append(f"operation[{i}] invalid generated node id")
            elif node_id in known and node_id not in known_nodes:
                errors.append(f"operation[{i}] add_node id refers to an edge")
            elif node_id in known_nodes and node_types.get(node_id) != node_type:
                errors.append(f"operation[{i}] duplicate node type mismatch")
            elif node_id not in known:
                known.add(node_id)
                known_nodes.add(node_id)
                node_types[node_id] = node_type
            parent = _value(op, "parent")
            if parent and (not isinstance(parent, str) or parent not in known_nodes):
                errors.append(f"operation[{i}] unknown parent node")
        elif name == "link":
            source, target = _value(op, "source"), _value(op, "target")
            if (not isinstance(source, str) or not isinstance(target, str)
                    or source not in known_nodes or target not in known_nodes or source == target):
                errors.append(f"operation[{i}] unknown node endpoint")
            relation = _value(op, "relation")
            if not isinstance(relation, str) or relation not in RELATIONS:
                errors.append(f"operation[{i}] invalid relation")
            edge_id = _value(op, "edge")
            if edge_id is not None:
                if not isinstance(edge_id, str) or not EDGE_ID.fullmatch(edge_id) or edge_id in known:
                    errors.append(f"operation[{i}] invalid edge id")
                else:
                    known.add(edge_id)
            if relation == "oppose" and isinstance(source, str) and isinstance(target, str):
                opposed.add(frozenset((source, target)))
        elif name in {"merge_as_duplicate", "replace", "set_importance", "mark_node", "move_node"}:
            node = _value(op, "node")
            if not isinstance(node, str) or node not in known_nodes:
                errors.append(f"operation[{i}] unknown node")
            elif name == "replace" and (cell_by_id.get(node, {}).get("data") or {}).get("type") == "conclusion":
                errors.append(f"operation[{i}] preserve conclusion; add a new conclusion and replace edge")
            if name in {"merge_as_duplicate", "move_node"}:
                parent = _value(op, "parent")
                if not isinstance(parent, str) or parent not in known_nodes:
                    errors.append(f"operation[{i}] unknown parent node")
                elif (name == "merge_as_duplicate" and isinstance(node, str)
                      and frozenset((node, parent)) in opposed):
                    errors.append(f"operation[{i}] opposing nodes cannot be merged as duplicates")
            importance = _value(op, "importance")
            if name == "set_importance" and (not isinstance(importance, str)
                                              or importance not in {"high", "normal", "low"}):
                errors.append(f"operation[{i}] invalid importance")
            mark = _value(op, "mark")
            if name == "mark_node" and (not isinstance(mark, str) or mark not in {"gray", "strike"}):
                errors.append(f"operation[{i}] invalid mark")
            rationale = _value(op, "importance_rationale")
            if rationale is not None and (not isinstance(rationale, str)
                                          or rationale not in IMPORTANCE_REASONS):
                errors.append(f"operation[{i}] invalid importance rationale")
            if name == "replace":
                label = _value(op, "label")
                if label is not None and (not isinstance(label, str) or not label.strip()
                                          or len(label.strip()) > 40):
                    errors.append(f"operation[{i}] invalid node label")
    return errors


def annotate_graph_operations(operations, insights, board_cells=None) -> None:
    """Attach analyzer-owned confidence/rationale to graph candidates by evidence refs.

    Syncer-supplied confidence is ignored: the server derives it from trusted
    analyzer candidates whose evidence IDs are subsequently allowlisted.
    """
    by_evidence = {}
    for insight in insights or []:
        for ref in (_value(insight, "evidence", []) or []):
            by_evidence.setdefault(ref, []).append(insight)
    rationale_by_node = {}
    for op in operations or []:
        if _value(op, "op") != "add_node":
            continue
        candidates = [candidate for ref in (_value(op, "meta_ids", []) or [])
                      for candidate in by_evidence.get(ref, [])]
        confidence = min((_value(c, "confidence", 0.5) for c in candidates), default=0.5)
        rationale = next((_value(c, "importance_rationale") for c in candidates
                          if _value(c, "importance_rationale")), None)
        op.confidence = confidence
        op.importance_rationale = rationale or "unspecified"
        rationale_by_node[_value(op, "node")] = op.importance_rationale
    for op in operations or []:
        if _value(op, "op") == "set_importance":
            op.importance_rationale = rationale_by_node.get(_value(op, "node"), "unspecified")
