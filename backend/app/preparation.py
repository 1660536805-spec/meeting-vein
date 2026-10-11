"""Local meeting preparation: source-aware text materials and human question cards."""
from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_EXTRACTED_CHARS = 100_000
QUESTION_CUE = re.compile(r"[?？]|是否|如何|哪个|哪种|选用|选择|决定|确定")


def extract_material(filename: str, raw: bytes) -> list[dict]:
    """Extract bounded, citable text fragments; scanned PDFs need OCR elsewhere."""
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("material must be between 1 byte and 5 MB")
    suffix = Path(filename).suffix.lower()
    segments: list[dict] = []
    if suffix in {".txt", ".md"}:
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("text material must be UTF-8") from exc
        segments = [{"locator": f"line:{i}", "text": line.strip()}
                    for i, line in enumerate(content.splitlines(), 1) if line.strip()]
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw), strict=False)
            if len(reader.pages) > 40:
                raise ValueError("PDF has more than 40 pages")
            for page_no, page in enumerate(reader.pages, 1):
                for line_no, line in enumerate((page.extract_text() or "").splitlines(), 1):
                    if line.strip():
                        segments.append({"locator": f"page:{page_no}:line:{line_no}",
                                         "text": line.strip()})
        except ValueError:
            raise
        except Exception as exc:  # malformed or encrypted documents
            raise ValueError("could not extract PDF text") from exc
    else:
        raise ValueError("supported materials: TXT, Markdown, text PDF")
    if not segments:
        raise ValueError("material has no selectable text; scanned PDFs need OCR")
    if sum(len(segment["text"]) for segment in segments) > MAX_EXTRACTED_CHARS:
        raise ValueError("material text exceeds 100000 characters")
    return segments


class PreparationConflict(RuntimeError):
    pass


class PreparationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS materials (
                id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, filename TEXT NOT NULL,
                content_sha256 TEXT NOT NULL, segments_json TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(meeting_id, content_sha256)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS prep_questions (
                id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, event_id TEXT NOT NULL,
                text TEXT NOT NULL, source_kind TEXT NOT NULL, source_ref TEXT,
                status TEXT NOT NULL DEFAULT 'pending', version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(meeting_id, event_id)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS prep_questions_meeting ON prep_questions(meeting_id)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _material_record(row: sqlite3.Row) -> dict:
        return {key: row[key] for key in ("id", "meeting_id", "filename", "content_sha256", "created_at")}

    def add_material(self, meeting_id: str, filename: str, raw: bytes) -> dict:
        filename = Path(filename).name[:120]
        if not filename:
            raise ValueError("filename is required")
        segments = extract_material(filename, raw)
        digest = hashlib.sha256(raw).hexdigest()
        with self._connect() as conn:
            prior = conn.execute("SELECT * FROM materials WHERE meeting_id=? AND content_sha256=?",
                                 (meeting_id, digest)).fetchone()
            if prior:
                return self._material_record(prior)
            material_id = f"mat_{uuid.uuid4().hex[:16]}"
            conn.execute("INSERT INTO materials VALUES (?,?,?,?,?,?)",
                         (material_id, meeting_id, filename, digest,
                          json.dumps(segments, ensure_ascii=False), self._now()))
            return self._material_record(conn.execute("SELECT * FROM materials WHERE id=?",
                                                      (material_id,)).fetchone())

    def list_materials(self, meeting_id: str) -> list[dict]:
        with self._connect() as conn:
            return [self._material_record(row) for row in conn.execute(
                "SELECT * FROM materials WHERE meeting_id=? ORDER BY created_at,id", (meeting_id,))]

    def export_materials(self, meeting_id: str) -> list[dict]:
        """Keep cited source text with a portable local snapshot."""
        with self._connect() as conn:
            return [{**self._material_record(row), "segments": json.loads(row["segments_json"])}
                    for row in conn.execute(
                        "SELECT * FROM materials WHERE meeting_id=? ORDER BY created_at,id", (meeting_id,))]

    def source_excerpt(self, meeting_id: str, source_ref: str) -> str:
        material_id, separator, locator = source_ref.partition(":")
        if not separator or not locator:
            raise ValueError("invalid material source")
        with self._connect() as conn:
            row = conn.execute("SELECT segments_json FROM materials WHERE meeting_id=? AND id=?",
                               (meeting_id, material_id)).fetchone()
        if row is None:
            raise ValueError("material source not found")
        for segment in json.loads(row["segments_json"]):
            if segment["locator"] == locator:
                return segment["text"]
        raise ValueError("material source locator not found")

    def suggest(self, meeting_id: str, agenda: list[str]) -> list[dict]:
        suggestions = [{"text": f"待决：{topic[:190]}", "source_kind": "agenda",
                        "source_ref": f"agenda:{index}", "source_label": f"议程第 {index} 项"}
                       for index, topic in enumerate(agenda, 1) if topic.strip()]
        with self._connect() as conn:
            for row in conn.execute("SELECT * FROM materials WHERE meeting_id=? ORDER BY created_at,id",
                                    (meeting_id,)):
                for segment in json.loads(row["segments_json"]):
                    text = re.sub(r"^\s*[-*#>]+\s*", "", segment["text"]).strip()
                    if len(text) < 6 or not QUESTION_CUE.search(text):
                        continue
                    suggestions.append({"text": text[:200], "source_kind": "material",
                                        "source_ref": f"{row['id']}:{segment['locator']}",
                                        "source_label": f"{row['filename']} · {segment['locator']}"})
                    if len(suggestions) >= 50:
                        return suggestions
        return suggestions

    def add_question(self, meeting_id: str, event_id: str, text: str,
                     source_kind: str, source_ref: str | None = None) -> dict:
        text = text.strip()
        if not text or len(text) > 200 or not event_id or len(event_id) > 120:
            raise ValueError("invalid question or event_id")
        if source_kind not in {"agenda", "material", "manual"}:
            raise ValueError("invalid question source")
        if source_kind == "material":
            self.source_excerpt(meeting_id, source_ref or "")
        now = self._now()
        with self._connect() as conn:
            prior = conn.execute("SELECT * FROM prep_questions WHERE meeting_id=? AND event_id=?",
                                 (meeting_id, event_id)).fetchone()
            if prior:
                if prior["text"] != text or prior["source_kind"] != source_kind or prior["source_ref"] != source_ref:
                    raise PreparationConflict("event_id already used for another question")
                return dict(prior)
            question_id = f"q_{uuid.uuid4().hex[:16]}"
            conn.execute("INSERT INTO prep_questions VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (question_id, meeting_id, event_id, text, source_kind, source_ref,
                          "pending", 1, now, now))
            return dict(conn.execute("SELECT * FROM prep_questions WHERE id=?", (question_id,)).fetchone())

    def list_questions(self, meeting_id: str) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM prep_questions WHERE meeting_id=? ORDER BY created_at,id", (meeting_id,))]

    def update_question(self, meeting_id: str, question_id: str,
                        expected_version: int, status: str) -> dict:
        if status not in {"pending", "resolved", "dismissed"}:
            raise ValueError("invalid question status")
        with self._connect() as conn:
            changed = conn.execute("UPDATE prep_questions SET status=?,version=version+1,updated_at=? "
                                   "WHERE meeting_id=? AND id=? AND version=?",
                                   (status, self._now(), meeting_id, question_id, expected_version))
            if changed.rowcount != 1:
                found = conn.execute("SELECT 1 FROM prep_questions WHERE meeting_id=? AND id=?",
                                     (meeting_id, question_id)).fetchone()
                if not found:
                    raise KeyError("question not found")
                raise PreparationConflict("question version changed")
            return dict(conn.execute("SELECT * FROM prep_questions WHERE id=?", (question_id,)).fetchone())
