"""
Compliance Retriever — Retrieves relevant source material before each generation step.

This is the core retrieval engine that makes the compliance action package
source-grounded instead of model-only.

Pipeline:
  1. Build a retrieval query from the policy text + generation step context
  2. Search the curated knowledge base for relevant chunks
  3. Optionally use controlled live research if the KB is insufficient
  4. Format the retrieved context for injection into LLM prompts
  5. Return a RetrievalContext object with all sources and attribution info
"""

import logging
import re
from typing import Optional, List, Dict, Any

from app.services.retrieval.models import (
    SourceChunk, SourceMetadata, SourceType, SourceCategory, Jurisdiction,
    RetrievalResult, RetrievalContext, SourceStatus, resolve_source_status,
)
from app.config import settings
from app.services.retrieval.store import get_store
from app.services.retrieval.sanitize import sanitize_source_text, wrap_untrusted_sources

logger = logging.getLogger(__name__)


# ── Authority tiers ──
#
# What governs, versus what merely discusses what governs. Retrieval ranks by
# tier first and similarity only within a tier, so a regulation is never
# displaced by a guidance document that happens to score higher.
#
# The tiers mirror the distinction verification already enforces: only codified
# law can establish a legal requirement (see _LEGALLY_BINDING_CATEGORIES in
# verification.py). Retrieval now orders by the same rule, so what the model is
# shown first and what it is allowed to treat as binding no longer disagree.
_TIER_LAW = 0          # codified, enforceable, and the answer to "what is required"
_TIER_AGENCY = 1       # what a regulator says about the law — persuasive, not binding
_TIER_REFERENCE = 2    # drafting material: templates, clause libraries, examples

_AUTHORITY_TIERS = {
    SourceCategory.federal_regulation: _TIER_LAW,
    SourceCategory.state_law: _TIER_LAW,
    SourceCategory.federal_guidance: _TIER_AGENCY,
    SourceCategory.ocr_guidance: _TIER_AGENCY,
    SourceCategory.enforcement_action: _TIER_AGENCY,
    SourceCategory.requirement_pack: _TIER_AGENCY,
    SourceCategory.policy_template: _TIER_REFERENCE,
    SourceCategory.policy_clause_library: _TIER_REFERENCE,
    SourceCategory.example_policy: _TIER_REFERENCE,
}


def _authority_tier(category) -> int:
    """Tier for a category. Anything unrecognised sorts last, never first."""
    return _AUTHORITY_TIERS.get(category, _TIER_REFERENCE)


_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "washington d.c.": "DC",
}


def _extract_state_code(jurisdiction: Optional[str]) -> Optional[str]:
    """Pull a 2-letter state code out of a jurisdiction string.

    The frontend sends "City, ST" when a city is entered alongside the state
    (e.g. "Brooklyn, NY"), or just "ST" when only the state is picked. A plain
    Jurisdiction(jurisdiction.upper()) enum lookup only matches the latter —
    any city text silently broke state-law retrieval and fell back to
    federal-only. This matches a trailing 2-letter code either way.
    """
    if not jurisdiction:
        return None

    text = jurisdiction.strip()

    match = re.search(r"\b([A-Za-z]{2})\s*$", text)
    if match:
        code = match.group(1).upper()
        if code in Jurisdiction.__members__:
            return code

    # Also accept a spelled-out state name. The app's own UI sends a 2-letter
    # code, but anything calling the API directly naturally writes "Tennessee",
    # which resolved to nothing -- so state-law retrieval silently fell back to
    # federal-only while the analysis still claimed to cover the state.
    return _STATE_NAMES.get(text.split(",")[-1].strip().lower())

# ── Query templates for each generation step ──
# {industry_name} and {key_regulations} are filled from the selected industry's
# own config (see _build_query) so these aren't hardcoded to healthcare -- a
# "Other/General" gas station policy shouldn't have "HIPAA... 45 CFR 164"
# baked into its retrieval query, federal or live-research alike.

