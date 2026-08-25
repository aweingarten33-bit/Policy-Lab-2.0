"""Authoritative full-text store — the complete text of each cited section.

Retrieval and verification want different things from the same regulation, and
the pipeline was serving both from one artefact.

Retrieval wants small chunks: an 800-character passage embeds into a
meaningful vector, a whole section does not. Verification wants the opposite —
the entire cited scope, because the sentence that decides whether a claim is
true is frequently not the sentence that matched the query. A claim about
§164.404(c) has to be checked against paragraph (c), wherever in the section
that paragraph happens to fall.

Previously the eCFR client truncated every section at 3,000 characters before
anything else saw it, and ingestion then split what survived into 800-character
chunks. So verification asked "does the cited subsection appear here?" of an
800-character window carved out of a section that had already been cut. When
the controlling subsection sat past the cut, the honest answer was no, and the
claim came back unverifiable — not because the regulation failed to say it, but
because the text saying it had been discarded during ingestion.

This store keeps the complete, untruncated section text keyed by its citation,
alongside the vector store rather than inside it. Retrieval keeps its chunks;
verification resolves the full scope from here. It is a plain SQLite file in
the same directory as the Chroma database, so it is baked into the image and
restored to a mounted disk by exactly the same machinery.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from typing import Dict, Iterable, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

SECTION_DB_FILENAME = "authoritative_sections.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS authoritative_sections (
    citation_key      TEXT PRIMARY KEY,
    citation          TEXT NOT NULL,
    part_citation     TEXT,
    source_name       TEXT,
    authority         TEXT,
    url               TEXT,
    full_text         TEXT NOT NULL,
    effective_date    TEXT,
    publication_date  TEXT,
    retrieved_date    TEXT,
    last_verified_date TEXT,
    source_status     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sections_part ON authoritative_sections(part_citation);
"""


def citation_key(citation: str) -> str:
    """A stable lookup key for a citation string.

    Normalises the cosmetic variation between how a regulation is written in a
    finding and how it is stored ("45 CFR § 164.404" / "45 CFR §164.404" /
    "45 cfr section 164.404"). Subsection markers are deliberately dropped: the
    store holds whole sections, and a claim about (c)(1) resolves to the same
    section as a claim about (b).
    """
    if not citation:
        return ""
    c = citation.lower().strip()
    c = re.sub(r"\([A-Za-z0-9]+\)", " ", c)          # drop subsection markers
    # "42 U.S.C." and "42 USC" name the same authority.
    c = re.sub(r"\bu\.\s*s\.\s*c\.?", "usc", c)
    c = re.sub(r"\bc\.\s*f\.\s*r\.?", "cfr", c)
    c = re.sub(r"[§¶.,;:]+", lambda m: "." if "." in m.group(0) else " ", c)
    c = c.replace("section", " ")
    c = re.sub(r"\s+", " ", c).strip()
    return c


class SectionStore:
    """Read/write access to the authoritative full-text sections."""

    def __init__(self, persist_dir: Optional[str] = None):
        self._persist_dir = persist_dir or settings.kb_persist_dir
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def path(self) -> str:
        return os.path.join(os.path.abspath(self._persist_dir), SECTION_DB_FILENAME)

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # check_same_thread=False plus an explicit lock: FastAPI serves requests
        # from a thread pool, and the reads here are short.
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn
        return conn

    def put_many(self, sections: Iterable[Dict[str, Optional[str]]]) -> int:
        """Insert or replace whole sections. Returns how many were written."""
        rows = []
        for s in sections:
            citation = (s.get("citation") or "").strip()
            text = (s.get("full_text") or "").strip()
            if not citation or not text:
                continue
            rows.append((
                citation_key(citation),
                citation,
                s.get("part_citation"),
                s.get("source_name"),
                s.get("authority"),
                s.get("url"),
                text,
                s.get("effective_date"),
                s.get("publication_date"),
                s.get("retrieved_date"),
                s.get("last_verified_date"),
                s.get("source_status"),
            ))
        if not rows:
            return 0

        with self._lock:
            conn = self._connect()
            conn.executemany(
                "INSERT OR REPLACE INTO authoritative_sections ("
                "citation_key, citation, part_citation, source_name, authority, url, "
                "full_text, effective_date, publication_date, retrieved_date, "
                "last_verified_date, source_status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            conn.commit()
        return len(rows)

    def get(self, citation: str) -> Optional[Dict[str, Optional[str]]]:
        """Return the complete stored section for a citation, or None."""
        key = citation_key(citation)
        if not key:
            return None
        try:
            with self._lock:
                conn = self._connect()
                row = conn.execute(
                    "SELECT * FROM authoritative_sections WHERE citation_key = ?", (key,)
                ).fetchone()
        except sqlite3.Error as e:
            # A missing or unreadable store must degrade to "no full text
            # available", never take down verification.
            logger.warning("Authoritative section lookup failed for %r: %s", citation, e)
            return None
        return dict(row) if row is not None else None

    def get_text(self, citation: str) -> Optional[str]:
        row = self.get(citation)
        return row["full_text"] if row else None

    def count(self) -> int:
        try:
            with self._lock:
                conn = self._connect()
                return int(conn.execute("SELECT COUNT(*) FROM authoritative_sections").fetchone()[0])
        except sqlite3.Error:
            return 0

    def citations(self) -> List[str]:
        try:
            with self._lock:
                conn = self._connect()
                return [r[0] for r in conn.execute("SELECT citation FROM authoritative_sections")]
        except sqlite3.Error:
            return []

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


_section_store: Optional[SectionStore] = None


def get_section_store() -> SectionStore:
    global _section_store
    if _section_store is None:
        _section_store = SectionStore()
    return _section_store


def reset_section_store() -> None:
    """Drop the cached singleton (used by tests that repoint KB_PERSIST_DIR)."""
    global _section_store
    if _section_store is not None:
        _section_store.close()
    _section_store = None
