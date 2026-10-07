"""Evaluation helpers for the human-labeled meeting candidate sample."""
from __future__ import annotations

from itertools import combinations


def confusion_matrix(expected, predicted) -> dict:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted must have the same number of rows")
    pairs = list(zip(expected, predicted))
    labels = sorted(set(expected) | set(predicted))
    matrix = {label: {guess: 0 for guess in labels} for label in labels}
    for gold, guess in pairs:
        matrix[gold][guess] += 1
    correct = sum(gold == guess for gold, guess in pairs)
    per_label = {}
    for label in labels:
        tp = matrix[label][label]
        support = sum(matrix[label].values())
        predicted_count = sum(matrix[gold][label] for gold in labels)
        precision = tp / predicted_count if predicted_count else 0.0
        recall = tp / support if support else 0.0
        per_label[label] = {"support": support, "precision": precision, "recall": recall,
                            "f1": (2 * precision * recall / (precision + recall)
                                   if precision + recall else 0.0)}
    return {"labels": labels, "matrix": matrix, "count": len(pairs),
            "correct": correct, "accuracy": correct / len(pairs) if pairs else None,
            "per_label": per_label}


def duplicate_pair_metrics(gold_clusters: dict[int, str], predicted_clusters: dict[int, str]) -> dict:
    """Pairwise cluster precision/recall; singleton items create no positive pair."""
    ids = sorted(gold_clusters)
    gold_pairs = {frozenset((left, right)) for left, right in combinations(ids, 2)
                  if gold_clusters[left] == gold_clusters[right]}
    predicted_pairs = {frozenset((left, right)) for left, right in combinations(ids, 2)
                       if predicted_clusters.get(left) is not None
                       and predicted_clusters.get(left) == predicted_clusters.get(right)}
    tp = len(gold_pairs & predicted_pairs)
    fp = len(predicted_pairs - gold_pairs)
    fn = len(gold_pairs - predicted_pairs)
    precision = tp / (tp + fp) if tp + fp else (1.0 if not gold_pairs else 0.0)
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if recall is not None and precision + recall else 0.0
    return {"gold_pairs": len(gold_pairs), "predicted_pairs": len(predicted_pairs),
            "true_positive_pairs": tp, "false_positive_pairs": fp,
            "false_negative_pairs": fn, "precision": precision, "recall": recall, "f1": f1}


def binary_confusion(expected, predicted) -> dict:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted must have the same number of rows")
    labels = ("no", "yes")
    matrix = {label: {guess: 0 for guess in labels} for label in labels}
    for gold, guess in zip(expected, predicted):
        matrix["yes" if gold else "no"]["yes" if guess else "no"] += 1
    return {"labels": list(labels), "matrix": matrix, "count": len(expected)}


def evaluation_report(rows, predictions, mode: str) -> dict:
    """Compare a prediction map keyed by source line number with human labels."""
    normalized = {int(key): value for key, value in predictions.items()}
    typed = [r for r in rows if (r.get("gold_type") or "").strip()]
    expected_types = [r["gold_type"].strip() for r in typed]
    predicted_types = [(normalized.get(int(r["line_no"]), {}).get("type") or "__missing__")
                       for r in typed]

    relation_rows = [r for r in rows if (r.get("relation_anchor") or "").strip()]
    expected_relations = [r["relation_anchor"].split(":", 1)[0] for r in relation_rows]
    predicted_relations = [(normalized.get(int(r["line_no"]), {}).get("relation") or "__missing__")
                           for r in relation_rows]
    expected_conflicts = [r["relation_anchor"].startswith("oppose:") for r in relation_rows]
    predicted_conflicts = [relation == "oppose" for relation in predicted_relations]

    gold_clusters: dict[int, str] = {}
    for row in rows:
        line = int(row["line_no"])
        anchor = row.get("relation_anchor") or ""
        if anchor.startswith("duplicate:"):
            gold_clusters[line] = anchor.split(":", 1)[1]
        else:
            note = row.get("annotation_note") or ""
            marker = "重复簇 "
            if marker in note:
                gold_clusters[line] = note.split(marker, 1)[1].split()[0].strip("，,。；;")
    duplicate_predictions = {
        line: prediction.get("node_id")
        for line, prediction in normalized.items()
        if prediction.get("node_id") is not None
    }
    source_rows = [r for r in rows if (r.get("gold_type") or "").strip()]
    expected_evidence, predicted_evidence = [], []
    for row in source_rows:
        line = int(row["line_no"])
        evidence = normalized.get(line, {}).get("evidence_line_no")
        expected_evidence.append(True)
        predicted_evidence.append(evidence == line)

    owner_rows = [r for r in relation_rows]
    owner_expected, owner_predicted = [], []
    for row in owner_rows:
        line = int(row["line_no"])
        gold_target = row["relation_anchor"].split(":", 1)[1]
        guess = normalized.get(line, {}).get("ownership_anchor")
        owner_expected.append(bool(gold_target))
        owner_predicted.append(bool(guess and guess == gold_target))

    return {
        "mode": mode,
        "sample_rows": len(rows),
        "human_labeled_types": len(typed),
        "type_confusion": confusion_matrix(expected_types, predicted_types),
        "relation_labeled_rows": len(relation_rows),
        "relation_confusion": confusion_matrix(expected_relations, predicted_relations),
        "conflict_confusion": binary_confusion(expected_conflicts, predicted_conflicts),
        "assignment": {
            "evidence_link": binary_confusion(expected_evidence, predicted_evidence),
            "gold_anchor_rows": len(owner_rows),
            "correct_anchor_assignments": sum(owner_expected[i] and owner_predicted[i]
                                               for i in range(len(owner_rows))),
            "anchor_accuracy": (sum(owner_expected[i] and owner_predicted[i]
                                     for i in range(len(owner_rows))) / len(owner_rows)
                                if owner_rows else None),
        },
        "deduplication": duplicate_pair_metrics(gold_clusters, duplicate_predictions),
        "limitations": ["真实 LLM 结果须由独立 predictions 文件提供；本工具不会发送会议原文到外部服务。"],
    }