QUERY_TEMPLATES = {
    "gap_analysis": (
        "{industry_name} compliance requirements for {policy_type} policy. "
        "Federal regulations: {key_regulations}. "
        "Regulatory standards and mandatory policy elements."
    ),
    "rewritten_policy": (
        "Required policy language for {policy_type} compliance in {industry_name}. "
        "Mandatory clauses and provisions. Federal regulations: {key_regulations}. "
        "Regulatory text for policy sections."
    ),
    "draft_policy": (
        "{industry_name} policy language and requirements for {policy_type}. "
        "Federal regulations: {key_regulations}. "
        "Real policy examples, templates, and mandatory clauses."
    ),
    "redline": (
        "Regulatory changes and updates for {policy_type}. "
        "Required amendments and policy modifications."
    ),
    "adjacent_policies": (
        "Related healthcare compliance policies and requirements. "
        "Policy frameworks required alongside {policy_type}. "
        "Comprehensive compliance program requirements."
    ),
    "remediation_plan": (
        "Compliance remediation steps and requirements for {policy_type} gaps. "
        "Enforcement actions and corrective action plans. "
        "OCR resolution agreements and remediation requirements."
    ),
    "board_summary": (
        "Healthcare compliance risk assessment and regulatory exposure. "
        "OCR enforcement penalties and fines. "
        "Board governance requirements for compliance programs."
    ),
    "implementation_checklist": (
        "Implementation steps for {policy_type} compliance. "
        "Verification and audit requirements. "
        "Documentation and evidence requirements."
    ),
}


