"""
Verification Service — Post-generation claim verification against source material.

After each generation step, this service checks whether:
  1. Cited regulations exist in the source set
  2. Quoted language is actually supported by source text
  3. Claims without source backing are flagged as unverified

This is the core of the no-hallucination architecture:
  - If a claim cannot be verified from source material, it is flagged
  - If a citation doesn't exist in the source set, it is flagged
  - The UI and export clearly show what is verified vs. unverified
"""

import re
import logging
from typing import Optional, List, Dict, Tuple

from app.services.retrieval.models import (
    SourceChunk, SourceMetadata, SourceType, SourceCategory,
    RetrievalResult, RetrievalContext,
    SourceAttribution, VerificationStatus, ClaimVerification, VerificationReport,
)
from app.services.retrieval.store import get_store

logger = logging.getLogger(__name__)

# ── Citation patterns ──

# Common regulatory citation patterns
CITATION_PATTERNS = [
    # CFR citations: 45 CFR §164.530(b)(1), 42 CFR Part 2
    r'\d+\s+CFR\s+§?\d+\.\d+(?:\([a-z]\)(?:\(\d+\))?)?',
    r'\d+\s+CFR\s+Part\s+\d+',
    # USC citations: 42 USC §1320d-6
    r'\d+\s+USC\s+§?\d+[a-z]?-?\d*',
    # State code citations: NY Pub Health Law §18
    r'[A-Z]{2}\s+(?:Pub\s+)?(?:Health|Gen\s+Bus|Civil|Penal)\s+(?:Law|Code)\s+§?\d+',
    # OIG citations
    r'OIG\s+(?:C-?\d{4}|Advisory\s+Opinion\s+\d{2}-\d+)',
    # OCR citations
    r'OCR\s+(?:Guidance|Bulletin|FAQ)\s+.*?\d{4}',
]

# Build compiled regexes
CITATION_REGEXES = [re.compile(p, re.IGNORECASE) for p in CITATION_PATTERNS]


