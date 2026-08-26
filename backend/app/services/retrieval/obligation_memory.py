"""Obligation memory — reuse a verification the engine has already earned.

Every analysis re-derives the same regulations from scratch. Re-run the same
policy twice and the second run asks the model the identical question about the
identical passage of the identical regulation, and pays for the identical
answer. This remembers that answer.

What is remembered, precisely
-----------------------------
One thing only: the semantic entailment verdict — "does this passage support
this claim?" — keyed to the claim, the citation, and a fingerprint of the exact
authoritative text it was checked against.

What is NOT remembered, and this is the whole safety argument: none of the
deterministic checks. build_claim_evidence() runs in full on every analysis,
every time. Citation resolution, subsection scope, concrete-fact matching,
source standing, and the binding-law class are all recomputed against the
CURRENT corpus before this store is ever consulted. The cache can only replace
the model's opinion, never the code's checks.

So a cached entry cannot resurrect a stale conclusion:

  * If the regulation text changed, the scope text changes, its fingerprint
    changes, and the key does not match. Miss.
  * If the source is now proposed, superseded or of unknown standing,
    build_claim_evidence fails it closed before the lookup happens, and
    apply_claim_support would refuse the promotion regardless.
  * If the cited subsection no longer resolves, citation_exists is False and
    the claim never reaches the lookup.
  * If a stated figure no longer appears in the source, specifics_supported is
    False and the claim never reaches the lookup.

Only SUPPORTED verdicts that actually produced a `verified` record are stored.
An unverified candidate is never written, so the store cannot launder a failed
extraction into reusable memory.

Storage is a SQLite file beside the vector store and the section store, for the
same reason those are: it is the simplest thing that already fits, needs no new
infrastructure, and is trivially inspectable.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from app.config import settings
from app.services.retrieval.section_store import citation_key

logger = logging.getLogger(__name__)

MEMORY_DB_FILENAME = "obligation_memory.sqlite3"

# Bumped when the meaning of a stored row changes -- a new deterministic gate,
# a change to what the fingerprint covers, a different claim normalisation.
# Entries from an older generation are ignored rather than migrated: this is a
# cache, and re-earning an entry costs one model call.
MEMORY_GENERATION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS verified_obligations (
    key                TEXT PRIMARY KEY,
    generation         INTEGER NOT NULL,
    claim_fingerprint  TEXT NOT NULL,
    citation_key       TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    citation           TEXT,
    source_name        TEXT,
    source_url         TEXT,
    excerpt            TEXT,
    source_status      TEXT NOT NULL,
    support            TEXT NOT NULL,
    note               TEXT,
    verified_at        TEXT NOT NULL,
    last_used_at       TEXT,
    use_count          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_obligations_citation
    ON verified_obligations(citation_key);
"""

_WHITESPACE = re.compile(r"\s+")


def fingerprint_text(text: str) -> str:
    """A stable content hash, insensitive to whitespace reflowing only."""
    normalized = _WHITESPACE.sub(" ", (text or "")).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def claim_fingerprint(claim: str) -> str:
    """Fingerprint for a claim's text.

    Case and whitespace are normalised because the same finding re-generated is
    frequently identical apart from those. Nothing else is normalised: a claim
    that differs by a word differs in meaning often enough that treating the two
    as the same obligation would be guessing, and guessing is what this system
    exists not to do.
    """
    return fingerprint_text((claim or "").lower())


@dataclass(frozen=True)
class RememberedObligation:
    """A verification this engine already earned, and what it was earned from."""
    support: str
    note: str
    citation: Optional[str]
    source_name: Optional[str]
    source_url: Optional[str]
    excerpt: Optional[str]
    source_status: str
    verified_at: str
    use_count: int