class ComplianceRetriever:
    """
    Retrieves relevant source material from the curated knowledge base
    before each generation step in the compliance action package pipeline.
    """

    def __init__(self):
        self._store = None

    @property
    def store(self):
        if self._store is None:
            self._store = get_store()
        return self._store

    def retrieve_for_step(
        self,
        step_name: str,
        policy_text: str,
        policy_type: str = "",
        jurisdiction: Optional[str] = None,
        gap_findings: Optional[List[str]] = None,
        max_results_per_collection: int = 3,
        collections: Optional[List[str]] = None,
        industry: Optional[str] = None,
    ) -> RetrievalContext:
        """
        Retrieve relevant source material for a specific generation step.

        Args:
            step_name: Which step in the pipeline (gap_analysis, rewritten_policy, etc.)
            policy_text: The original policy text
            policy_type: Identified policy type
            jurisdiction: State/jurisdiction code
            gap_findings: Key findings from previous steps (for downstream steps)
            max_results_per_collection: Max results per collection
            collections: Specific collections to search (None = all relevant)
            industry: Industry slug -- fills the query template so it isn't
                hardcoded to healthcare regardless of what's selected

        Returns:
            RetrievalContext with all retrieved material ready for prompt injection
        """
        # Build the retrieval query
        query = self._build_query(step_name, policy_text, policy_type, gap_findings, industry)

        # Determine which collections to search
        target_collections = collections or self._get_relevant_collections(
            step_name, jurisdiction, industry
        )

        # Build metadata filters
        where_filter = self._build_metadata_filter(jurisdiction, industry)

        # Query each collection
        all_results = self.store.query_all_collections(
            query_text=query,
            n_results_per_collection=max_results_per_collection,
            where=where_filter if where_filter else None,
            collections=target_collections if target_collections else None,
        )

        # Parse results into RetrievalResult objects
        retrieved_chunks = []
        for result_set in all_results:
            col_name = result_set["collection"]
            results = result_set["results"]

            if not results["ids"] or not results["ids"][0]:
                continue

            for i, chunk_id in enumerate(results["ids"][0]):
                try:
                    doc_text = results["documents"][0][i] if results["documents"] else ""
                    meta_dict = results["metadatas"][0][i] if results["metadatas"] else {}
                    distance = results["distances"][0][i] if results["distances"] else 1.0

                    # Convert distance to similarity score (cosine distance -> similarity)
                    score = max(0, 1.0 - distance)

                    # Reconstruct metadata
                    metadata = self._parse_metadata(meta_dict, col_name)

                    chunk = SourceChunk(
                        id=chunk_id,
                        text=doc_text,
                        metadata=metadata,
                    )

                    retrieved_chunks.append(RetrievalResult(
                        chunk=chunk,
                        score=score,
                        query=query,
                    ))
                except Exception as e:
                    logger.warning(f"Failed to parse result {chunk_id}: {e}")
                    continue

        # Codified law first, always. Similarity decides order only within a
        # tier, never across them.
        #
        # Ordering was purely by score, and guidance prose beat regulatory text
        # on nearly every healthcare policy: on a breach-notification policy the
        # OIG General Compliance Program Guidance took the top three slots at
        # ~0.66 while the actual CFR sections came fourth through sixth at
        # ~0.44. Guidance is written in the same register as a policy, so it
        # reads as "similar" to almost any policy; a regulation is written in
        # the register of a regulation. Semantic similarity cannot tell the
        # difference between the document that governs and the document that
        # merely sounds like the question.
        #
        # The model reads its context top-down, so this is why guidance kept
        # being cited on policies it has nothing to say about while the
        # controlling regulation went unmentioned.
        retrieved_chunks.sort(key=lambda r: (_authority_tier(r.chunk.metadata.category), -r.score))
        retrieved_chunks = self._cap_supporting_material(retrieved_chunks)

        # Take top results (limit total to avoid context bloat)
        max_total = 15
        retrieved_chunks = retrieved_chunks[:max_total]

        # Format context for prompt injection
        formatted_context = self._format_context_for_prompt(retrieved_chunks)

        # Build the retrieval context
        context = RetrievalContext(
            query=query,
            retrieved_chunks=retrieved_chunks,
            live_research_used=False,
            total_sources_found=len(retrieved_chunks),
            formatted_context=formatted_context,
        )

        logger.info(
            f"Retrieved {len(retrieved_chunks)} chunks for step '{step_name}' "
            f"from {len(all_results)} collections"
        )

        return context

    def _build_query(
        self,
        step_name: str,
        policy_text: str,
        policy_type: str,
        gap_findings: Optional[List[str]] = None,
        industry: Optional[str] = None,
    ) -> str:
        """Build a retrieval query for a specific generation step."""
        from app.services.industry_config import get_industry, BASELINE_EMPLOYMENT_REGS
        cfg = get_industry(industry or "healthcare")
        industry_name = cfg["name"]
        # Top industry-specific regs plus the baseline employment regs
        # (deduped) -- otherwise a general HR-type request (e.g. an
        # absenteeism policy) under a specific vertical like Hospitals only
        # ever retrieves HIPAA/CMS content and never reaches FMLA/ADA.
        industry_regs = cfg.get("regulations", [])[:3]
        key_regulations = ", ".join(dict.fromkeys(industry_regs + BASELINE_EMPLOYMENT_REGS)) or "applicable federal regulations"

        template = QUERY_TEMPLATES.get(step_name, QUERY_TEMPLATES["gap_analysis"])

        # Fill in the template. Extra kwargs a given template doesn't use
        # (e.g. the legacy per-step templates below) are simply ignored.
        query = template.format(
            policy_type=policy_type or f"{industry_name} compliance",
            industry_name=industry_name,
            key_regulations=key_regulations,
        )

        # Add key phrases from the policy text (first 500 chars)
        policy_excerpt = policy_text[:500].strip()
        if policy_excerpt:
            query += f"\n\nPolicy excerpt for context: {policy_excerpt}"

        # Add gap findings for downstream steps
        if gap_findings:
            findings_text = "; ".join(gap_findings[:5])
            query += f"\n\nKey gaps identified: {findings_text}"

        return query

    # OIG/HCCA compliance-program guidance is written for federal health care
    # program participants. It has nothing to say about a factory's noise
    # policy, and pulling it in produced exactly that: an OSHA analysis citing
    # nursing-facility guidance.
    _GUIDANCE_INDUSTRIES = frozenset({"healthcare", "home_health", "pharmacy"})

    def _get_relevant_collections(
        self,
        step_name: str,
        jurisdiction: Optional[str] = None,
        industry: Optional[str] = None,
    ) -> List[str]:
        """Determine which collections are most relevant for a step."""
        # All steps benefit from federal regulations and agency guidance.
        # federal_guidance carries the OIG/HCCA compliance-program material,
        # which is what actually defines an "effective compliance program" --
        # the CFR never states the seven elements, so a step that only sees
        # regulatory text has nothing to ground program-design findings in.
        base_collections = ["federal_regulation", "federal_guidance", "ocr_guidance"]

        # Step-specific collections
        step_collections = {
            "gap_analysis": ["federal_regulation", "federal_guidance", "ocr_guidance", "enforcement_action", "requirement_pack"],
            "draft_policy": ["example_policy", "policy_template", "policy_clause_library", "federal_regulation", "federal_guidance"],
            "rewritten_policy": ["federal_regulation", "federal_guidance", "policy_clause_library", "policy_template"],
            "redline": ["federal_regulation", "policy_clause_library"],
            "adjacent_policies": ["policy_template", "example_policy", "requirement_pack"],
            "remediation_plan": ["federal_guidance", "enforcement_action", "ocr_guidance", "requirement_pack"],
            "board_summary": ["federal_guidance", "enforcement_action", "ocr_guidance"],
            "implementation_checklist": ["federal_guidance", "policy_template", "requirement_pack", "policy_clause_library"],
        }

        collections = list(step_collections.get(step_name, base_collections))

        # Healthcare compliance-program guidance only for the sectors it
        # actually governs.
        if industry is not None and industry not in self._GUIDANCE_INDUSTRIES:
            collections = [c for c in collections if c != "federal_guidance"]

        # Add state law if a resolvable state code was specified
        if _extract_state_code(jurisdiction):
            collections.append("state_law")

        return collections

    def _allowed_citations(self, industry: Optional[str]) -> Optional[List[str]]:
        """The CFR parts an industry is actually governed by.

        Every industry's regulations live in one `federal_regulation`
        collection, and until now retrieval only ever filtered by
        jurisdiction. The industry shaped the *wording* of the semantic query
        and nothing more -- so nothing stopped a search from returning another
        sector's law.

        It reliably did. An OSHA industrial noise policy came back grounded in
        45 CFR Part 164 (HIPAA) and OIG nursing-facility guidance, because
        "employee training", "records retention" and "notification
        requirements" read as similar text no matter which statute they sit
        in. Semantic similarity cannot tell regulatory domains apart; only a
        filter can.

        Baseline employment law is always included: FMLA and the ADA apply to
        a factory and a hospital alike.
        """
        from app.services.industry_config import (
            BASELINE_EMPLOYMENT_TARGETS, INDUSTRIES, get_industry,
        )

        if not industry or industry not in INDUSTRIES:
            return None  # unknown industry -> no scoping, old behaviour

        citations = {
            f"{title} CFR Part {part}"
            for title, part, _, _ in get_industry(industry).get("ecfr_targets", [])
        }
        # Employment law binds every employer, whatever the sector: a hospital
        # writing an attendance policy needs FMLA and the ADA.
        for title, part, _, _ in BASELINE_EMPLOYMENT_TARGETS:
            citations.add(f"{title} CFR Part {part}")

        return sorted(citations)

    def _build_metadata_filter(
        self,
        jurisdiction: Optional[str] = None,
        industry: Optional[str] = None,
    ) -> Optional[Dict]:
        """Build a ChromaDB metadata filter for jurisdiction and industry."""
        clauses: List[Dict] = []

        if jurisdiction:
            state_code = _extract_state_code(jurisdiction)
            if state_code:
                clauses.append({"jurisdiction": {"$in": ["federal", state_code]}})
            else:
                # Specified but unresolvable -- federal only, rather than an
                # unfiltered mix of every state.
                clauses.append({"jurisdiction": "federal"})

        allowed = self._allowed_citations(industry)
        if allowed:
            # Filter on part_citation, not citation.
            #
            # eCFR content is now stored per section ("45 CFR § 164.404"), so a
            # filter comparing `citation` against a list of part-level strings
            # would match nothing. part_citation carries the part for exactly
            # this purpose.
            #
            # The empty-string arm matters as much as the list: sources that are
            # not CFR parts have no part_citation, and OIG/HCCA compliance-
            # program guidance is the main one. Under the old `citation` filter
            # those chunks could never match either, so selecting any industry
            # silently excluded every guidance document from retrieval -- the
            # material that defines the seven elements of an effective
            # compliance program, absent from every healthcare analysis that
            # named an industry.
            clauses.append({
                "$or": [
                    {"part_citation": {"$in": allowed}},
                    {"part_citation": ""},
                ]
            })

        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    def _cap_supporting_material(self, results: List[RetrievalResult]) -> List[RetrievalResult]:
        """Let guidance in only after the law, and only a little of it.

        Ordering alone is not enough. Three guidance chunks sitting below three
        regulations still put a large block of compliance-program prose in front
        of the model on a policy about something else entirely, and it gets
        cited. Guidance earns a small, fixed allowance -- enough to be useful on
        the policies it genuinely governs, too little to crowd out the
        regulation on the ones it does not.

        The allowance is not a relevance judgement and does not try to be one.
        It is a budget, applied the same way every time.
        """
        allowance = settings.kb_max_guidance_chunks
        kept: List[RetrievalResult] = []
        guidance_used = 0

        for result in results:
            if _authority_tier(result.chunk.metadata.category) == _TIER_LAW:
                kept.append(result)
                continue
            if guidance_used >= allowance:
                continue
            kept.append(result)
            guidance_used += 1

        dropped = len(results) - len(kept)
        if dropped:
            logger.info(
                f"Retrieval: kept {guidance_used} supporting source(s) behind "
                f"{sum(1 for r in kept if _authority_tier(r.chunk.metadata.category) == _TIER_LAW)} "
                f"authority source(s); dropped {dropped} beyond the allowance"
            )
        return kept

    def _parse_metadata(self, meta_dict: Dict[str, Any], collection_name: str) -> SourceMetadata:
        """Parse a metadata dict from ChromaDB into a SourceMetadata object."""
        try:
            category = SourceCategory(meta_dict.get("category", collection_name))
        except ValueError:
            category = SourceCategory.federal_regulation

        try:
            jurisdiction = Jurisdiction(meta_dict.get("jurisdiction", "federal"))
        except ValueError:
            jurisdiction = Jurisdiction.federal

        try:
            source_type = SourceType(meta_dict.get("source_type", "retrieved_source"))
        except ValueError:
            source_type = SourceType.retrieved_source

        # Chunks embedded before source_status existed store "" for it. Left as
        # None so resolve_source_status() derives the standing, rather than
        # coercing an absent value into a claim about it.
        try:
            source_status = SourceStatus(meta_dict.get("source_status") or "") or None
        except ValueError:
            source_status = None

        return SourceMetadata(
            source_name=meta_dict.get("source_name", "Unknown"),
            source_type=source_type,
            category=category,
            jurisdiction=jurisdiction,
            effective_date=meta_dict.get("effective_date") or None,
            publication_date=meta_dict.get("publication_date") or None,
            retrieved_date=meta_dict.get("retrieved_date") or None,
            last_verified_date=meta_dict.get("last_verified_date") or None,
            source_status=source_status,
            citation=meta_dict.get("citation") or None,
            part_citation=meta_dict.get("part_citation") or None,
            url=meta_dict.get("url") or None,
            section=meta_dict.get("section") or None,
            authority=meta_dict.get("authority") or None,
            is_current=bool(meta_dict.get("is_current", True)),
            chunk_index=int(meta_dict.get("chunk_index", 0)),
            total_chunks=int(meta_dict.get("total_chunks", 1)),
            collection=meta_dict.get("collection", collection_name),
        )

    def _format_context_for_prompt(self, results: List[RetrievalResult]) -> str:
        """
        Format retrieved chunks into a structured context string
        ready for injection into LLM prompts.

        Each chunk is clearly delimited with its source attribution
        so the model can cite specific sources in its output.
        """
        if not results:
            return "No relevant source material found in the knowledge base."

        lines = [
            "The following was retrieved from the curated compliance knowledge base.",
            "You MUST cite these sources when they support your findings.",
            "You MUST NOT invent citations that are not present below.",
            "",
            "Sources are listed in order of authority, not relevance. Codified law "
            "comes first and is what a finding should cite. Agency guidance follows "
            "it and is context: cite guidance only where the finding is genuinely "
            "about it, never as the authority for a legal requirement, and never in "
            "place of a regulation that governs the same point.",
            "",
            "Being retrieved is not a reason to cite something. A source that has "
            "nothing to say about this policy should not appear in your output at "
            "all — retrieval casts a wide net and it is your job to ignore what "
            "does not apply.",
            "",
        ]

        for i, result in enumerate(results, 1):
            chunk = result.chunk
            meta = chunk.metadata

            lines.append(f"─── Source {i} ───")
            lines.append(f"Source Name: {meta.source_name}")
            if meta.citation:
                lines.append(f"Citation: {meta.citation}")
            if meta.authority:
                lines.append(f"Authority: {meta.authority}")
            # Each date is labelled for what it actually is. A single "Date:"
            # line let the model read a publication or download date as the
            # date the rule took effect and then say so in a finding.
            if meta.effective_date:
                lines.append(f"Effective Date (stated by the source): {meta.effective_date}")
            if meta.publication_date:
                lines.append(f"Publication Date: {meta.publication_date}")
            if meta.last_verified_date:
                lines.append(f"Text Last Confirmed Against Publisher: {meta.last_verified_date}")
            status = resolve_source_status(meta)
            lines.append(f"Source Status: {status.value}")
            if status is not SourceStatus.current_verified:
                lines.append(
                    "NOTE: this source's standing as current law is not established. "
                    "Do not state anything from it as a present legal requirement."
                )
            if meta.jurisdiction:
                lines.append(f"Jurisdiction: {meta.jurisdiction.value}")
            if meta.url:
                lines.append(f"URL: {meta.url}")
            lines.append(f"Collection: {meta.category.value}")
            lines.append("")
            # Defanged before it reaches the prompt: a document reaching this
            # point may be attacker-controlled (KB ingest, or a spoofed page
            # pulled by live research), and raw injection into model context is
            # the classic indirect prompt-injection path.
            lines.append(sanitize_source_text(chunk.text))
            lines.append("")

        lines.append("")
        lines.append("IMPORTANT INSTRUCTIONS FOR CITATION:")
        lines.append("- When a finding is supported by the retrieved source material above, cite it using the exact citation provided.")
        lines.append("- When a finding is NOT supported by any retrieved source, clearly mark it as [MODEL INFERENCE — NOT VERIFIED FROM LOADED SOURCES].")
        lines.append("- Do NOT fabricate section numbers, citations, or regulatory text that does not appear in the source material above.")
        lines.append("- If you are unsure whether a regulation exists, say 'Requires independent review' rather than guessing.")

        return wrap_untrusted_sources("\n".join(lines))


# Singleton
_retriever: Optional[ComplianceRetriever] = None


def get_retriever() -> ComplianceRetriever:
    """Get the singleton ComplianceRetriever instance."""
    global _retriever
    if _retriever is None:
        _retriever = ComplianceRetriever()
    return _retriever
