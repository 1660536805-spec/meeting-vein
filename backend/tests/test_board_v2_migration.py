"""Pure compatibility conversion tests for the amo.board/v1 → v2 boundary."""

import copy
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from app.models import GraphOp
from app.storage import (StoreA, _is_descendant, _repair_orphans,
                         migrate_board_v1_to_v2, relayout, validate_parent_assignment)


def node(node_id: str, *, kind: str = "point", refs: list | None = None) -> dict:
    return {"id": node_id, "shape": "amo-node", "data": {
        "type": kind, "label": node_id, "metadata_refs": refs or []}}


def edge(edge_id: str, source: str, target: str, relation: str) -> dict:
    return {"id": edge_id, "shape": "edge", "source": {"cell": source},
            "target": {"cell": target}, "data": {"relation": relation}}


class BoardV2MigrationTests(unittest.TestCase):
    def test_semantic_edges_do_not_define_structural_ancestry(self):
        index = {"a": node("a"), "b": node("b"),
                 "oppose": edge("oppose", "a", "b", "oppose"),
                 "support": edge("support", "b", "a", "support")}

        self.assertFalse(_is_descendant(index, "b", "a"))
        self.assertFalse(_is_descendant(index, "a", "b"))

    def test_v2_parent_ids_define_structural_ancestry_independent_of_semantic_edges(self):
        a, b, c = node("a", kind="issue"), node("b"), node("c")
        b["data"]["parent_id"] = "a"
        c["data"]["parent_id"] = "b"
        index = {x["id"]: x for x in (a, b, c)}
        index["oppose"] = edge("oppose", "c", "a", "oppose")

        self.assertTrue(_is_descendant(index, "c", "a"))
        self.assertFalse(_is_descendant(index, "a", "c"))

    def test_orphan_repair_does_not_treat_semantic_edges_as_structure(self):
        root = node("n_issue_root", kind="issue")
        orphan = node("orphan")
        index = {root["id"]: root, orphan["id"]: orphan,
                 "support": edge("support", root["id"], orphan["id"], "support")}

        repaired = _repair_orphans(index)

        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0]["node"], "orphan")
        self.assertEqual(repaired[0]["parent"], "n_issue_root")
        self.assertTrue(any(c.get("shape") == "edge" and c.get("data", {}).get("relation") == "subordinate"
                            for c in index.values()))

    def test_orphan_repair_does_not_guess_between_multiple_v1_parents(self):
        root = node("n_issue_root", kind="issue")
        left, right = node("left", kind="issue"), node("right", kind="issue")
        ambiguous = node("ambiguous")
        index = {c["id"]: c for c in (root, left, right, ambiguous)}
        index.update({"left-edge": edge("left-edge", "left", "ambiguous", "subordinate"),
                      "right-edge": edge("right-edge", "right", "ambiguous", "subordinate")})

        repaired = _repair_orphans(index)

        self.assertNotIn("ambiguous", [item["node"] for item in repaired])
        self.assertTrue(index["ambiguous"]["data"]["needs_parent_review"])
        parents = [c["source"]["cell"] for c in index.values()
                   if c.get("shape") == "edge" and c.get("data", {}).get("relation") == "subordinate"
                   and c["target"]["cell"] == "ambiguous"]
        self.assertEqual(parents, ["left", "right"])

    def test_v2_orphan_repair_sets_parent_id_without_adding_semantic_edge(self):
        root = node("n_issue_root", kind="issue")
        orphan = node("orphan")
        root["data"]["parent_id"] = None
        orphan["data"]["parent_id"] = None
        index = {root["id"]: root, orphan["id"]: orphan}

        repaired = _repair_orphans(index)

        self.assertEqual(len(repaired), 1)
        self.assertNotIn("edge", repaired[0])
        self.assertEqual(orphan["data"]["parent_id"], "n_issue_root")
        self.assertEqual(len([c for c in index.values() if c.get("shape") == "edge"]), 0)

    def test_v2_orphan_repair_does_not_override_migration_review_flag(self):
        root = node("n_issue_root", kind="issue")
        review = node("review")
        root["data"]["parent_id"] = None
        review["data"].update(parent_id=None, needs_parent_review=True)
        index = {root["id"]: root, review["id"]: review}

        repaired = _repair_orphans(index)

        self.assertEqual(repaired, [])
        self.assertIsNone(review["data"]["parent_id"])
        self.assertTrue(review["data"]["needs_parent_review"])

    def test_v2_move_changes_parent_id_and_preserves_semantic_edges(self):
        a, b, c = node("a", kind="issue"), node("b"), node("c")
        a["data"]["parent_id"] = None
        b["data"]["parent_id"] = "a"
        c["data"]["parent_id"] = "a"
        relation = edge("debate", "b", "c", "oppose")
        index = {x["id"]: x for x in (a, b, c, relation)}
        with tempfile.TemporaryDirectory() as root:
            store = StoreA(root)
            store._apply_one(index, GraphOp(op="move_node", node="b", parent="c"))

            self.assertEqual(index["b"]["data"]["parent_id"], "c")
            self.assertEqual(index["debate"], relation)
            self.assertEqual([x["id"] for x in index.values() if x.get("shape") == "edge"], ["debate"])

    def test_v2_merge_does_not_write_locked_survivor_or_remove_duplicate(self):
        target, duplicate, child = node("target"), node("duplicate", refs=["utt-duplicate"]), node("child")
        target["data"].update(parent_id=None, metadata_refs=["utt-target"], lock={"locked": True})
        duplicate["data"].update(parent_id="target", metadata_refs=["utt-duplicate"])
        child["data"]["parent_id"] = "duplicate"
        relation = edge("debate", "duplicate", "child", "oppose")
        index = {cell["id"]: cell for cell in (target, duplicate, child, relation)}

        StoreA._apply_one(StoreA.__new__(StoreA), index,
                          GraphOp(op="merge_as_duplicate", node="duplicate", parent="target"))

        self.assertIn("duplicate", index)
        self.assertEqual(index["child"]["data"]["parent_id"], "duplicate")
        self.assertEqual(index["target"]["data"]["metadata_refs"], ["utt-target"])
        self.assertEqual(index["target"]["data"]["lock"], {"locked": True})
        self.assertEqual(index["debate"]["source"]["cell"], "duplicate")
        self.assertEqual(index["debate"]["target"]["cell"], "child")

    def test_v2_add_and_structural_link_write_parent_id_without_tree_edges(self):
        root = node("n_issue_root", kind="issue")
        root["data"]["parent_id"] = None
        child = node("child")
        child["data"]["parent_id"] = None
        index = {root["id"]: root, child["id"]: child}
        store = StoreA.__new__(StoreA)

        store._apply_one(index, GraphOp(op="add_node", node="new"))
        store._apply_one(index, GraphOp(op="link", source="new", target="child", relation="subordinate"))

        self.assertEqual(index["new"]["data"]["parent_id"], "n_issue_root")
        self.assertEqual(index["child"]["data"]["parent_id"], "new")
        self.assertFalse(any(cell.get("shape") == "edge" for cell in index.values()))

    def test_v1_merge_reparents_children_and_preserves_semantic_edges_and_evidence(self):
        root, target = node("root", kind="issue"), node("target", refs=["utt-target"])
        duplicate = node("duplicate", refs=["utt-duplicate"])
        child = node("child")
        index = {cell["id"]: cell for cell in (root, target, duplicate, child,
            edge("rt", "root", "target", "subordinate"),
            edge("td", "target", "duplicate", "subordinate"),
            edge("dc", "duplicate", "child", "subordinate"),
            edge("debate", "duplicate", "root", "oppose"))}

        StoreA._apply_one(StoreA.__new__(StoreA), index,
                          GraphOp(op="merge_as_duplicate", node="duplicate", parent="target"))

        self.assertNotIn("duplicate", index)
        self.assertEqual(index["child"]["id"], "child")
        self.assertEqual(index["dc"]["source"]["cell"], "target")
        self.assertNotIn("td", index)
        self.assertEqual(index["debate"]["source"]["cell"], "target")
        self.assertEqual(index["target"]["data"]["metadata_refs"], ["utt-target", "utt-duplicate"])

    def test_relayout_does_not_use_semantic_edges_to_extend_tree_depth(self):
        cells = [node("root", kind="issue"), node("a"), node("b"), node("c"), node("d"),
                 edge("tree", "root", "a", "subordinate"),
                 edge("oppose1", "a", "b", "oppose"),
                 edge("oppose2", "b", "c", "oppose"),
                 edge("oppose3", "c", "d", "oppose")]

        arranged = relayout(cells)
        y = {c["id"]: c["position"]["y"] for c in arranged if c.get("shape") != "edge"}

        self.assertEqual(y["b"], y["c"])
        self.assertEqual(y["c"], y["d"])

    def test_parent_validator_rejects_missing_targets_root_moves_self_and_cycles(self):
        root, a, b = node("n_issue_root", kind="issue"), node("a"), node("b")
        index = {c["id"]: c for c in (root, a, b)}
        index["ra"] = edge("ra", "n_issue_root", "a", "subordinate")
        index["ab"] = edge("ab", "a", "b", "subordinate")

        cases = (("missing", "a"), ("a", "missing"), ("n_issue_root", "a"),
                 ("a", "a"), ("a", "b"))
        for child, parent in cases:
            with self.subTest(child=child, parent=parent):
                with self.assertRaises(ValueError):
                    validate_parent_assignment(index, child, parent)

        self.assertEqual(validate_parent_assignment(index, "b", "n_issue_root"), "n_issue_root")

    def test_converts_unique_subordinate_parent_and_preserves_semantic_edges_and_refs(self):
        source = {"schema": "amo.board/v1", "graph_id": "mtg_demo", "version": 8,
                  "cells": [node("root", kind="issue"), node("a", refs=["utt-1"]),
                            node("b", refs=["utt-2"]), edge("tree", "root", "a", "subordinate"),
                            edge("opp", "a", "b", "oppose"), edge("dup", "a", "b", "duplicate")]}
        original = copy.deepcopy(source)

        migrated = migrate_board_v1_to_v2(source)

        self.assertEqual(migrated["schema"], "amo.board/v2")
        self.assertEqual(migrated["version"], 8)
        by_id = {cell["id"]: cell for cell in migrated["cells"]}
        self.assertIsNone(by_id["root"]["data"]["parent_id"])
        self.assertEqual(by_id["a"]["data"]["parent_id"], "root")
        self.assertEqual(by_id["a"]["data"]["metadata_refs"], ["utt-1"])
        self.assertEqual({c["id"] for c in migrated["cells"] if c.get("shape") == "edge"}, {"opp", "dup"})
        self.assertEqual(source, original, "conversion must not mutate the source snapshot")

    def test_ambiguous_multiple_parent_is_flagged_without_dropping_semantic_edges(self):
        source = {"schema": "amo.board/v1", "graph_id": "mtg_demo", "version": 1,
                  "cells": [node("root", kind="issue"), node("left", kind="issue"),
                            node("right", kind="issue"), node("x", refs=["utt-x"]),
                            edge("l", "left", "x", "subordinate"),
                            edge("r", "right", "x", "subordinate"),
                            edge("support", "x", "left", "support")]}

        migrated = migrate_board_v1_to_v2(source)

        by_id = {cell["id"]: cell for cell in migrated["cells"]}
        self.assertIsNone(by_id["x"]["data"]["parent_id"])
        self.assertTrue(by_id["x"]["data"]["needs_parent_review"])
        self.assertEqual(by_id["x"]["data"]["metadata_refs"], ["utt-x"])
        self.assertEqual([c["id"] for c in migrated["cells"] if c.get("shape") == "edge"], ["support"])

    def test_cycles_are_flagged_instead_of_becoming_tree_parents(self):
        source = {"schema": "amo.board/v1", "graph_id": "mtg_demo", "version": 1,
                  "cells": [node("root", kind="issue"), node("a"), node("b"),
                            edge("ra", "root", "a", "subordinate"),
                            edge("ab", "a", "b", "subordinate"),
                            edge("ba", "b", "a", "subordinate")]}

        migrated = migrate_board_v1_to_v2(source)

        by_id = {cell["id"]: cell for cell in migrated["cells"]}
        self.assertIsNone(by_id["a"]["data"]["parent_id"])
        self.assertIsNone(by_id["b"]["data"]["parent_id"])
        self.assertTrue(by_id["a"]["data"]["needs_parent_review"])
        self.assertTrue(by_id["b"]["data"]["needs_parent_review"])

    def test_store_preview_reports_changes_without_writing_the_migration(self):
        with tempfile.TemporaryDirectory() as root:
            store = StoreA(root)
            source = {"schema": "amo.board/v1", "graph_id": "mtg_demo", "version": 4,
                      "title": "demo", "updated_at": "before",
                      "cells": [node("root", kind="issue"), node("a", refs=["utt-1"]),
                                node("b"), edge("tree", "root", "a", "subordinate"),
                                edge("opp", "a", "b", "oppose")]}
            with open(store._path("mtg_demo"), "w", encoding="utf-8") as f:
                json.dump(source, f)

            preview = store.migration_preview("mtg_demo")

            self.assertEqual(preview["from_schema"], "amo.board/v1")
            self.assertEqual(preview["to_schema"], "amo.board/v2")
            self.assertEqual(preview["source_version"], 4)
            self.assertEqual(preview["parent_changes"], [{"node_id": "a", "from": None, "to": "root"}])
            self.assertEqual(preview["needs_review"], ["b"])
            self.assertEqual(preview["semantic_edges_preserved"], 1)
            self.assertEqual(preview["metadata_refs_before"], preview["metadata_refs_after"])
            self.assertEqual(store.list_meetings()[0]["schema"], "amo.board/v1")
            with open(store._path("mtg_demo"), encoding="utf-8") as f:
                self.assertEqual(json.load(f), source, "preview must not write or alter source data")

    def test_preview_does_not_flush_dirty_store_cache(self):
        with tempfile.TemporaryDirectory() as root:
            store = StoreA(root)
            store.create_meeting("mtg_demo", "demo")
            with open(store._path("mtg_demo"), "rb") as f:
                original_bytes = f.read()
            store._last_flush["mtg_demo"] = time.monotonic()
            cells = [node("root", kind="issue"), node("draft")]
            with patch("app.storage.SAVE_THROTTLE_MS", 60_000):
                store.save("mtg_demo", cells)
                preview = store.migration_preview("mtg_demo")
            self.assertIn("draft", preview["needs_review"])
            with open(store._path("mtg_demo"), "rb") as f:
                self.assertEqual(f.read(), original_bytes, "preview must leave deferred writes deferred")

    def test_accept_migration_backs_up_source_and_rollback_restores_it(self):
        with tempfile.TemporaryDirectory() as root:
            store = StoreA(root)
            source = {"schema": "amo.board/v1", "graph_id": "mtg_demo", "version": 4,
                      "title": "demo", "updated_at": "before",
                      "cells": [node("root", kind="issue"), node("a", refs=["utt-1"]),
                                edge("tree", "root", "a", "subordinate"),
                                edge("support", "root", "a", "support")]}
            path = store._path("mtg_demo")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(source, f, ensure_ascii=False, indent=2)
            with open(path, "rb") as f:
                original_bytes = f.read()

            accepted = store.accept_v1_migration("mtg_demo", expected_version=4)

            with open(path, encoding="utf-8") as f:
                migrated = json.load(f)
            self.assertEqual(migrated["schema"], "amo.board/v2")
            self.assertTrue(os.path.exists(accepted["backup_path"]))
            with open(accepted["backup_path"], "rb") as f:
                self.assertEqual(f.read(), original_bytes)
            self.assertEqual(store._schemas["mtg_demo"], "amo.board/v2")

            restored = store.rollback_v1_migration("mtg_demo", expected_version=4)

            self.assertEqual(restored["schema"], "amo.board/v1")
            with open(path, "rb") as f:
                self.assertEqual(f.read(), original_bytes)
            self.assertEqual(store.load("mtg_demo"), source["cells"])

    def test_history_and_share_snapshot_keep_schema_for_v1_and_v2_documents(self):
        with tempfile.TemporaryDirectory() as root:
            store = StoreA(root)
            source = {"schema": "amo.board/v1", "graph_id": "mtg_demo", "version": 3,
                      "title": "demo", "updated_at": "before",
                      "cells": [node("root", kind="issue"), node("a"),
                                edge("tree", "root", "a", "subordinate")]}
            with open(store._path("mtg_demo"), "w", encoding="utf-8") as f:
                json.dump(source, f)

            store.accept_v1_migration("mtg_demo", expected_version=3)
            shared = store.create_share_snapshot("mtg_demo")
            snapshot = store.load_share_snapshot(shared["token"])
            history = store.history("mtg_demo")

            self.assertEqual(history[0]["schema"], "amo.board/v1")
            self.assertEqual(history[-1]["schema"], "amo.board/v2")
            self.assertEqual(snapshot["schema"], "amo.board/v2")
            self.assertEqual(snapshot["cells"], store.load("mtg_demo"))

    def test_accept_migration_rejects_stale_version_without_creating_backup(self):
        with tempfile.TemporaryDirectory() as root:
            store = StoreA(root)
            store.create_meeting("mtg_demo", "demo")

            with self.assertRaisesRegex(RuntimeError, "version conflict"):
                store.accept_v1_migration("mtg_demo", expected_version=0)

            self.assertFalse(os.path.exists(store._migration_backup_path("mtg_demo")))
            self.assertEqual(store.migration_preview("mtg_demo")["from_schema"], "amo.board/v1")


if __name__ == "__main__":
    unittest.main()
