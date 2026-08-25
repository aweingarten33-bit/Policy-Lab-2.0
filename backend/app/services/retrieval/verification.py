"""Regulatory verification and claim-evidence construction.

Verification is intentionally stricter than retrieval. A source being similar,
or even containing the cited section number, is not proof that it supports a
claim. A finding becomes ``verified`` only after all applicable deterministic
checks pass and the exact cited excerpt substantively supports the claim.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Dict, List, Optional, Tuple

from app.services.retrieval.models import (
    ClaimVerification,
    RetrievalContext,
    SourceAttribution,
    SourceCategory,
    SourceStatus,
    SourceType,
    VerificationReport,
    VerificationStatus,
    can_support_present_duty,
    resolve_source_status,
)
from app.services.retrieval.section_store import get_section_store
from app.services.retrieval.store import get_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Citation parsing
# ---------------------------------------------------------------------------
# Repeated subsection groups matter. ``(b)(1)(ii)(A)`` must not be truncated to
# ``(b)(1)`` or treated as interchangeable with a neighbouring paragraph.
_SUBS = r"(?:\s*\([A-Za-z0-9]+\))*"
_CFR_SECTION_RE = re.compile(
    rf"\b(?P<title>\d+)\s*CFR\s*(?:(?:§|section)\s*)?(?P<section>\d+(?:\.\d+)+)(?P<subs>{_SUBS})",
    re.IGNORECASE,
)
_CFR_PART_RE = re.compile(r"\b(?P<title>\d+)\s*CFR\s+Part\s+(?P<part>\d+)\b", re.IGNORECASE)
_USC_RE = re.compile(
    rf"\b(?P<title>\d+)\s*U\.?S\.?C\.?\s*(?:(?:§|section)\s*)?(?P<section>\d+[A-Za-z0-9-]*)(?P<subs>{_SUBS})",
    re.IGNORECASE,
)
_STATE_SECTION_RE = re.compile(
    rf"\b(?P<state>[A-Z]{{2}})\s+(?P<body>[A-Za-z][A-Za-z0-9 .&'/-]{{1,80}}?)\s+§\s*(?P<section>[A-Za-z0-9.-]+)(?P<subs>{_SUBS})",
    re.IGNORECASE,
)

CITATION_PATTERNS = [
    _CFR_SECTION_RE.pattern,
    _CFR_PART_RE.pattern,
    _USC_RE.pattern,
    _STATE_SECTION_RE.pattern,
    r"OIG\s+(?:C-?\d{4}|Advisory\s+Opinion\s+\d{2}-\d+)",
    r"OCR\s+(?:Guidance|Bulletin|FAQ)\s+.*?\d{4}",
]
CITATION_REGEXES = [re.compile(p, re.IGNORECASE) for p in CITATION_PATTERNS]
_SUBSECTION_RE = re.compile(r"\(([A-Za-z0-9]+)\)")


# ---------------------------------------------------------------------------
# Concrete-fact parsing
# ---------------------------------------------------------------------------
# Numeric hallucinations are especially dangerous because they look precise.
# Keep these checks deterministic and scoped to the cited authority.
NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
}
_NUM_WORD_PATTERN = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
_NUM_TOKEN = rf"(?:\d{{1,9}}(?:\.\d+)?|{_NUM_WORD_PATTERN})"
_UNIT_PATTERN = r"(?:calendar\s+|business\s+|working\s+)?(day|days|month|months|year|years|week|weeks|hour|hours)"
_DURATION_REGEX = re.compile(rf"\b(?P<n>{_NUM_TOKEN})[\s-]+(?P<u>{_UNIT_PATTERN})", re.IGNORECASE)
_PERCENT_REGEX = re.compile(rf"\b(?P<n>{_NUM_TOKEN})\s*(?:%|percent)\b", re.IGNORECASE)
_MONEY_REGEX = re.compile(r"(?P<raw>(?:USD\s*)?\$\s*\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*(?:\.\d{1,2})?\s+dollars?\b)", re.IGNORECASE)
_RATIO_REGEX = re.compile(r"\b(?P<a>\d{1,5})\s*(?::|to)\s*(?P<b>\d{1,5})\b|\b(?P<a2>\d{1,5})\s+per\s+(?P<b2>\d{1,5})\b", re.IGNORECASE)
_AGE_REGEX = re.compile(r"\b(?:age\s+)?(?P<n>\d{1,3})\s*(?:years?\s+of\s+age|years?\s+old|or\s+older|or\s+younger)\b|\bage\s+(?P<n2>\d{1,3})\b", re.IGNORECASE)
_DISTANCE_REGEX = re.compile(r"\b(?P<n>\d+(?:\.\d+)?)\s*(?P<u>feet|foot|ft|inches|inch|meters|meter|miles|mile|yards|yard)\b", re.IGNORECASE)
_THRESHOLD_REGEX = re.compile(
    r"\b(?P<q>at\s+least|no\s+fewer\s+than|no\s+more\s+than|not\s+more\s+than|minimum\s+of|maximum\s+of)\s+(?P<n>\d{1,7})\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")

# A claim about when something took, or takes, legal effect. Kept apart from
# every other kind of date a document carries, because that separation is the
# whole point: a publication date, a download date and a date mentioned in
# passing are not commencement dates, and a claim that one of them is has to be
# checked against a source that actually says so.
_EFFECTIVE_DATE_CLAIM_RE = re.compile(
    r"\b(?:took\s+effect|takes\s+effect|became\s+effective|becomes\s+effective"
    r"|effective\s+(?:date|as\s+of|on|from)|in\s+force\s+(?:as\s+of|from|since)"
    r"|applies?\s+(?:as\s+of|from)|commenc(?:ed|es|ing))\b",
    re.IGNORECASE,
)
# Dates in the forms these documents and models actually write them.
_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}"
    r"|(?:January|February|March|April|May|June|July|August|September|October"
    r"|November|December)\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}/\d{1,2}/\d{2,4})\b",
    re.IGNORECASE,
)

_MANDATORY_CLAIM_RE = re.compile(
    r"\b(must|shall|required|requires|requirement|mandatory|prohibited|may\s+not|cannot|must\s+not)\b",
    re.IGNORECASE,
)
_MANDATORY_SOURCE_RE = re.compile(
    r"\b(must|shall|required|is\s+required|are\s+required|may\s+not|prohibited|shall\s+not)\b",
    re.IGNORECASE,
)
_PERMISSIVE_SOURCE_RE = re.compile(
    r"\b(may|should|recommend(?:s|ed|ation)?|encourage(?:s|d)?|consider|optional|best\s+practice)\b",
    re.IGNORECASE,
)

# Only authoritative material may prove a legal/regulatory claim. Templates and
# example policies are useful retrieval context but are not legal authority.
_NON_AUTHORITATIVE_CATEGORIES = {
    SourceCategory.policy_clause_library,
    SourceCategory.policy_template,
    SourceCategory.example_policy,
}

# Why a source of each non-current standing cannot settle a present-day duty.
# Written for a compliance officer reading the finding, not for a log.
_STATUS_REASONS = {
    SourceStatus.proposed: (
        "The matching source is a PROPOSED rule, not a final one. A proposal can be "
        "changed, delayed or withdrawn, and it imposes no obligation unless and until "
        "a final rule takes effect — so it cannot establish a current requirement."
    ),
    SourceStatus.superseded: (
        "The matching source has been SUPERSEDED by a later version, so it cannot "
        "establish what is required now. Check the current text of the provision."
    ),
    SourceStatus.historical: (
        "The matching source is HISTORICAL/archived material. It is a record of what "
        "was once published, not a statement of current law."
    ),
    SourceStatus.status_unknown: (
        "The standing of the matching source could not be established — it is not "
        "confirmed to be the current, in-force text. A requirement cannot be verified "
        "against a source whose currency is unknown."
    ),
}


@dataclass(frozen=True)
class ConcreteFact:
    kind: str
    value: str
    unit: str = ""
    qualifier: str = ""
    raw: str = ""

    @property
    def display(self) -> str:
        if self.kind == "duration":
            return f"{self.value} {self.unit}"
        if self.kind == "percent":
            return f"{self.value}%"
        if self.kind == "money":
            return self.raw or f"${self.value}"
        if self.kind == "ratio":
            return f"{self.value}:{self.unit}"
        if self.kind == "age":
            return f"age {self.value}"
        if self.kind == "distance":
            return f"{self.value} {self.unit}"
        if self.kind == "threshold":
            return f"{self.qualifier} {self.value}".strip()
        return self.raw or self.value


class VerificationService:
    """Verify generated compliance claims against retrieved authority."""

    def __init__(self):
        self._store = None

    @property
    def store(self):
        if self._store is None:
            self._store = get_store()
        return self._store

    # ------------------------------------------------------------------
    # Public citation verification
    # ------------------------------------------------------------------
    def verify_citations(
        self,
        text: str,
        retrieval_context: Optional[RetrievalContext] = None,
    ) -> List[ClaimVerification]:
        citations_found = self._extract_citations(text)
        if not citations_found:
            return []

        citation_context = self._map_citations_to_sentences(text, citations_found)
        return [
            self._verify_single_citation(
                citation,
                retrieval_context,
                citation_context.get(citation, ""),
            )
            for citation in citations_found
        ]

    def verify_section(
        self,
        section_name: str,
        section_text: str,
        retrieval_context: Optional[RetrievalContext] = None,
    ) -> VerificationReport:
        claim_verifications = self.verify_citations(section_text, retrieval_context)
        verified = sum(v.verification_status == VerificationStatus.verified for v in claim_verifications)
        partially = sum(v.verification_status == VerificationStatus.partially_verified for v in claim_verifications)
        contradicted = sum(v.verification_status == VerificationStatus.contradicted for v in claim_verifications)
        cannot_determine = sum(
            v.verification_status == VerificationStatus.cannot_determine for v in claim_verifications
        )
        # `cannot_determine` is counted into unverified for the aggregate. Both
        # mean "not confirmed", and every caller that reads unverified_claims
        # treats it as "needs independent review" -- which is exactly right for
        # a claim whose source standing could not be established. The per-claim
        # detail keeps the distinction.
        unverified = sum(
            v.verification_status == VerificationStatus.unverified for v in claim_verifications
        ) + cannot_determine

        if contradicted:
            overall = VerificationStatus.contradicted
        elif unverified > verified:
            overall = VerificationStatus.unverified
        elif partially or (unverified and verified):
            overall = VerificationStatus.partially_verified
        elif verified and verified == len(claim_verifications):
            # A roll-up, not a verdict. This is the only place outside
            # apply_claim_support that names VerificationStatus.verified, and it
            # is reachable only when every underlying claim is already verified
            # -- each of which had to pass apply_claim_support to get there.
            # The equality is explicit so the aggregate can never be greener
            # than its parts.
            overall = VerificationStatus.verified
        else:
            overall = VerificationStatus.unverified

        return VerificationReport(
            section_name=section_name,
            total_claims=len(claim_verifications),
            verified_claims=verified,
            partially_verified_claims=partially,
            unverified_claims=unverified,
            contradicted_claims=contradicted,
            cannot_determine_claims=cannot_determine,
            claim_details=claim_verifications,
            overall_status=overall,
        )

    def create_source_attribution(
        self,
        citation: str,
        retrieval_context: Optional[RetrievalContext] = None,
        claim_text: str = "",
    ) -> SourceAttribution:
        if retrieval_context:
            match = self._find_source_for_citation(citation, retrieval_context)
            if match is not None:
                meta = match.chunk.metadata
                source_status = resolve_source_status(meta)
                scope_text = self._source_scope_text(
                    citation, meta.citation or "", match.chunk.text, allow_full_text=True
                )
                scope_ok = bool(scope_text)

                if not can_support_present_duty(source_status):
                    status = VerificationStatus.cannot_determine
                    warning = _STATUS_REASONS[source_status]
                elif scope_ok:
                    status = VerificationStatus.partially_verified
                    warning = (
                        "The cited authority was located, but the claim itself has not yet been "
                        "tested against the cited passage."
                    )
                else:
                    status = VerificationStatus.unverified
                    warning = "The cited authority could not be verified at the exact current citation scope."

                return SourceAttribution(
                    source_type=meta.source_type,
                    verification_status=status,
                    source_name=meta.source_name,
                    source_citation=meta.citation,
                    source_url=meta.url,
                    # The date of the version checked — not a publication date
                    # dressed up as one. See _evidence_source.
                    source_date=(
                        getattr(meta, "last_verified_date", None)
                        or meta.effective_date
                        or getattr(meta, "retrieved_date", None)
                    ),
                    retrieved_text=self._select_excerpt(claim_text, scope_text or match.chunk.text, citation=citation)[:500],
                    confidence=match.score,
                    warning=warning,
                )

        # A direct KB lookup may locate a citation that was not in the generation
        # context. It still earns only partial verification until claim support
        # is checked.
        try:
            search_results = self.store.query_all_collections(
                query_text=citation,
                n_results_per_collection=3,
            )
            for result_set in search_results:
                results = result_set["results"]
                if not results.get("ids") or not results["ids"][0]:
                    continue
                for i, _chunk_id in enumerate(results["ids"][0]):
                    meta_dict = results["metadatas"][0][i] if results.get("metadatas") else {}
                    stored_citation = meta_dict.get("citation", "")
                    category_raw = meta_dict.get("category") or result_set.get("collection")
                    try:
                        category = SourceCategory(category_raw)
                    except Exception:
                        category = None
                    if category in _NON_AUTHORITATIVE_CATEGORIES:
                        continue
                    if not stored_citation or not self._citations_match(citation, stored_citation):
                        continue
                    # Same standing gate as the in-context path. A stored chunk
                    # from before source_status existed carries only is_current,
                    # which is what the fallback below reads.
                    stored_status = meta_dict.get("source_status") or ""
                    if stored_status:
                        try:
                            if not can_support_present_duty(SourceStatus(stored_status)):
                                continue
                        except ValueError:
                            continue
                    elif str(meta_dict.get("is_current", "true")).lower() in {"false", "0", "no"}:
                        continue
                    doc_text = results["documents"][0][i] if results.get("documents") else ""
                    scope_text = self._source_scope_text(
                        citation, stored_citation, doc_text, allow_full_text=True
                    )
                    if not scope_text:
                        continue
                    distance = results["distances"][0][i] if results.get("distances") else 1.0
                    score = max(0.0, 1.0 - distance)
                    return SourceAttribution(
                        source_type=SourceType.retrieved_source,
                        verification_status=VerificationStatus.partially_verified,
                        source_name=meta_dict.get("source_name", ""),
                        source_citation=stored_citation,
                        source_url=meta_dict.get("url") or None,
                        source_date=(
                            meta_dict.get("last_verified_date")
                            or meta_dict.get("effective_date")
                            or meta_dict.get("retrieved_date")
                            or None
                        ),
                        retrieved_text=self._select_excerpt(claim_text, scope_text, citation=citation)[:500],
                        confidence=score,
                        warning="Citation located in authoritative material; substantive claim support is still pending.",
                    )
        except Exception as e:
            logger.warning("Verification search failed for citation %r: %s", citation, e)

        return SourceAttribution(
            source_type=SourceType.model_knowledge,
            verification_status=VerificationStatus.unverified,
            source_citation=citation,
            confidence=0.0,
            warning="Not verified from current authoritative sources. Requires independent review.",
        )

    # ------------------------------------------------------------------
    # Evidence records used by the production gap-analysis path
    # ------------------------------------------------------------------
    def build_claim_evidence(
        self,
        claim_id: str,
        claim_text: str,
        citation: str,
        retrieval_context: Optional[RetrievalContext] = None,
    ):
        from app.models.schemas import ClaimSupport, EvidenceChecks, EvidenceSource, VerificationEvidence

        evidence = VerificationEvidence(
            claim_id=claim_id,
            claim_text=claim_text,
            citation=citation or None,
            status=VerificationStatus.unverified,
            source=EvidenceSource(),
            checks=EvidenceChecks(),
            reason="",
        )

        if not retrieval_context or not retrieval_context.get_all_sources():
            evidence.reason = "No authoritative source material was retrieved, so this claim could not be verified."
            return evidence

        match = self._find_source_for_citation(citation, retrieval_context)
        if match is None:
            evidence.reason = (
                f"The cited authority ({citation or 'none given'}) was not found in current "
                "authoritative source material retrieved for this request."
            )
            return evidence

        meta = match.chunk.metadata
        status = resolve_source_status(meta)
        evidence.checks.source_status = status
        evidence.checks.source_status_current = can_support_present_duty(status)

        if not can_support_present_duty(status):
            # A source whose standing is proposed, superseded, historical or
            # simply unknown cannot establish what the law requires today. This
            # is not "no support found" -- it is that no conclusion about
            # present law is available from this material, which is a different
            # thing to tell a reader.
            evidence.source = self._evidence_source(
                meta, self._select_excerpt(claim_text, match.chunk.text, citation=citation), status
            )
            evidence.status = VerificationStatus.cannot_determine
            evidence.reason = _STATUS_REASONS[status]
            return evidence

        scope_text = self._source_scope_text(
            citation, meta.citation or "", match.chunk.text, allow_full_text=True
        )
        if not scope_text:
            evidence.source = self._evidence_source(
                meta, self._select_excerpt(claim_text, match.chunk.text, citation=citation), status
            )
            evidence.reason = (
                "The regulation section was located, but the exact subsection cited by the "
                "finding was not present in that source text."
            )
            return evidence

        evidence.checks.citation_exists = True
        excerpt = self._select_excerpt(claim_text, scope_text, citation=citation)
        evidence.source = self._evidence_source(meta, excerpt, status)

        # IMPORTANT: concrete facts are checked only against the matched cited
        # authority/scope, never against the pooled retrieval context. A number
        # appearing in an unrelated regulation must not rescue a bad claim.
        specifics = self._extract_specifics(claim_text)
        if specifics:
            unsupported = [f for f in specifics if not self._specific_supported(f, scope_text)]
            evidence.checks.specifics_supported = not unsupported
            if unsupported:
                evidence.status = VerificationStatus.partially_verified
                evidence.reason = (
                    "The cited authority was found, but these concrete fact(s) are not stated "
                    f"at that citation scope: {', '.join(f.display for f in unsupported)}."
                )
                return evidence

        evidence.status = VerificationStatus.partially_verified
        evidence.checks.claim_support = ClaimSupport.not_checked
        evidence.reason = (
            "The current cited authority and any concrete facts were located at the cited "
            "scope. Substantive claim support still requires the entailment check."
        )
        return evidence

    @staticmethod
    def _evidence_source(meta, excerpt: str, status: SourceStatus):
        """Build an EvidenceSource carrying each date as the date it actually is."""
        from app.models.schemas import EvidenceSource

        return EvidenceSource(
            name=meta.source_name,
            url=meta.url,
            # What version was checked: the last time the text was confirmed
            # against its publisher, falling back through the other dates. The
            # publication date is last because it is the weakest answer to
            # "which version is this".
            version_date=(
                getattr(meta, "last_verified_date", None)
                or meta.effective_date
                or getattr(meta, "retrieved_date", None)
                or getattr(meta, "publication_date", None)
            ),
            excerpt=excerpt,
            publication_date=getattr(meta, "publication_date", None),
            effective_date=meta.effective_date,
            retrieved_date=getattr(meta, "retrieved_date", None),
            last_verified_date=getattr(meta, "last_verified_date", None),
            status=status,
        )

    def apply_claim_support(self, evidence, support, note: str = ""):
        """Fold the semantic entailment result into an evidence record.

        This is the only path to ``verified``. It is guarded by two
        deterministic checks that a classifier cannot talk its way past:

          * modality — a claim asserting a mandate cannot be promoted from an
            excerpt that is clearly only permissive or recommendatory;
          * standing — the source must be established as currently in force. A
            proposed, superseded, historical or unknown-status source can never
            confirm a present legal duty, however well its text matches.
        """
        from app.models.schemas import ClaimSupport

        # Standing gate. A non-current source can never carry a claim UP: no
        # amount of textual support from a proposed, superseded, historical or
        # unknown-standing document tells you what the law requires today.
        #
        # It does not interfere with the downward results. "This excerpt does
        # not say that" and "this excerpt says the opposite" are observations
        # about the text itself and remain true whatever the document's
        # standing, and they are more informative to a reader than a blanket
        # cannot-determine, so they keep their own meaning.
        if (
            not evidence.checks.source_status_current
            and support in (ClaimSupport.supported, ClaimSupport.partially_supported)
            # Only when a source was actually matched. With no located passage
            # there is no standing to judge, and the "no authoritative passage
            # was located" branch below is both true and more useful than
            # blaming the source's currency for a citation that does not exist.
            and evidence.source.excerpt
        ):
            evidence.checks.claim_support = support
            evidence.status = VerificationStatus.cannot_determine
            evidence.reason = _STATUS_REASONS.get(
                evidence.checks.source_status, _STATUS_REASONS[SourceStatus.status_unknown]
            )
            return evidence

        # Effective-date gate. Same shape as the modality gate: a deterministic
        # check the classifier cannot overrule, because "the source discusses
        # this and carries a date" is exactly the pattern that makes a
        # publication date read as a commencement date.
        if support in (ClaimSupport.supported, ClaimSupport.partially_supported):
            bad_date = self._unsupported_effective_date(evidence.claim_text, evidence)
            if bad_date:
                support = ClaimSupport.not_supported
                note = (
                    f"The claim states {bad_date} as an effective date, but the cited source "
                    f"does not give an effective date and that date does not appear in the "
                    f"cited passage. A publication or retrieval date is not an effective date."
                )

        excerpt = evidence.source.excerpt or ""
        if (
            support == ClaimSupport.supported
            and self._claim_asserts_mandate(evidence.claim_text)
            and self._excerpt_is_clearly_permissive(excerpt)
        ):
            support = ClaimSupport.not_supported
            note = note or (
                "The claim presents a legal mandate, but the cited passage uses permissive "
                "or recommendatory language rather than imposing that duty."
            )

        evidence.checks.claim_support = support

        if support == ClaimSupport.contradicted:
            evidence.status = VerificationStatus.contradicted
            evidence.reason = note or "The authoritative excerpt contradicts this claim."
        elif (
            support == ClaimSupport.supported
            and evidence.checks.citation_exists
            and evidence.checks.specifics_supported is not False
            and evidence.source.excerpt
            # Restated here rather than relying on the early return above, so
            # the conjunction that produces `verified` names every condition it
            # depends on and cannot be weakened by an edit elsewhere.
            and evidence.checks.source_status_current
        ):
            evidence.status = VerificationStatus.verified
            evidence.reason = note or (
                "Current citation located, concrete facts match the cited scope, and the "
                "authoritative excerpt supports the claim."
            )
        elif support == ClaimSupport.not_supported:
            evidence.status = VerificationStatus.unverified
            evidence.reason = note or "The cited authority exists, but the cited passage does not support this claim."
        elif not evidence.checks.citation_exists or not evidence.source.excerpt:
            # No located authority and no excerpt means there is nothing to have
            # partially supported anything. This previously fell through to
            # partially_verified, which both overstated the result and printed a
            # reason -- "the cited passage bears on the claim" -- describing a
            # passage that was never found.
            evidence.status = VerificationStatus.unverified
            evidence.reason = note or (
                "No authoritative passage was located for this citation, so the claim "
                "could not be checked against source material."
            )
        else:
            evidence.status = VerificationStatus.partially_verified
            # An already-recorded reason wins over the generic one. When the
            # concrete-fact check failed, it named the exact figure the source
            # does not state -- which is the whole finding -- and overwriting
            # that with "bears on the claim" threw away the useful half.
            evidence.reason = note or evidence.reason or (
                "The cited passage bears on the claim but does not fully establish it."
            )
        return evidence

    def check_unsupported_specifics(
        self,
        text: str,
        retrieval_context: Optional[RetrievalContext] = None,
        citation: str = "",
    ) -> Optional[str]:
        """Return an inline warning for concrete facts unsupported by the cited authority."""
        if not text or not retrieval_context:
            return None

        specifics = self._extract_specifics(text)
        if not specifics:
            return None

        if citation:
            match = self._find_source_for_citation(citation, retrieval_context)
            if match is None:
                return None
            source_text = self._source_scope_text(citation, match.chunk.metadata.citation or "", match.chunk.text)
        else:
            source_text = " ".join(
                r.chunk.text
                for r in retrieval_context.get_all_sources()
                if self._is_authoritative_result(r)
            )

        if not source_text.strip():
            return None

        unsupported = [f for f in specifics if not self._specific_supported(f, source_text)]
        if not unsupported:
            return None

        return (
            "Verify this concrete fact against the cited authority ("
            + ", ".join(f.display for f in unsupported)
            + "). It is not stated at the cited source scope, so it must not be presented as a confirmed legal requirement."
        )

    # ------------------------------------------------------------------
    # Citation matching and excerpt selection
    # ------------------------------------------------------------------
    def _extract_citations(self, text: str) -> List[str]:
        citations: List[str] = []
        for regex in CITATION_REGEXES:
            for match in regex.finditer(text):
                citations.append(match.group(0).strip())
        seen = set()
        unique = []
        for citation in citations:
            key = self._normalize_generic_citation(citation)
            if key not in seen:
                seen.add(key)
                unique.append(citation)
        return unique

    def _map_citations_to_sentences(self, text: str, citations: List[str]) -> Dict[str, str]:
        sentences = _SENTENCE_SPLIT.split(text)
        mapping: Dict[str, str] = {}
        for citation in citations:
            needle = self._normalize_generic_citation(citation)
            for sentence in sentences:
                if needle in self._normalize_generic_citation(sentence):
                    mapping[citation] = sentence.strip()
                    break
        return mapping

    def _verify_single_citation(
        self,
        citation: str,
        retrieval_context: Optional[RetrievalContext] = None,
        claim_sentence: str = "",
    ) -> ClaimVerification:
        attribution = self.create_source_attribution(citation, retrieval_context, claim_text=claim_sentence)
        status = attribution.verification_status
        warning = attribution.warning

        if status == VerificationStatus.partially_verified and claim_sentence and attribution.retrieved_text:
            specifics = self._extract_specifics(claim_sentence)
            unsupported = [
                fact for fact in specifics
                if not self._specific_supported(fact, attribution.retrieved_text)
            ]
            if unsupported:
                warning = (
                    f"Citation {citation} was located, but these concrete fact(s) are not "
                    f"supported by the cited passage: {', '.join(f.display for f in unsupported)}."
                )

        return ClaimVerification(
            claim_text=claim_sentence or citation,
            claimed_citation=citation,
            verification_status=status,
            supporting_evidence=attribution.retrieved_text,
            evidence_source=attribution.source_name,
            warning=warning,
        )

    def _find_source_for_citation(self, citation: str, retrieval_context: RetrievalContext):
        if not citation:
            return None

        candidates = []
        for result in retrieval_context.get_all_sources():
            if not self._is_authoritative_result(result):
                continue
            meta = result.chunk.metadata
            if not meta.citation or not self._citations_match(citation, meta.citation):
                continue
            scope_ok = bool(self._source_scope_text(
                citation, meta.citation, result.chunk.text, allow_full_text=True
            ))
            current = can_support_present_duty(resolve_source_status(meta))
            exact = self._normalize_generic_citation(citation) == self._normalize_generic_citation(meta.citation)
            candidates.append((current, scope_ok, exact, result.score, result))

        if not candidates:
            return None

        # Current + correct scope dominates semantic similarity.
        candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
        return candidates[0][-1]

    @staticmethod
    def _is_authoritative_result(result) -> bool:
        meta = result.chunk.metadata
        return meta.category not in _NON_AUTHORITATIVE_CATEGORIES

    def _source_scope_text(
        self,
        claimed: str,
        stored: str,
        source_text: str,
        *,
        allow_full_text: bool = False,
    ) -> str:
        """Return text for the exact citation scope, or empty when scope is absent.

        ``allow_full_text`` lets the search fall back to the complete stored
        section when the retrieved chunk does not contain the cited subsection.
        That fallback is the point of the authoritative section store: a chunk
        is an ~800-character window chosen for embedding quality, so the
        paragraph that decides a claim is very often in the *next* chunk. Before
        it existed, "the subsection is not in this window" and "the regulation
        does not contain this subsection" were the same answer, and the second
        is what got reported.
        """
        claimed_key = self._citation_key(claimed)
        stored_key = self._citation_key(stored)
        if claimed_key is None:
            return source_text

        kind, authority, section, subs = claimed_key
        if not subs:
            return source_text

        if stored_key is not None:
            skind, sauthority, ssection, ssubs = stored_key
            if (kind, authority, section) != (skind, sauthority, ssection):
                return ""
            # Metadata itself proves the chunk is at this scope when it is exact
            # or a descendant of the claimed paragraph.
            if ssubs and tuple(ssubs[: len(subs)]) == tuple(subs):
                return source_text

        found = self._locate_subsection(source_text, subs)
        if found:
            return found

        if allow_full_text:
            full_text = self._authoritative_full_text(claimed)
            if full_text and full_text != source_text:
                return self._locate_subsection(full_text, subs)

        return ""

    @staticmethod
    def _locate_subsection(source_text: str, subs) -> str:
        """Text around a nested subsection marker chain, or "" if absent.

        Returns a generous window rather than the paragraph alone: a duty is
        frequently stated in the lead-in and qualified in the paragraph, and
        both have to be visible for the support check to be fair.
        """
        if not source_text:
            return ""
        lower = source_text.lower()
        pos = 0
        first_pos = None
        for token in subs:
            marker = f"({str(token).lower()})"
            idx = lower.find(marker, pos)
            if idx < 0:
                return ""
            if first_pos is None:
                first_pos = idx
            pos = idx + len(marker)

        start = max(0, (first_pos or 0) - 150)
        end = min(len(source_text), pos + 2200)
        return source_text[start:end]

    @staticmethod
    def _authoritative_full_text(citation: str) -> str:
        """The complete stored text of a cited section, or "" if not held."""
        try:
            return get_section_store().get_text(citation) or ""
        except Exception as e:
            # Verification must degrade to the retrieved chunk, never fail.
            logger.warning("Authoritative section lookup failed for %r: %s", citation, e)
            return ""

    def _select_excerpt(
        self,
        claim: str,
        source_text: str,
        *,
        citation: str = "",
        max_chars: int = 900,
    ) -> str:
        if not source_text:
            return ""
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(source_text) if s.strip()]
        if not sentences:
            return source_text[:max_chars]

        claim_terms = self._content_terms(claim)
        if not claim_terms:
            return " ".join(sentences)[:max_chars]

        scored = []
        for i, sent in enumerate(sentences):
            overlap = len(claim_terms & self._content_terms(sent))
            scored.append((overlap, i, sent))
        scored.sort(key=lambda t: (-t[0], t[1]))
        best_idx = scored[0][1]
        window = sentences[max(0, best_idx - 1) : best_idx + 2]
        return " ".join(window).strip()[:max_chars]

    @staticmethod
    def _content_terms(text: str) -> set:
        stop = {
            "the", "and", "for", "that", "this", "with", "from", "must", "shall",
            "any", "all", "such", "under", "section", "part", "cfr", "usc", "may",
            "not", "are", "was", "were", "been", "have", "has", "will", "which",
            "its", "their", "them", "they", "policy", "policies", "required",
            "requirement", "requirements", "including", "include", "includes",
        }
        return {
            w for w in re.findall(r"[a-z]{4,}", text.lower())
            if w not in stop
        }

    def _citation_key(self, citation: str) -> Optional[Tuple[str, str, str, Tuple[str, ...]]]:
        if not citation:
            return None
        if m := _CFR_SECTION_RE.search(citation):
            return (
                "cfr",
                m.group("title"),
                m.group("section"),
                tuple(x.lower() for x in _SUBSECTION_RE.findall(m.group("subs") or "")),
            )
        if m := _CFR_PART_RE.search(citation):
            return ("cfr_part", m.group("title"), m.group("part"), tuple())
        if m := _USC_RE.search(citation):
            return (
                "usc",
                m.group("title"),
                m.group("section").lower(),
                tuple(x.lower() for x in _SUBSECTION_RE.findall(m.group("subs") or "")),
            )
        if m := _STATE_SECTION_RE.search(citation):
            body = re.sub(r"\s+", " ", m.group("body").lower()).strip()
            return (
                f"state:{m.group('state').upper()}",
                body,
                m.group("section").lower(),
                tuple(x.lower() for x in _SUBSECTION_RE.findall(m.group("subs") or "")),
            )
        return None

    def _citations_match(self, cite_a: str, cite_b: str) -> bool:
        a_key = self._citation_key(cite_a)
        b_key = self._citation_key(cite_b)
        if a_key and b_key:
            if a_key[:3] != b_key[:3]:
                return False
            a_subs, b_subs = a_key[3], b_key[3]
            if not a_subs or not b_subs:
                return True
            # Ancestor/descendant is acceptable for locating source material;
            # _source_scope_text then proves the exact claimed paragraph exists.
            shorter = min(len(a_subs), len(b_subs))
            return a_subs[:shorter] == b_subs[:shorter]

        # Never let a state citation match a different state's authority.
        a_state = re.match(r"^\s*([A-Z]{2})\b", cite_a, re.IGNORECASE)
        b_state = re.match(r"^\s*([A-Z]{2})\b", cite_b, re.IGNORECASE)
        if a_state and b_state and a_state.group(1).upper() != b_state.group(1).upper():
            return False

        a = self._normalize_generic_citation(cite_a)
        b = self._normalize_generic_citation(cite_b)
        if not a or not b:
            return False
        if a == b:
            return True

        # Generic guidance names may carry a year or subtitle. Allow containment
        # only at a token boundary; never use it to match parsed CFR/USC cites.
        if a_key or b_key:
            return False
        return re.search(rf"(?:^| ){re.escape(a)}(?: |$)", b) is not None or re.search(
            rf"(?:^| ){re.escape(b)}(?: |$)", a
        ) is not None

    @staticmethod
    def _normalize_generic_citation(citation: str) -> str:
        c = citation.lower().strip()
        c = re.sub(r"[§¶,;:]", " ", c)
        c = c.replace("section", " ")
        c = re.sub(r"\s+", " ", c).strip()
        return c

    # ------------------------------------------------------------------
    # Concrete fact helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _number_value(raw: str) -> str:
        value = raw.lower().replace(",", "").strip()
        if value in NUMBER_WORDS:
            return str(NUMBER_WORDS[value])
        try:
            n = float(value)
            return str(int(n)) if n.is_integer() else str(n)
        except ValueError:
            return value

    def _extract_specifics(self, text: str) -> List[ConcreteFact]:
        facts: List[ConcreteFact] = []

        for m in _DURATION_REGEX.finditer(text):
            unit = m.group("u").lower().strip()
            unit = re.sub(r"^(calendar|business|working)\s+", "", unit).rstrip("s")
            facts.append(ConcreteFact("duration", self._number_value(m.group("n")), unit, raw=m.group(0)))

        for m in _PERCENT_REGEX.finditer(text):
            facts.append(ConcreteFact("percent", self._number_value(m.group("n")), raw=m.group(0)))

        for m in _MONEY_REGEX.finditer(text):
            digits = re.sub(r"[^0-9.]", "", m.group("raw"))
            if digits:
                facts.append(ConcreteFact("money", self._number_value(digits), raw=m.group("raw")))

        for m in _RATIO_REGEX.finditer(text):
            a, b = (m.group("a"), m.group("b")) if m.group("a") else (m.group("a2"), m.group("b2"))
            facts.append(ConcreteFact("ratio", a, b, raw=m.group(0)))

        for m in _AGE_REGEX.finditer(text):
            n = m.group("n") or m.group("n2")
            facts.append(ConcreteFact("age", n, raw=m.group(0)))

        for m in _DISTANCE_REGEX.finditer(text):
            unit = m.group("u").lower()
            canonical = {
                "feet": "foot", "ft": "foot", "foot": "foot",
                "inches": "inch", "inch": "inch",
                "meters": "meter", "meter": "meter",
                "miles": "mile", "mile": "mile",
                "yards": "yard", "yard": "yard",
            }[unit]
            facts.append(ConcreteFact("distance", self._number_value(m.group("n")), canonical, raw=m.group(0)))

        for m in _THRESHOLD_REGEX.finditer(text):
            q = re.sub(r"\s+", " ", m.group("q").lower()).strip()
            facts.append(ConcreteFact("threshold", m.group("n"), qualifier=q, raw=m.group(0)))

        seen = set()
        unique = []
        for fact in facts:
            key = (fact.kind, fact.value, fact.unit, fact.qualifier)
            if key not in seen:
                seen.add(key)
                unique.append(fact)
        return unique

    def _number_forms(self, value: str) -> set[str]:
        forms = {value}
        try:
            ivalue = int(float(value))
        except ValueError:
            return forms
        for word, n in NUMBER_WORDS.items():
            if n == ivalue:
                forms.add(word)
        return forms

    def _specific_supported(self, fact: ConcreteFact, source_text: str) -> bool:
        haystack = source_text.lower()
        forms = self._number_forms(fact.value)

        if fact.kind == "duration":
            return any(
                re.search(rf"\b{re.escape(n)}[\s-]+(?:calendar\s+|business\s+|working\s+)?{re.escape(fact.unit)}s?\b", haystack)
                for n in forms
            )
        if fact.kind == "percent":
            return any(re.search(rf"\b{re.escape(n)}\s*(?:%|percent)\b", haystack) for n in forms)
        if fact.kind == "money":
            return any(
                re.search(rf"(?:\$\s*{re.escape(n)}\b|\b{re.escape(n)}\s+dollars?\b)", haystack.replace(",", ""))
                for n in forms
            )
        if fact.kind == "ratio":
            return bool(
                re.search(rf"\b{re.escape(fact.value)}\s*(?::|to)\s*{re.escape(fact.unit)}\b", haystack)
                or re.search(rf"\b{re.escape(fact.value)}\s+per\s+{re.escape(fact.unit)}\b", haystack)
            )
        if fact.kind == "age":
            return any(
                re.search(rf"\bage\s+{re.escape(n)}\b|\b{re.escape(n)}\s+years?\s+(?:of\s+age|old)\b|\b{re.escape(n)}\s+or\s+(?:older|younger)\b", haystack)
                for n in forms
            )
        if fact.kind == "distance":
            unit_forms = {
                "foot": r"(?:foot|feet|ft)",
                "inch": r"(?:inch|inches)",
                "meter": r"(?:meter|meters)",
                "mile": r"(?:mile|miles)",
                "yard": r"(?:yard|yards)",
            }[fact.unit]
            return any(re.search(rf"\b{re.escape(n)}\s*{unit_forms}\b", haystack) for n in forms)
        if fact.kind == "threshold":
            q = re.escape(fact.qualifier).replace(r"\ ", r"\s+")
            return bool(re.search(rf"\b{q}\s+{re.escape(fact.value)}\b", haystack))
        return True

    @staticmethod
    def _claim_asserts_mandate(text: str) -> bool:
        return bool(_MANDATORY_CLAIM_RE.search(text or ""))

    @staticmethod
    def _unsupported_effective_date(claim: str, evidence) -> Optional[str]:
        """The date a claim asserts as an effective date, if nothing establishes it.

        Returns the offending date, or None when the claim makes no
        effective-date assertion or the source does establish it.

        A source may carry several dates -- when it was published, when we
        downloaded it, when we last confirmed it -- and only one of them is an
        answer to "when did this take effect". Two things can settle that: the
        source's own stated effective_date, or the date appearing in the cited
        passage. A publication date is neither, however close it looks.
        """
        if not claim or not _EFFECTIVE_DATE_CLAIM_RE.search(claim):
            return None

        claimed = _DATE_RE.findall(claim)
        if not claimed:
            return None

        source = evidence.source
        stated = (getattr(source, "effective_date", None) or "").strip()
        excerpt = source.excerpt or ""

        for date_text in claimed:
            normalized = date_text.strip().lower()
            if stated and normalized in stated.strip().lower():
                continue
            if _DATE_RE.search(excerpt) and normalized in excerpt.lower():
                continue
            return date_text
        return None

    @staticmethod
    def _excerpt_is_clearly_permissive(text: str) -> bool:
        if not text:
            return False
        return bool(_PERMISSIVE_SOURCE_RE.search(text)) and not bool(_MANDATORY_SOURCE_RE.search(text))


_verification: Optional[VerificationService] = None


def get_verification_service() -> VerificationService:
    global _verification
    if _verification is None:
        _verification = VerificationService()
    return _verification
