"""
Orchestration Engine v3 — Source-Grounded Compliance Intelligence System.

Chains multiple LLM calls together with retrieval-first architecture:
  1. Retrieve relevant source material from the curated knowledge base
  2. Optionally augment with controlled live research
  3. Generate each output with retrieved context injected
  4. Verify claims against source material after generation
  5. Apply source attribution to every output

Pipeline (phased parallel execution):
  Phase 0 — Step 1: Gap Analysis   (must be first — everything depends on it)
  Phase 1 — Steps 2 + 4 in parallel: Rewritten Policy + Adjacent Policies
  Phase 2 — Steps 3 + 5 in parallel: Redline (needs rewrite) + Remediation Plan (needs adjacent)
  Phase 3 — Steps 6 + 7 in parallel: Board Summary + Checklist (both need remediation + rewrite)

FIXES APPLIED:
  generate_full_package() now runs steps in parallel where dependencies allow.
  Before the fix, all 7 steps ran sequentially. With 3-4 LLM calls each
  (retrieve → generate → verify) and 30-45s per step, the full package took
  3-5 MINUTES. After the fix, the 3 parallel phases cut that to ~90 seconds.

  The streaming version (generate_full_package_stream) was already doing
  phased parallel execution and is unchanged.
"""

import uuid
import logging
import asyncio
from datetime import datetime
from typing import Optional, List, Callable, Dict

from app.config import settings
from app.models.schemas import (
    ComplianceActionPackage, PackageStatus, AnalysisResult,
    RewrittenPolicy, RedlineChange,
    RemediationPlan, BoardSummary, ImplementationChecklist,
    SourceAttribution, SourceType, VerificationStatus,
)
from app.services.llm_service import analyze_policy
from app.services.retrieval.retriever import get_retriever, ComplianceRetriever
from app.services.retrieval.verification import get_verification_service, VerificationService
from app.services.retrieval.live_research import get_live_research_service, LiveResearchService
from app.services.retrieval.models import RetrievalContext

logger = logging.getLogger(__name__)


