import tempfile
import unittest
import io
from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, DecodedStreamObject, NameObject

from app.preparation import PreparationStore, extract_material


class PreparationTests(unittest.TestCase):
    def test_text_material_suggestions_keep_source_and_require_human_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PreparationStore(Path(directory) / "prep.sqlite3")
            material = store.add_material("mtg_one", "plan.md", b"# Plan\nShould we pilot?\nChoose vendor A or B?\n")
            self.assertEqual(material["filename"], "plan.md")
            suggestions = store.suggest("mtg_one", ["Launch scope"])
            self.assertIn(("agenda", "agenda:1", "待决：Launch scope"),
                          [(item["source_kind"], item["source_ref"], item["text"]) for item in suggestions])
            self.assertTrue(any(item["source_ref"] == f"{material['id']}:line:2"
                                and "pilot" in item["text"] for item in suggestions))
            self.assertEqual(store.list_questions("mtg_one"), [])
            candidate = next(item for item in suggestions if "pilot" in item["text"])
            created = store.add_question("mtg_one", "q-event-1", candidate["text"],
                                         candidate["source_kind"], candidate["source_ref"])
            self.assertEqual(store.add_question("mtg_one", "q-event-1", candidate["text"],
                                                candidate["source_kind"], candidate["source_ref"])["id"], created["id"])
            self.assertEqual(created["status"], "pending")
            self.assertEqual(store.source_excerpt("mtg_one", candidate["source_ref"]),
                             "Should we pilot?")
            with self.assertRaises(ValueError):
                store.add_question("mtg_one", "fake-source", "Fake?", "material",
                                   f"{material['id']}:line:999")
            resolved = store.update_question("mtg_one", created["id"], 1, "resolved")
            self.assertEqual(resolved["status"], "resolved")
            self.assertEqual(resolved["version"], 2)

    def test_rejects_scanned_pdf_and_unsupported_materials(self):
        with self.assertRaises(ValueError):
            extract_material("bad.bin", b"binary")
        with self.assertRaises(ValueError):
            extract_material("scan.pdf", b"not a pdf")

    def test_extracts_text_pdf_with_page_locator(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        font = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }))
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 30 220 Td (Choose vendor A?) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(content)
        target = io.BytesIO()
        writer.write(target)
        segments = extract_material("plan.pdf", target.getvalue())
        self.assertEqual(segments, [{"locator": "page:1:line:1", "text": "Choose vendor A?"}])


if __name__ == "__main__":
    unittest.main()
