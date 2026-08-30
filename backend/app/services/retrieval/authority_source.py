"""Where verification gets its authority from.

Verification depends on the substrate in exactly two places:

  1. resolve a citation to a source (with its standing and dates);
  2. fetch the complete authoritative text of that cited section.

Everything else it does -- subsection scope resolution, concrete-fact matching,
modality, document class, the single path to ``verified`` -- is arithmetic on
what those two calls return. So they are the whole seam between Policy Lab's
verification rules and whatever stores the law.

This module is that seam. Two implementations sit behind it:
:class:`ChromaAuthorityProvider`, which is today's Chroma + section-store
substrate moved behind the interface unchanged, and (in
``opencontracts_provider.py``) an OpenContracts-backed one. The verifier cannot
tell them apart, which is the point -- it is how the substrate can be replaced
without renegotiating any of the guarantees built on top of it.

Both providers return a :class:`RetrievalResult`. That is deliberate: it is
already the shape verification consumes, so swapping the substrate changes
where an authority is *looked up* and nothing about how it is *judged*.

The status boundary
-------------------
OpenContracts records what a publisher SAID about a document: fifteen lifecycle
states (CURRENT, ENACTED, PROPOSED, WITHDRAWN, SUPERSEDED, EXPIRED, ...) plus
version relationships and effective dates. That is provenance, and it is
theirs to model.

Policy Lab answers a different and narrower question: may this source establish
a present legal obligation? That is five states, and it stays ours. The mapping
from their fifteen to our five is deterministic, lives here, and fails closed --
an unrecognised publisher state becomes STATUS_UNKNOWN, never CURRENT_VERIFIED.

Keeping the two apart matters. Their vocabulary will grow as they add
publishers; ours must not, because every value in it is a legal-effect decision
this product is accountable for.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Protocol

from app.models.schemas import SourceStatus
from app.services.retrieval.models import (
    RetrievalContext,
    RetrievalResult,
    SourceCategory,
    can_support_present_duty,
    resolve_source_status,
)
from app.services.retrieval.section_store import get_section_store

logger = logging.getLogger(__name__)


class AuthorityProvider(Protocol):
    """The two calls verification makes against the substrate."""

    def find_authority(
        self, citation: str, retrieval_context: RetrievalContext
    ) -> Optional[RetrievalResult]:
        """Resolve a citation to a source, or None when nothing matches."""

    def full_text(self, citation: str) -> str:
        """The complete authoritative text of a cited section, or ""."""


class ChromaAuthorityProvider:
    """LEGACY substrate: chunks retrieved from Chroma, sections from SQLite.

    No longer the production path for federal CFR. It remains as the rollback
    while the OpenContracts path proves itself, and for a deployment that has
    deliberately pinned ``AUTHORITY_PROVIDER=chroma``. Everything it does for
    federal regulations is now done by OpenContracts: fetching the section,
    parsing it, modelling the record, resolving the citation.

    The bodies below were verification's own methods and are unchanged.

    ``matcher`` is the verifier itself. Citation matching and subsection scope
    resolution are Policy Lab rules, not substrate behaviour, so they stay
    where they are and are called back into rather than duplicated -- both
    providers therefore apply exactly the same scope test.
    """

    def __init__(self, matcher):
        self._matcher = matcher

    def find_authority(
        self, citation: str, retrieval_context: RetrievalContext
    ) -> Optional[RetrievalResult]:
        if not citation:
            return None

        candidates = []
        for result in retrieval_context.get_all_sources():
            if not self._matcher._is_authoritative_result(result):
                continue
            meta = result.chunk.metadata
            if not meta.citation or not self._matcher._citations_match(citation, meta.citation):
                continue
            scope_ok = bool(self._matcher._source_scope_text(
                citation, meta.citation, result.chunk.text, allow_full_text=True
            ))
            current = can_support_present_duty(resolve_source_status(meta))
            exact = (
                self._matcher._normalize_generic_citation(citation)
                == self._matcher._normalize_generic_citation(meta.citation)
            )
            candidates.append((current, scope_ok, exact, result.score, result))

        if not candidates:
            return None

        # Current + correct scope dominates semantic similarity.
        candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
        return candidates[0][-1]

    def full_text(self, citation: str) -> str:
        try:
            return get_section_store().get_text(citation) or ""
        except Exception as e:
            # Verification must degrade to the retrieved chunk, never fail.
            logger.warning("Authoritative section lookup failed for %r: %s", citation, e)
            return ""


# ── OpenContracts publisher status -> Policy Lab legal effect ──
#
# Their vocabulary describes a publication lifecycle. Ours describes whether a
# present duty can rest on the document. The mapping is many-to-few and lossy
# in exactly one direction -- towards caution.
#
# CURRENT/EFFECTIVE are the only states that assert the text is in force now.
# ENACTED, SIGNED, ADOPTED, APPROVED, PUBLISHED and FILED all mean "it
# happened" and say nothing about whether it has since been amended, so they
# are not enough on their own; a version check has to confirm it. Everything
# else is either not-yet-law or no-longer-law.
_PUBLISHER_STATUS_MAP = {
    "CURRENT": SourceStatus.current_verified,
    "EFFECTIVE": SourceStatus.current_verified,
    # In force at some point, standing now unconfirmed.
    "ENACTED": SourceStatus.status_unknown,
    "SIGNED": SourceStatus.status_unknown,
    "ADOPTED": SourceStatus.status_unknown,
    "APPROVED": SourceStatus.status_unknown,
    "PUBLISHED": SourceStatus.status_unknown,
    "FILED": SourceStatus.status_unknown,
    # Not law yet, and may never be.
    "PENDING": SourceStatus.proposed,
    "PROPOSED": SourceStatus.proposed,
    "DRAFT": SourceStatus.proposed,
    # Was, and is not.
    "SUPERSEDED": SourceStatus.superseded,
    "EXPIRED": SourceStatus.superseded,
    "WITHDRAWN": SourceStatus.historical,
    "REJECTED": SourceStatus.historical,
}

# Federal Register provenance. A section sourced from a proposed rule is a
# proposal whatever its publisher status says, so this is checked first and
# overrides.
_PROPOSED_RULE_HINTS = re.compile(
    r"\b(proposed[\s_-]?rule|NPRM|notice[\s_-]of[\s_-]proposed[\s_-]rulemaking"
    r"|advance[\s_-]notice)\b",
    re.IGNORECASE,
)


def derive_source_status(meta: dict) -> SourceStatus:
    """Policy Lab's legal-effect status for an OpenContracts authority document.

    Reads only fields OpenContracts actually writes (see
    ``AuthoritySourceRecord.as_document_metadata``):
    ``status``, ``current_version``, ``superseded_by_key``, ``effective_from``,
    ``effective_until``, ``effective_date_review_status``, ``instrument_type``.

    Fails closed at every branch. The order is significant -- each check can
    only move the answer away from CURRENT_VERIFIED, never towards it.
    """
    meta = meta or {}

    # 1. Provenance beats declared status. A section extracted from an NPRM is
    #    a proposal even if the record was stamped CURRENT.
    provenance = " ".join(
        str(meta.get(k) or "")
        for k in ("instrument_type", "source_url", "title", "version_label", "authority_type")
    )
    if _PROPOSED_RULE_HINTS.search(provenance):
        return SourceStatus.proposed

    # 2. An explicit supersession relationship settles it.
    if meta.get("superseded_by_key"):
        return SourceStatus.superseded

    # 3. OpenContracts' own version flag. False is an assertion, not an absence.
    if meta.get("current_version") is False:
        return SourceStatus.superseded

    # 4. Publisher lifecycle state.
    raw_status = str(meta.get("status") or "").strip().upper()
    mapped = _PUBLISHER_STATUS_MAP.get(raw_status)
    if mapped is None:
        # An unrecognised state is a state we have not reasoned about. It does
        # not get the benefit of the doubt.
        if raw_status:
            logger.info("Unmapped OpenContracts authority status %r -> STATUS_UNKNOWN", raw_status)
        return SourceStatus.status_unknown
    if mapped is not SourceStatus.current_verified:
        return mapped

    # 5. Current-but-unreviewed. OpenContracts sets this itself when a record
    #    claims to be current without stating an effective date, and it is
    #    exactly the case Policy Lab must not treat as established.
    if meta.get("effective_date_review_status") == "UNKNOWN_NEEDS_REVIEW":
        return SourceStatus.status_unknown

    # 6. A stated end date that has passed.
    if meta.get("effective_until"):
        return SourceStatus.superseded

    return SourceStatus.current_verified


# Which authority families are codified law. Deliberately expressed as a
# SourceCategory rather than a boolean, because verification already decides
# binding force from the document's category -- so an OpenContracts record maps
# into the existing rule instead of getting a parallel one.
_BINDING_INSTRUMENTS = {"REGULATION", "STATUTE", "RULE", "CODE", "ORDINANCE"}

# OpenContracts' authority-type vocabulary (``enrichment.constants``). These
# classify the source itself, and "guidance" is disqualifying however the
# instrument is labelled -- an agency FAQ published as a REGULATION-typed
# artefact is still an agency FAQ.
_NON_BINDING_AUTHORITY_TYPES = {"guidance", "case", "treaty"}


def derive_category(meta: dict) -> SourceCategory:
    """The Policy Lab source category for an OpenContracts authority document.

    Codified law becomes ``federal_regulation`` / ``state_law``; everything
    else becomes ``federal_guidance``, which is authoritative about what an
    agency expects but cannot establish a legal requirement.
    """
    meta = meta or {}
    instrument = str(meta.get("instrument_type") or "").strip().upper()
    authority_type = str(meta.get("authority_type") or "").strip().lower()
    jurisdiction = str(meta.get("jurisdiction") or "").strip().upper()

    if authority_type in _NON_BINDING_AUTHORITY_TYPES:
        return SourceCategory.federal_guidance
    if instrument not in _BINDING_INSTRUMENTS:
        return SourceCategory.federal_guidance
    if jurisdiction and not jurisdiction.startswith(("US", "FEDERAL", "UNITED")):
        return SourceCategory.state_law
    return SourceCategory.federal_regulation


def canonical_key_for(citation: str) -> str:
    """A Policy Lab citation rendered as an OpenContracts canonical key.

    Their grammar is ``cfr-{title}:{section}`` -- see
    ``pipeline/authority_source_providers/cfr_provider.py``, whose docstring
    gives ``cfr-40:261.4`` and ``cfr-17:240.10b-5``. So
    "45 CFR § 164.404(c)" becomes "cfr-45:164.404(c)", and OpenContracts'
    own ``candidate_keys`` then handles subsection roll-up.
    """
    text = (citation or "").strip()
    match = re.search(
        r"\b(?P<title>\d+)\s*CFR\s*(?:§+\s*)?(?P<section>\d+(?:\.\d+)+)(?P<subs>(?:\s*\([A-Za-z0-9]+\))*)",
        text,
        re.IGNORECASE,
    )
    if match:
        subs = re.sub(r"\s+", "", match.group("subs") or "")
        return f"cfr-{match.group('title')}:{match.group('section')}{subs}"

    usc = re.search(
        r"\b(?P<title>\d+)\s*U\.?S\.?C\.?\s*(?:§+\s*)?(?P<section>\d+[A-Za-z0-9-]*)"
        r"(?P<subs>(?:\s*\([A-Za-z0-9]+\))*)",
        text,
        re.IGNORECASE,
    )
    if usc:
        subs = re.sub(r"\s+", "", usc.group("subs") or "")
        return f"usc-{usc.group('title')}:{usc.group('section')}{subs}"

    return re.sub(r"\s+", "-", text.lower())


class LegacyFallbackAuthorityProvider:
    """OpenContracts, with the legacy substrate behind it only when it is down.

    The distinction this class exists to enforce: a substrate that cannot be
    reached is a different thing from a substrate that answered. Falling back
    on the first is availability. Falling back on the second would give every
    claim two stores to be believed by, and the whole point of the verification
    rules is that a claim gets one honest answer.

    So the fallback is consulted when the OpenContracts runtime cannot start,
    and never because OpenContracts resolved nothing, or resolved something
    proposed, superseded, or of unknown standing. Those are answers.
    """

    def __init__(self, primary, fallback):
        self._primary = primary
        self._fallback = fallback

    @property
    def resolves_without_retrieval_context(self) -> bool:
        # Whichever substrate is actually serving. On the legacy one the
        # retrieved chunks are the authority again, so the guard must come back.
        return getattr(self._substrate(), "resolves_without_retrieval_context", False)

    def _substrate(self):
        from app.services.retrieval import opencontracts_runtime as ocr

        if ocr.available():
            return self._primary
        logger.error(
            "OpenContracts unavailable (%s); using the legacy authority path",
            ocr.unavailable_reason(),
        )
        return self._fallback

    def find_authority(self, citation: str, retrieval_context):
        return self._substrate().find_authority(citation, retrieval_context)

    def full_text(self, citation: str) -> str:
        return self._substrate().full_text(citation)


def get_authority_provider(matcher) -> AuthorityProvider:
    """The authority provider this deployment verifies against.

    Defaults to OpenContracts. ``AUTHORITY_PROVIDER=chroma`` pins the legacy
    path; an unrecognised value gets the default rather than an exception,
    because a typo in an environment variable must not decide which store the
    law comes from.
    """
    from app.config import settings

    choice = (settings.authority_provider or "").strip().lower()
    if choice == "chroma":
        logger.warning("AUTHORITY_PROVIDER=chroma: using the legacy authority substrate")
        return ChromaAuthorityProvider(matcher)

    if choice not in ("", "opencontracts"):
        logger.warning(
            "Unrecognised AUTHORITY_PROVIDER=%r; using opencontracts", settings.authority_provider
        )

    from app.services.retrieval.opencontracts_client import CFRAuthorityClient
    from app.services.retrieval.opencontracts_provider import OpenContractsAuthorityProvider

    provider = OpenContractsAuthorityProvider(matcher, CFRAuthorityClient())
    if settings.authority_legacy_fallback_enabled:
        return LegacyFallbackAuthorityProvider(provider, ChromaAuthorityProvider(matcher))
    return provider