class PackageOrchestrator:
    """
    Orchestrates the generation of all 7 Compliance Action Package outputs
    with retrieval-first, verification-after architecture.

    Pipeline per step:
      1. Retrieve relevant source material from the curated knowledge base
      2. (Optional) Augment with controlled live research if KB is insufficient
      3. Generate the output with retrieved context injected into the prompt
      4. Verify claims against source material
      5. Apply source attribution labels

    Every output carries:
      - source_attributions: List of SourceAttribution for cited material
      - retrieved_sources_used: Names of KB sources that contributed
      - live_research_used: Whether live research was needed
    """

    def __init__(self):
        self._status_callbacks: List[Callable] = []
        self._retriever: Optional[ComplianceRetriever] = None
        self._verification: Optional[VerificationService] = None
        self._live_research: Optional[LiveResearchService] = None

    @property
    def retriever(self) -> ComplianceRetriever:
        if self._retriever is None:
            self._retriever = get_retriever()
        return self._retriever

    @property
    def verification(self) -> VerificationService:
        if self._verification is None:
            self._verification = get_verification_service()
        return self._verification

    @property
    def live_research(self) -> LiveResearchService:
        if self._live_research is None:
            self._live_research = get_live_research_service()
        return self._live_research

    def on_status_update(self, callback: Callable):
        """Register a callback for status updates (for SSE/streaming)."""
        self._status_callbacks.append(callback)

    async def _notify_status(self, package_id: str, status: str, output_name: str = ""):
        """Notify all registered callbacks of a status change."""
        for cb in self._status_callbacks:
            try:
                await cb(package_id, status, output_name)
            except Exception as e:
                logger.warning(f"Status callback error: {e}")

    async def _retrieve_for_step(
        self,
        step_name: str,
        policy_text: str,
        policy_type: str = "",
        industry: Optional[str] = None,
        jurisdiction: Optional[str] = None,
        gap_findings: Optional[List[str]] = None,
        enable_live_research: bool = False,
    ) -> RetrievalContext:
        """
        Retrieve relevant source material for a generation step.

        1. Search the curated knowledge base
        2. Optionally augment with live research if KB is insufficient
        """
        logger.info(f"[{step_name}] Retrieving source material from knowledge base...")

        context = self.retriever.retrieve_for_step(
            step_name=step_name,
            policy_text=policy_text,
            policy_type=policy_type,
            jurisdiction=jurisdiction,
            gap_findings=gap_findings,
            industry=industry,
        )

        logger.info(
            f"[{step_name}] Retrieved {context.total_sources_found} chunks from KB "
            f"(live research: {'requested' if enable_live_research else 'off'})"
        )

        # Optionally augment with live research
        if enable_live_research:
            context = await self.live_research.augment_retrieval_context(
                context=context,
                policy_type=policy_type,
                industry=industry,
                jurisdiction=jurisdiction,
            )
            if context.live_research_used:
                logger.info(f"[{step_name}] Augmented with live research: {len(context.live_research_results)} results")

        return context

    def _verify_and_attribute(
        self,
        text: str,
        retrieval_context: RetrievalContext,
        section_name: str,
    ) -> tuple:
        """
        Verify claims and create source attributions.

        Returns:
            (source_attributions, retrieved_sources, live_research_used, verification_summary)
        """
        # Verify citations against source set
        report = self.verification.verify_section(
            section_name=section_name,
            section_text=text,
            retrieval_context=retrieval_context,
        )

        # Build source attributions
        attributions = []
        for claim in report.claim_details:
            attribution = self.verification.create_source_attribution(
                citation=claim.claimed_citation or claim.claim_text,
                retrieval_context=retrieval_context,
                claim_text=claim.claim_text,
            )
            attributions.append(attribution)

        # If no specific claims were found, create an overall attribution
        if not attributions:
            if retrieval_context.total_sources_found > 0:
                attributions.append(SourceAttribution(
                    source_type=SourceType.retrieved_source,
                    verification_status=VerificationStatus.partially_verified,
                    confidence=0.7,
                    warning="Analysis supported by retrieved source material but specific claims could not be individually verified.",
                ))
            else:
                attributions.append(SourceAttribution(
                    source_type=SourceType.model_knowledge,
                    verification_status=VerificationStatus.unverified,
                    confidence=0.0,
                    warning="No source material found in the knowledge base. All findings are model inference only and must be independently verified.",
                ))

        # Collect source names
        retrieved_sources = retrieval_context.get_source_names()

        # Build verification summary
        verified = report.verified_claims
        total = max(report.total_claims, 1)
        unverified = report.unverified_claims

        if report.total_claims == 0:
            verification_summary = (
                f"No specific citations detected for verification. "
                f"Analysis based on {retrieval_context.total_sources_found} retrieved source chunks. "
                f"All findings should be independently verified."
            )
        else:
            verification_summary = (
                f"Verification: {verified}/{total} citations verified against source material. "
                f"{unverified} citations could not be verified and require independent review."
            )

        return attributions, retrieved_sources, retrieval_context.live_research_used, verification_summary

    async def generate_full_package(
        self,
        text: str,
        file_name: Optional[str] = None,
        industry: Optional[str] = None,
        jurisdiction: Optional[str] = None,
        requested_outputs: Optional[List[str]] = None,
        enable_live_research: bool = False,
    ) -> ComplianceActionPackage:
        """
        Generate the complete Compliance Action Package with retrieval-first architecture.

        Args:
            text: The policy text to analyze
            file_name: Original file name
            industry: Industry vertical slug (healthcare, home_health, other)
            jurisdiction: State/jurisdiction code
            requested_outputs: Which outputs to generate. None = all outputs.
            enable_live_research: Whether to use controlled live research

        Returns:
            ComplianceActionPackage with all requested outputs, source-attributed
        """
        package_id = str(uuid.uuid4())[:8]
        # Active outputs only — 90-day plan, exec summary, checklist, and adjacent policies were retired.
        all_outputs = requested_outputs or [
            "gap_analysis",
        ]

        # Track overall KB sources and live research usage
        all_kb_sources: List[str] = []
        all_kb_source_urls: Dict[str, str] = {}
        any_live_research = False
        total_unverified = 0

        package = ComplianceActionPackage(
            package_id=package_id,
            created_at=datetime.now().isoformat(),
            source_file_name=file_name,
            policy_type="",
            jurisdiction=jurisdiction,
            gap_analysis=AnalysisResult(
                policy_type="Pending",
                audit_ready_summary="Analysis in progress...",
            ),
            status=PackageStatus.pending,
            completed_outputs=[],
        )

        # ── Step 1: Gap Analysis (always required — foundation for all other outputs) ──
        if "gap_analysis" in all_outputs:
            try:
                logger.info(f"[{package_id}] Step 1/1: Gap Analysis (with retrieval)")
                package.status = PackageStatus.retrieving
                await self._notify_status(package_id, "retrieving", "gap_analysis")

                # Retrieve source material
                retrieval_ctx = await self._retrieve_for_step(
                    step_name="gap_analysis",
                    policy_text=text,
                    industry=industry,
                    jurisdiction=jurisdiction,
                    enable_live_research=enable_live_research,
                )

                package.status = PackageStatus.analyzing
                await self._notify_status(package_id, "analyzing", "gap_analysis")

                # Generate with retrieved context
                gap_result = await analyze_policy(
                    text=text,
                    file_name=file_name,
                    industry=industry,
                    jurisdiction=jurisdiction,
                    retrieval_context=retrieval_ctx,
                )
                package.gap_analysis = gap_result
                package.policy_type = gap_result.policy_type

                # Verify and attribute
                package.status = PackageStatus.verifying
                attributions, sources, live_used, ver_summary = self._verify_and_attribute(
                    text=gap_result.audit_ready_summary + " " + " ".join(
                        f.finding for f in gap_result.gap_table[:5]
                    ),
                    retrieval_context=retrieval_ctx,
                    section_name="gap_analysis",
                )

                gap_result.source_attributions = attributions
                gap_result.retrieved_sources_used = sources
                gap_result.live_research_used = live_used
                gap_result.verification_summary = ver_summary

                all_kb_sources.extend(s for s in sources if s not in all_kb_sources)
                all_kb_source_urls.update(retrieval_ctx.get_source_url_map())
                if live_used:
                    any_live_research = True
                total_unverified += sum(1 for a in attributions if a.verification_status == VerificationStatus.unverified)

                package.completed_outputs.append("gap_analysis")
                logger.info(f"[{package_id}] Gap analysis complete: {len(gap_result.gap_table)} findings, {len(sources)} sources used")
            except Exception as e:
                logger.error(f"[{package_id}] Gap analysis failed: {e}")
                package.status = PackageStatus.failed
                package.error_message = f"Gap analysis failed: {str(e)}"
                return package

        # Build gap findings for downstream steps
        gap_findings = []
        if package.gap_analysis:
            gap_findings = [
                f"{row.clause}: {row.finding[:100]}"
                for row in package.gap_analysis.gap_table
                if row.status != "compliant"
            ]

        # Rewrite + redline phases retired (May 2026) — gap analysis is the only output.
        # gap_findings stays computed above so any future downstream work can use it.

        # ── Final: Set package-level metadata ──
        package.kb_sources_used = all_kb_sources if all_kb_sources else None
        package.kb_source_urls = all_kb_source_urls if all_kb_source_urls else None
        package.live_research_used = any_live_research
        package.unverified_claim_count = total_unverified

        if total_unverified > 0:
            package.verification_overall = (
                f"{total_unverified} claims could not be verified from loaded sources. "
                f"These require independent review by qualified compliance counsel."
            )
        elif all_kb_sources:
            package.verification_overall = (
                f"All identified citations verified against {len(all_kb_sources)} source(s) in the knowledge base. "
                f"Findings should still be independently confirmed."
            )
        else:
            package.verification_overall = (
                "No source material was available in the knowledge base. "
                "All findings are model inference only and MUST be independently verified."
            )

        package.status = PackageStatus.complete
        logger.info(
            f"[{package_id}] Package complete: {len(package.completed_outputs)}/1 outputs — "
            f"KB sources: {len(all_kb_sources)}, Live research: {any_live_research}, "
            f"Unverified claims: {total_unverified}"
        )
        return package

    async def generate_full_package_stream(
        self,
        text: str,
        file_name: Optional[str] = None,
        industry: Optional[str] = None,
        jurisdiction: Optional[str] = None,
        requested_outputs: Optional[List[str]] = None,
        enable_live_research: bool = False,
    ):
        """
        Streaming version: yields ComplianceActionPackage snapshots as steps complete.

        Phase 0 — Gap analysis: yields immediately on completion (~30s)
        Phase 1 — Rewrite
        Phase 2 — Redline (depends on rewritten policy from Phase 1)
        """
        package_id = str(uuid.uuid4())[:8]
        # Active outputs only — 90-day plan, exec summary, checklist, and adjacent policies were retired.
        all_outputs = requested_outputs or [
            "gap_analysis",
        ]

        all_kb_sources: List[str] = []
        all_kb_source_urls: Dict[str, str] = {}
        any_live_research = False
        total_unverified = 0

        package = ComplianceActionPackage(
            package_id=package_id,
            created_at=datetime.now().isoformat(),
            source_file_name=file_name,
            policy_type="",
            jurisdiction=jurisdiction,
            gap_analysis=AnalysisResult(
                policy_type="Pending",
                audit_ready_summary="Analysis in progress...",
            ),
            status=PackageStatus.pending,
            completed_outputs=[],
        )

        # ── Phase 0: Gap Analysis — stream first ──
        try:
            retrieval_ctx = await self._retrieve_for_step(
                step_name="gap_analysis",
                policy_text=text,
                industry=industry,
                jurisdiction=jurisdiction,
                enable_live_research=enable_live_research,
            )
            gap_result = await analyze_policy(
                text=text,
                file_name=file_name,
                industry=industry,
                jurisdiction=jurisdiction,
                retrieval_context=retrieval_ctx,
            )
            package.gap_analysis = gap_result
            package.policy_type = gap_result.policy_type
            attributions, sources, live_used, ver_summary = self._verify_and_attribute(
                text=gap_result.audit_ready_summary,
                retrieval_context=retrieval_ctx,
                section_name="gap_analysis",
            )
            gap_result.source_attributions = attributions
            gap_result.retrieved_sources_used = sources
            gap_result.live_research_used = live_used
            gap_result.verification_summary = ver_summary
            all_kb_sources.extend(s for s in sources if s not in all_kb_sources)
            all_kb_source_urls.update(retrieval_ctx.get_source_url_map())
            if live_used:
                any_live_research = True
            total_unverified += sum(1 for a in attributions if a.verification_status == VerificationStatus.unverified)
            package.completed_outputs.append("gap_analysis")
            package.status = PackageStatus.analyzing
            package.kb_sources_used = all_kb_sources if all_kb_sources else None
            package.kb_source_urls = all_kb_source_urls if all_kb_source_urls else None
            logger.info(f"[{package_id}] Gap analysis complete — streaming first result")
        except Exception as e:
            logger.error(f"[{package_id}] Gap analysis failed: {e}")
            package.status = PackageStatus.failed
            package.error_message = f"Gap analysis failed: {str(e)}"
            yield package
            return

        yield package  # ← user sees results here, ~30s in

        gap_findings = [
            f"{row.clause}: {row.finding[:100]}"
            for row in package.gap_analysis.gap_table
            if row.status != "compliant"
        ]

        # Rewrite + redline streaming phases retired (May 2026). Gap analysis was
        # already yielded above as the streaming package; no further phases needed.

        package.kb_sources_used = all_kb_sources if all_kb_sources else None
        package.kb_source_urls = all_kb_source_urls if all_kb_source_urls else None
        package.live_research_used = any_live_research
        package.unverified_claim_count = total_unverified
        package.status = PackageStatus.complete
        logger.info(f"[{package_id}] Streaming package complete: {len(package.completed_outputs)}/1 outputs")
        yield package


# Singleton
_orchestrator: Optional[PackageOrchestrator] = None


def get_orchestrator() -> PackageOrchestrator:
    """Get the singleton PackageOrchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PackageOrchestrator()
    return _orchestrator