class ObligationMemory:
    """Verified entailment verdicts, keyed to the exact text that produced them."""

    def __init__(self, persist_dir: Optional[str] = None):
        self._persist_dir = persist_dir or settings.kb_persist_dir
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self.hits = 0
        self.misses = 0
        self.writes = 0

    @property
    def path(self) -> str:
        return os.path.join(os.path.abspath(self._persist_dir), MEMORY_DB_FILENAME)

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn
        return conn

    @staticmethod
    def _key(claim_fp: str, cite_key: str, source_fp: str) -> str:
        return hashlib.sha256(
            f"{MEMORY_GENERATION}|{claim_fp}|{cite_key}|{source_fp}".encode("utf-8")
        ).hexdigest()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def recall(self, claim: str, citation: str, source_fingerprint: str) -> Optional[RememberedObligation]:
        """A previously verified verdict for this exact claim/citation/text, if any.

        All three parts of the key must match. A changed regulation produces a
        different source fingerprint and therefore a different key, so a stale
        entry is unreachable rather than merely stale.
        """
        if not settings.obligation_memory_enabled:
            return None
        if not claim or not citation or not source_fingerprint:
            return None

        key = self._key(claim_fingerprint(claim), citation_key(citation), source_fingerprint)
        try:
            with self._lock:
                conn = self._connect()
                row = conn.execute(
                    "SELECT * FROM verified_obligations WHERE key = ? AND generation = ?",
                    (key, MEMORY_GENERATION),
                ).fetchone()
                if row is None:
                    self.misses += 1
                    return None
                conn.execute(
                    "UPDATE verified_obligations SET last_used_at = ?, use_count = use_count + 1 "
                    "WHERE key = ?",
                    (datetime.now(timezone.utc).isoformat(), key),
                )
                conn.commit()
        except sqlite3.Error as e:
            # A cache that cannot be read is a cache miss, never an error. The
            # full pipeline is the fallback and it is always correct.
            logger.warning("Obligation memory read failed for %r: %s", citation, e)
            return None

        self.hits += 1
        return RememberedObligation(
            support=row["support"],
            note=row["note"] or "",
            citation=row["citation"],
            source_name=row["source_name"],
            source_url=row["source_url"],
            excerpt=row["excerpt"],
            source_status=row["source_status"],
            verified_at=row["verified_at"],
            use_count=row["use_count"],
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def remember(self, evidence, claim: str) -> bool:
        """Store a verdict, but only from a fully verified evidence record.

        Returns whether anything was written. Every condition below is a reason
        this claim must not become reusable memory, and they are checked here
        rather than trusted from the caller so the store cannot be misused from
        a future call site.
        """
        from app.models.schemas import ClaimSupport, SourceStatus, VerificationStatus

        if not settings.obligation_memory_enabled:
            return False

        checks = evidence.checks
        source_fp = getattr(checks, "source_fingerprint", None)

        disqualifying = (
            # Only a fully verified record. An unverified, partial, contradicted
            # or cannot-determine outcome is a result, not an obligation.
            evidence.status is not VerificationStatus.verified,
            checks.claim_support is not ClaimSupport.supported,
            # Every deterministic gate must have passed on the way in.
            not checks.citation_exists,
            checks.specifics_supported is False,
            # Standing: only currently-in-force text becomes a current
            # obligation. This repeats what verification already enforced --
            # deliberately, because it is the invariant most worth stating twice.
            not checks.source_status_current,
            checks.source_status is not SourceStatus.current_verified,
            # Provenance must survive, or the entry is not auditable later.
            not evidence.source.excerpt,
            not evidence.citation,
            not source_fp,
            not claim,
        )
        if any(disqualifying):
            return False

        key = self._key(claim_fingerprint(claim), citation_key(evidence.citation), source_fp)
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._lock:
                conn = self._connect()
                conn.execute(
                    "INSERT OR REPLACE INTO verified_obligations ("
                    "key, generation, claim_fingerprint, citation_key, source_fingerprint, "
                    "citation, source_name, source_url, excerpt, source_status, support, note, "
                    "verified_at, last_used_at, use_count) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        key, MEMORY_GENERATION, claim_fingerprint(claim),
                        citation_key(evidence.citation), source_fp,
                        evidence.citation, evidence.source.name, evidence.source.url,
                        evidence.source.excerpt, checks.source_status.value,
                        checks.claim_support.value, evidence.reason or "",
                        now, None, 0,
                    ),
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.warning("Obligation memory write failed for %r: %s", evidence.citation, e)
            return False

        self.writes += 1
        return True

    # ------------------------------------------------------------------
    # Housekeeping / introspection
    # ------------------------------------------------------------------
    def count(self) -> int:
        try:
            with self._lock:
                conn = self._connect()
                return int(
                    conn.execute(
                        "SELECT COUNT(*) FROM verified_obligations WHERE generation = ?",
                        (MEMORY_GENERATION,),
                    ).fetchone()[0]
                )
        except sqlite3.Error:
            return 0

    def forget_citation(self, citation: str) -> int:
        """Drop every entry for a citation. Used when a source is refreshed.

        Not strictly required -- a changed source already produces a different
        fingerprint and so a different key -- but it keeps the store from
        accumulating entries for text that no longer exists.
        """
        key = citation_key(citation)
        if not key:
            return 0
        try:
            with self._lock:
                conn = self._connect()
                cur = conn.execute(
                    "DELETE FROM verified_obligations WHERE citation_key = ?", (key,)
                )
                conn.commit()
                return cur.rowcount or 0
        except sqlite3.Error:
            return 0

    def stats(self) -> Dict[str, int]:
        return {
            "entries": self.count(),
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
        }

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


_memory: Optional[ObligationMemory] = None


def get_obligation_memory() -> ObligationMemory:
    global _memory
    if _memory is None:
        _memory = ObligationMemory()
    return _memory


def reset_obligation_memory() -> None:
    """Drop the cached singleton (used by tests that repoint KB_PERSIST_DIR)."""
    global _memory
    if _memory is not None:
        _memory.close()
    _memory = None
