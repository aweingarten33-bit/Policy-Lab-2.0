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
    RetrievalResult, RetrievalContext,
)
from app.services.retrieval.store import get_store

logger = logging.getLogger(__name__)


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
    match = re.search(r"\b([A-Za-z]{2})\s*$", jurisdiction.strip())
    if not match:
        return None
    code = match.group(1).upper()
    return code if code in Jurisdiction.__members__ else None

# ── Query templates for each generation step ──

QUERY_TEMPLATES = {
    "gap_analysis": (
        "Healthcare compliance requirements for {policy_type} policy. "
        "Federal regulations HIPAA OCR requirements 45 CFR 164. "
        "Regulatory standards and mandatory policy elements."
    ),
    "rewritten_policy": (
        "Required policy language for {policy_type} compliance. "
        "Mandatory clauses and provisions for healthcare policy. "
        "Regulatory text for policy sections."
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

        Returns:
            RetrievalContext with all retrieved material ready for prompt injection
        """
        # Build the retrieval query
        query = self._build_query(step_name, policy_text, policy_type, gap_findings)

        # Determine which collections to search
        target_collections = collections or self._get_relevant_collections(step_name, jurisdiction)

        # Build metadata filters
        where_filter = self._build_metadata_filter(jurisdiction)

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

        # Sort by relevance score
        retrieved_chunks.sort(key=lambda r: r.score, reverse=True)

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
    ) -> str:
        """Build a retrieval query for a specific generation step."""
        template = QUERY_TEMPLATES.get(step_name, QUERY_TEMPLATES["gap_analysis"])

        # Fill in the template
        query = template.format(
            policy_type=policy_type or "healthcare compliance",
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

    def _get_relevant_collections(
        self,
        step_name: str,
        jurisdiction: Optional[str] = None,
    ) -> List[str]:
        """Determine which collections are most relevant for a step."""
        # All steps benefit from federal regulations and OCR guidance
        base_collections = ["federal_regulation", "ocr_guidance"]

        # Step-specific collections
        step_collections = {
            "gap_analysis": ["federal_regulation", "ocr_guidance", "enforcement_action", "requirement_pack"],
            "draft_policy": ["example_policy", "policy_template", "policy_clause_library", "federal_regulation"],
            "rewritten_policy": ["federal_regulation", "policy_clause_library", "policy_template"],
            "redline": ["federal_regulation", "policy_clause_library"],
            "adjacent_policies": ["policy_template", "example_policy", "requirement_pack"],
            "remediation_plan": ["enforcement_action", "ocr_guidance", "requirement_pack"],
            "board_summary": ["enforcement_action", "ocr_guidance"],
            "implementation_checklist": ["policy_template", "requirement_pack", "policy_clause_library"],
        }

        collections = step_collections.get(step_name, base_collections)

        # Add state law if a resolvable state code was specified
        if _extract_state_code(jurisdiction):
            collections.append("state_law")

        return collections

    def _build_metadata_filter(self, jurisdiction: Optional[str] = None) -> Optional[Dict]:
        """Build a ChromaDB metadata filter based on jurisdiction."""
        if not jurisdiction:
            return None

        # Search for both federal and the specific jurisdiction
        state_code = _extract_state_code(jurisdiction)
        if state_code:
            return {"jurisdiction": {"$in": ["federal", state_code]}}

        # Jurisdiction was specified but not resolvable to a state code —
        # fall back to federal-only rather than an unfiltered mix of every state.
        return {"jurisdiction": "federal"}

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

        return SourceMetadata(
            source_name=meta_dict.get("source_name", "Unknown"),
            source_type=source_type,
            category=category,
            jurisdiction=jurisdiction,
            effective_date=meta_dict.get("effective_date") or None,
            citation=meta_dict.get("citation") or None,
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
            "═══ RETRIEVED SOURCE MATERIAL ═══",
            "The following source material was retrieved from the curated compliance knowledge base.",
            "You MUST cite these sources when they support your findings.",
            "You MUST NOT invent citations that are not present below.",
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
            if meta.effective_date:
                lines.append(f"Effective Date: {meta.effective_date}")
            if meta.jurisdiction:
                lines.append(f"Jurisdiction: {meta.jurisdiction.value}")
            if meta.url:
                lines.append(f"URL: {meta.url}")
            lines.append(f"Collection: {meta.category.value}")
            lines.append("")
            lines.append(chunk.text)
            lines.append("")

        lines.append("═══ END RETRIEVED SOURCE MATERIAL ═══")
        lines.append("")
        lines.append("IMPORTANT INSTRUCTIONS FOR CITATION:")
        lines.append("- When a finding is supported by the retrieved source material above, cite it using the exact citation provided.")
        lines.append("- When a finding is NOT supported by any retrieved source, clearly mark it as [MODEL INFERENCE — NOT VERIFIED FROM LOADED SOURCES].")
        lines.append("- Do NOT fabricate section numbers, citations, or regulatory text that does not appear in the source material above.")
        lines.append("- If you are unsure whether a regulation exists, say 'Requires independent review' rather than guessing.")

        return "\n".join(lines)


# Singleton
_retriever: Optional[ComplianceRetriever] = None


def get_retriever() -> ComplianceRetriever:
    """Get the singleton ComplianceRetriever instance."""
    global _retriever
    if _retriever is None:
        _retriever = ComplianceRetriever()
    return _retriever
