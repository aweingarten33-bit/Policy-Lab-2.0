"""Verification's authority lookups, served by OpenContracts instead of Chroma.

This is the other half of the seam in ``authority_source.py``. It resolves a
citation the way OpenContracts resolves one -- canonical key, then their own
``candidate_keys`` roll-up -- and returns the authority in the shape
verification already consumes, so every Policy Lab rule downstream runs
unmodified against it.

What is borrowed and what is not
--------------------------------
Borrowed from OpenContracts, by calling their code rather than copying it:

  * ``enrichment.authorities.candidate_keys`` -- key resolution order,
    including the subsection-to-section roll-up;
  * ``enrichment.authority_sources.AuthoritySourceRecord`` -- the authority
    record and its ``as_document_metadata()`` projection, which is what this
    module reads;
  * ``pipeline.authority_source_providers.cfr_provider`` -- eCFR fetching and
    section extraction, used to build the corpus.

Not borrowed, and deliberately: the legal-effect decision. OpenContracts says
what the publisher said; ``derive_source_status`` says whether a present duty
may rest on it. See the status-boundary note in ``authority_source.py``.

Talking to OpenContracts
------------------------
``client`` is anything that can answer "give me the authority document filed
under this canonical key". Against a running OpenContracts that is a GraphQL or
REST call; in the compatibility spike it is an in-process store holding real
``AuthoritySourceRecord`` objects. The provider does not care which, and that
is the only reason a spike can prove anything about the real thing.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Protocol

from app.services.retrieval.authority_source import (
    canonical_key_for,
    derive_category,
    derive_source_status,
)
from app.services.retrieval.models import (
    Jurisdiction,
    RetrievalContext,
    RetrievalResult,
    SourceChunk,
    SourceMetadata,
    SourceType,
)

logger = logging.getLogger(__name__)


class AuthorityDocumentClient(Protocol):
    """One call: fetch the authority document stored under a canonical key."""

    def get_authority_document(self, canonical_key: str) -> Optional[dict]:
        """``{"text": str, "metadata": dict}`` as OpenContracts holds it."""


def _oc_candidate_keys(canonical_key: str) -> Optional[list[str]]:
    """OpenContracts' own key-resolution order, or None if they are unavailable.

    Never reimplemented on failure. A private copy of their resolver would
    drift from theirs and silently resolve citations differently, which is the
    class of bug this whole integration exists to remove. Without them there is
    no resolution, and no resolution means unverified.
    """
    from app.services.retrieval import opencontracts_runtime as ocr

    if not ocr.available():
        return None
    try:
        return ocr.candidate_keys(canonical_key)
    except Exception as e:  # noqa: BLE001
        logger.error("OpenContracts candidate_keys failed for %r: %s", canonical_key, e)
        return None


class OpenContractsAuthorityProvider:
    """Serves verification's two authority lookups from an OpenContracts corpus.

    ``matcher`` is the verifier, exactly as for ``ChromaAuthorityProvider`` --
    citation matching and subsection scope resolution are Policy Lab rules and
    are called back into, so both substrates are held to the same scope test.
    """

    # Resolution is by canonical key, so an empty retrieval context is not
    # evidence that there is nothing to verify against. Retrieval finds
    # candidate passages; it no longer owns the authority.
    resolves_without_retrieval_context = True

    def __init__(self, matcher, client: AuthorityDocumentClient):
        self._matcher = matcher
        self._client = client

    # ── the seam ──

    def find_authority(
        self, citation: str, retrieval_context: RetrievalContext
    ) -> Optional[RetrievalResult]:
        doc = self._resolve(citation)
        if doc is None:
            return None

        meta_dict = doc.get("metadata") or {}
        text = doc.get("text") or ""
        if not text:
            # An authority record with no text cannot support anything. Better
            # to report nothing found than to hand back an empty excerpt that
            # every downstream check would have to special-case.
            return None

        metadata = self._source_metadata(meta_dict, citation, text)

        # The claimed subsection still has to actually be in the text. This is
        # the same gate the Chroma provider applies, called on the same object,
        # so a citation to a paragraph that does not exist fails here whichever
        # substrate is behind it.
        if not self._matcher._source_scope_text(
            citation, metadata.citation or "", text, allow_full_text=True
        ):
            return None

        return RetrievalResult(
            chunk=SourceChunk(
                id=f"oc:{meta_dict.get('canonical_key') or citation}",
                text=text,
                metadata=metadata,
            ),
            # OpenContracts resolved this by canonical key, not by embedding
            # distance. There is no similarity score to report and inventing
            # one would be a lie about how the match was made.
            score=1.0,
            query=citation,
        )

    def full_text(self, citation: str) -> str:
        doc = self._resolve(citation)
        return (doc or {}).get("text") or ""

    # ── internals ──

    def _resolve(self, citation: str) -> Optional[dict]:
        if not citation:
            return None
        key = canonical_key_for(citation)
        keys = _oc_candidate_keys(key)
        if keys is None:
            return None
        for candidate in keys:
            doc = self._client.get_authority_document(candidate)
            if doc:
                return doc
        return None

    def _source_metadata(self, meta: dict, citation: str, text: str) -> SourceMetadata:
        """Project an OpenContracts authority document onto Policy Lab metadata.

        Every date lands in the field that answers the question it actually
        answers -- ``effective_from`` is when the provision took effect,
        ``retrieved_at`` is when the text was fetched, and neither is allowed
        to stand in for the other. That separation is a Phase 0 guarantee and
        it survives the substrate change intact.
        """
        stored_citation = str(meta.get("citation") or "").strip() or self._citation_from_key(
            str(meta.get("canonical_key") or "")
        ) or citation
        retrieved_at = str(meta.get("retrieved_at") or "")[:10] or None

        return SourceMetadata(
            source_name=str(meta.get("title") or meta.get("source_identifier") or stored_citation),
            source_type=SourceType.retrieved_source,
            category=derive_category(meta),
            jurisdiction=Jurisdiction.federal,
            citation=stored_citation,
            part_citation=self._part_citation(stored_citation),
            url=meta.get("source_url"),
            authority=meta.get("publisher"),
            effective_date=meta.get("effective_from"),
            publication_date=meta.get("published_date") or meta.get("issued_date"),
            retrieved_date=retrieved_at,
            last_verified_date=retrieved_at,
            source_status=derive_source_status(meta),
            chunk_index=0,
            total_chunks=1,
            collection="opencontracts_authorities",
        )

    @staticmethod
    def _citation_from_key(canonical_key: str) -> str:
        """"cfr-45:164.404" -> "45 CFR 164.404", matching OpenContracts' own
        citation rendering in ``cfr_provider._locate_impl``."""
        match = re.fullmatch(r"cfr-(\d+):(.+)", canonical_key or "", re.IGNORECASE)
        if match:
            return f"{match.group(1)} CFR {match.group(2)}"
        usc = re.fullmatch(r"usc-(\d+):(.+)", canonical_key or "", re.IGNORECASE)
        if usc:
            return f"{usc.group(1)} U.S.C. {usc.group(2)}"
        return ""

    @staticmethod
    def _part_citation(citation: str) -> Optional[str]:
        """The part-level citation retrieval scopes an industry by."""
        match = re.search(r"\b(\d+)\s*CFR\s*(?:§+\s*)?(\d+)\.", citation or "", re.IGNORECASE)
        if match:
            return f"{match.group(1)} CFR Part {match.group(2)}"
        return None
