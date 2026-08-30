"""Local cache of OpenContracts authority documents.

Keyed by OpenContracts canonical key, holding OpenContracts' own
``AuthoritySourceRecord.as_document_metadata()`` projection verbatim alongside
the section text. Nothing is reshaped on the way in or out: Policy Lab does not
own this metadata and does not get to edit it.

This is not a second authority model. A running OpenContracts would hold these
rows in Postgres as Documents; here they are a table, because the integration
is library-level rather than service-level. Either way the record is theirs and
the schema question is answered the same way -- one blob, unmodified.

Why cache at all: verification resolves a citation for every claim in every
analysis, and the alternative is an eCFR round trip per claim. The cached row
also carries the content hash, which is what makes a changed regulation
invalidate Obligation Memory rather than silently reusing a verdict.

Superseded by the real thing. If OpenContracts is ever deployed as a service,
this file is deleted and the client points at their API; nothing above it
changes, because nothing above it knows this exists.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Iterable, Optional

from app.config import settings

logger = logging.getLogger(__name__)

STORE_FILENAME = "opencontracts_authorities.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oc_authorities (
    canonical_key TEXT PRIMARY KEY,
    text          TEXT NOT NULL,
    metadata      TEXT NOT NULL,
    content_hash  TEXT,
    fetched_at    REAL NOT NULL
);
"""


class OpenContractsStore:
    """Read/write cache of authority documents produced by OpenContracts."""

    def __init__(self, path: Optional[str] = None):
        self._path = path or os.path.join(settings.kb_persist_dir, STORE_FILENAME)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        return self._conn

    def get(self, canonical_key: str) -> Optional[dict]:
        """``{"text": ..., "metadata": ...}`` as OpenContracts produced it."""
        if not canonical_key:
            return None
        try:
            with self._lock:
                row = self._connect().execute(
                    "SELECT text, metadata FROM oc_authorities WHERE canonical_key = ?",
                    (canonical_key,),
                ).fetchone()
        except Exception as e:  # noqa: BLE001
            # A broken cache must not verify anything and must not crash an
            # analysis. Report a miss; the client will refetch.
            logger.warning("OpenContracts authority cache read failed for %r: %s", canonical_key, e)
            return None
        if row is None:
            return None
        try:
            return {"text": row["text"], "metadata": json.loads(row["metadata"])}
        except (ValueError, TypeError) as e:
            logger.warning("Malformed cached authority %r: %s", canonical_key, e)
            return None

    def put(self, canonical_key: str, text: str, metadata: dict) -> None:
        if not canonical_key or not text:
            return
        try:
            with self._lock:
                conn = self._connect()
                conn.execute(
                    "INSERT OR REPLACE INTO oc_authorities "
                    "(canonical_key, text, metadata, content_hash, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        canonical_key,
                        text,
                        json.dumps(metadata, default=str),
                        str(metadata.get("content_hash") or ""),
                        time.time(),
                    ),
                )
                conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("OpenContracts authority cache write failed for %r: %s", canonical_key, e)

    def keys(self) -> Iterable[str]:
        try:
            with self._lock:
                return [r["canonical_key"] for r in self._connect().execute(
                    "SELECT canonical_key FROM oc_authorities"
                )]
        except Exception:  # noqa: BLE001
            return []

    def count(self) -> int:
        try:
            with self._lock:
                return int(self._connect().execute(
                    "SELECT COUNT(*) AS n FROM oc_authorities"
                ).fetchone()["n"])
        except Exception:  # noqa: BLE001
            return 0

    def forget(self, canonical_key: str) -> None:
        try:
            with self._lock:
                conn = self._connect()
                conn.execute("DELETE FROM oc_authorities WHERE canonical_key = ?", (canonical_key,))
                conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("OpenContracts authority cache delete failed: %s", e)


_store: Optional[OpenContractsStore] = None
_store_lock = threading.Lock()


def get_opencontracts_store() -> OpenContractsStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = OpenContractsStore()
    return _store


def reset_store_for_tests() -> None:
    global _store
    with _store_lock:
        _store = None