class VerificationService:
    """
    Verifies claims in generated outputs against the curated knowledge base.

    For each claim or citation in an output:
      1. Check if the cited regulation exists in the source set
      2. Check if the quoted language matches source text
      3. Flag claims that cannot be verified
    """

    def __init__(self):
        self._store = None

    @property
    def store(self):
        if self._store is None:
            self._store = get_store()
        return self._store

    def verify_citations(
        self,
        text: str,
        retrieval_context: Optional[RetrievalContext] = None,
    ) -> List[ClaimVerification]:
        """
        Verify all regulatory citations found in a text against the source set.

        Args:
            text: The generated text to verify
            retrieval_context: The retrieval context used during generation

        Returns:
            List of ClaimVerification results for each citation found
        """
        # Extract all citations from the text
        citations_found = self._extract_citations(text)

        if not citations_found:
            return []

        verifications = []

        # Check each citation against the source set
        for citation in citations_found:
            verification = self._verify_single_citation(citation, retrieval_context)
            verifications.append(verification)

        return verifications

    def verify_section(
        self,
        section_name: str,
        section_text: str,
        retrieval_context: Optional[RetrievalContext] = None,
    ) -> VerificationReport:
        """
        Verify an entire output section.

        Args:
            section_name: Name of the output section (e.g., 'gap_analysis')
            section_text: The full text of the section
            retrieval_context: The retrieval context used during generation

        Returns:
            VerificationReport with verification results for all claims
        """
        claim_verifications = self.verify_citations(section_text, retrieval_context)

        # Count by status
        verified = sum(1 for v in claim_verifications if v.verification_status == VerificationStatus.verified)
        partially = sum(1 for v in claim_verifications if v.verification_status == VerificationStatus.partially_verified)
        unverified = sum(1 for v in claim_verifications if v.verification_status == VerificationStatus.unverified)
        contradicted = sum(1 for v in claim_verifications if v.verification_status == VerificationStatus.contradicted)

        # Determine overall status
        if contradicted > 0:
            overall = VerificationStatus.contradicted
        elif unverified > verified:
            overall = VerificationStatus.unverified
        elif partially > 0 or (unverified > 0 and verified > 0):
            overall = VerificationStatus.partially_verified
        elif verified > 0:
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
            claim_details=claim_verifications,
            overall_status=overall,
        )

    def create_source_attribution(
        self,
        citation: str,
        retrieval_context: Optional[RetrievalContext] = None,
        claim_text: str = "",
    ) -> SourceAttribution:
        """
        Create a SourceAttribution for a specific citation.

        This determines whether the citation came from:
          - Retrieved source material (verified)
          - Live research (verified but time-sensitive)
          - Model knowledge (unverified — needs independent review)

        Args:
            citation: The citation string
            retrieval_context: The retrieval context from the generation step
            claim_text: The text of the claim being attributed

        Returns:
            SourceAttribution with verification status and source info
        """
        # First, check if this citation appears in retrieved sources
        if retrieval_context:
            for result in retrieval_context.get_all_sources():
                meta = result.chunk.metadata
                if meta.citation and self._citations_match(citation, meta.citation):
                    return SourceAttribution(
                        source_type=SourceType.verified_source if result.score > 0.8 else SourceType.retrieved_source,
                        verification_status=VerificationStatus.verified,
                        source_name=meta.source_name,
                        source_citation=meta.citation,
                        source_url=meta.url,
                        source_date=meta.effective_date,
                        retrieved_text=result.chunk.text[:500],
                        confidence=result.score,
                    )

        # Check the knowledge base directly
        try:
            search_results = self.store.query_all_collections(
                query_text=citation,
                n_results_per_collection=2,
            )

            for result_set in search_results:
                results = result_set["results"]
                if not results["ids"] or not results["ids"][0]:
                    continue

                for i, chunk_id in enumerate(results["ids"][0]):
                    meta = results["metadatas"][0][i] if results["metadatas"] else {}
                    doc_text = results["documents"][0][i] if results["documents"] else ""
                    distance = results["distances"][0][i] if results["distances"] else 1.0

                    stored_citation = meta.get("citation", "")
                    if stored_citation and self._citations_match(citation, stored_citation):
                        score = max(0, 1.0 - distance)
                        return SourceAttribution(
                            source_type=SourceType.verified_source if score > 0.8 else SourceType.retrieved_source,
                            verification_status=VerificationStatus.verified if score > 0.7 else VerificationStatus.partially_verified,
                            source_name=meta.get("source_name", ""),
                            source_citation=stored_citation,
                            source_url=meta.get("url") or None,
                            source_date=meta.get("effective_date") or None,
                            retrieved_text=doc_text[:500],
                            confidence=score,
                        )
        except Exception as e:
            logger.warning(f"Verification search failed for citation '{citation}': {e}")

        # Not found in any source — mark as model inference
        return SourceAttribution(
            source_type=SourceType.model_knowledge,
            verification_status=VerificationStatus.unverified,
            source_citation=citation,
            confidence=0.0,
            warning="Not verified from loaded sources. Requires independent review.",
        )

    def _extract_citations(self, text: str) -> List[str]:
        """Extract all regulatory citations from a text."""
        citations = []
        for regex in CITATION_REGEXES:
            matches = regex.findall(text)
            citations.extend(matches)
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for c in citations:
            c_clean = c.strip()
            if c_clean not in seen:
                seen.add(c_clean)
                unique.append(c_clean)
        return unique

    def _verify_single_citation(
        self,
        citation: str,
        retrieval_context: Optional[RetrievalContext] = None,
    ) -> ClaimVerification:
        """Verify a single citation against the source set."""
        attribution = self.create_source_attribution(citation, retrieval_context)

        return ClaimVerification(
            claim_text=citation,
            claimed_citation=citation,
            verification_status=attribution.verification_status,
            supporting_evidence=attribution.retrieved_text,
            evidence_source=attribution.source_name,
            warning=attribution.warning,
        )

    def _citations_match(self, cite_a: str, cite_b: str) -> bool:
        """
        Check if two citations refer to the same regulation.
        Handles minor formatting differences.
        """
        # Normalize both citations
        def normalize(c: str) -> str:
            c = c.lower().strip()
            c = re.sub(r'[§¶]', '', c)         # Remove section symbols first --
                                                # eCFR-ingested citations are stored
                                                # as "45 CFR § 164.312" (space after §)
                                                # while the model writes "§164.312" (no
                                                # space); removing § before collapsing
                                                # whitespace left a stray double-space
                                                # that made every real citation fail to
                                                # match its own source.
            c = re.sub(r'\s+', ' ', c).strip()  # Then collapse whitespace
            c = c.replace('section', 'sec')    # Normalize
            c = c.replace('part ', 'part')     # Normalize
            return c

        a = normalize(cite_a)
        b = normalize(cite_b)

        # Exact match after normalization
        if a == b:
            return True

        # One contains the other
        if a in b or b in a:
            return True

        # Check if the core citation number matches
        # e.g., "45 cfr 164.530" should match "45 cfr 164.530(b)"
        a_core = re.sub(r'\([a-z]\)', '', a)
        b_core = re.sub(r'\([a-z]\)', '', b)
        if a_core == b_core:
            return True

        return False


# Singleton
_verification: Optional[VerificationService] = None


def get_verification_service() -> VerificationService:
    """Get the singleton VerificationService instance."""
    global _verification
    if _verification is None:
        _verification = VerificationService()
    return _verification
