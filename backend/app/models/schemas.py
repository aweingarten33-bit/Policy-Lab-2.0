"""
Pydantic models for the Policy Gap Analyzer API — Phase 3.
Source-Grounded Compliance Intelligence System.

Defines the complete Compliance Action Package with all 7 outputs,
plus source attribution, verification status, and knowledge base management.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from enum import Enum
from datetime import datetime


# ── Enums ──

class GapStatus(str, Enum):
    compliant = "compliant"
    partial = "partial"
    gap = "gap"
    missing = "missing"


class RiskLevel(str, Enum):
    critical = "critical"
    high = "high"
    moderate = "moderate"
    low = "low"
    compliant = "compliant"


class ExportFormat(str, Enum):
    docx = "docx"
    pdf = "pdf"


class PackageStatus(str, Enum):
    pending = "pending"
    retrieving = "retrieving"
    analyzing = "analyzing"
    rewriting = "rewriting"
    generating_redline = "generating_redline"
    finding_adjacent = "finding_adjacent"
    building_remediation = "building_remediation"
    drafting_board_summary = "drafting_board_summary"
    building_checklist = "building_checklist"
    verifying = "verifying"
    complete = "complete"
    failed = "failed"


# ── Source Attribution Enums ──

class SourceType(str, Enum):
    """Origin of a claim or piece of information."""
    model_knowledge = "model_knowledge"        # LLM training data — NOT verified
    retrieved_source = "retrieved_source"      # From curated internal knowledge base
    live_research = "live_research"            # From controlled web research
    verified_source = "verified_source"        # Cross-checked against source material


class VerificationStatus(str, Enum):
    """Verification result for a claim."""
    verified = "verified"                      # Confirmed against source material
    partially_verified = "partially_verified"  # Some support found, not exact match
    unverified = "unverified"                  # No supporting source found
    contradicted = "contradicted"              # Source material contradicts the claim


# ── Source Attribution Model ──

class SourceAttribution(BaseModel):
    """
    Attribution for a single claim or finding.
    Every major output in the compliance action package carries one of these.
    """
    source_type: SourceType = Field(
        SourceType.model_knowledge,
        description="Where this information came from"
    )
    verification_status: VerificationStatus = Field(
        VerificationStatus.unverified,
        description="Whether this claim has been verified against source material"
    )
    source_name: Optional[str] = Field(
        None,
        description="Name of the source document (if retrieved/verified)"
    )
    source_citation: Optional[str] = Field(
        None,
        description="Formal citation (e.g., '45 CFR §164.530(b)')"
    )
    source_url: Optional[str] = Field(
        None,
        description="URL to the source material"
    )
    source_date: Optional[str] = Field(
        None,
        description="Date of the source material"
    )
    retrieved_text: Optional[str] = Field(
        None,
        description="The actual retrieved source text that supports this claim (for verification and evidence display)"
    )
    confidence: float = Field(
        0.5,
        description="Confidence in this attribution (0-1)"
    )
    warning: Optional[str] = Field(
        None,
        description="Warning message if the claim cannot be verified"
    )


# ── Core Models (Phase 1) ──

class AnalysisRequest(BaseModel):
    """Request body for policy gap analysis."""
    text: str = Field(..., min_length=50, description="The policy text to analyze")
    file_name: Optional[str] = Field(None, description="Original file name, if uploaded")
    industry: Optional[str] = Field(
        "healthcare",
        description="Industry vertical: 'healthcare', 'education', 'hoa'. Determines which regulations are applied."
    )
    jurisdiction: Optional[str] = Field(
        None,
        description="State/jurisdiction code (e.g., 'NY', 'CA'). If provided, adds state-specific regulations."
    )
    regulation_scope: Optional[List[str]] = Field(
        None,
        description="Specific regulation families to check (e.g., ['HIPAA', '42-CFR-2']). If omitted, checks ALL applicable regulations."
    )
    enable_live_research: bool = Field(
        False,
        description="Whether to use controlled live research when the KB is insufficient"
    )


class ExportRequest(BaseModel):
    """Request body for generating an export file."""
    result: "AnalysisResult"
    file_name: Optional[str] = Field(None, description="Original file name for the report header")
    export_format: ExportFormat = Field(ExportFormat.docx, description="Export format: docx or pdf")


class GapRow(BaseModel):
    """A single gap finding row."""
    clause: str = Field(..., description="Policy section or topic area")
    regulations: List[str] = Field(default_factory=list, description="Applicable regulation citations")
    status: GapStatus = Field(..., description="Compliance status")
    risk_level: Optional[RiskLevel] = Field(None, description="Risk level for OCR/audit context")
    current_state: Optional[str] = Field(
        None,
        description="What the policy currently says about this topic (direct quote or close paraphrase)"
    )
    finding: str = Field(..., description="What is wrong or missing")
    suggested_language: str = Field(..., description="Ready-to-paste policy text to remediate the gap")
    citation: str = Field(..., description="Full citation with source and year")
    remediation_priority: Optional[str] = Field(
        None,
        description="Immediate / 30-day / 90-day / Next-review remediation timeline"
    )
    oig_element: Optional[str] = Field(
        None,
        description="OIG GCPG element this finding relates to (1–7), e.g. '3 — Training & Education'"
    )
    # ── Source Attribution (Phase 3) ──
    source_attribution: Optional[SourceAttribution] = Field(
        None,
        description="Source attribution for this finding"
    )


class AnalysisResult(BaseModel):
    """Complete analysis result returned by the LLM and used for export."""
    policy_type: str = Field(..., description="Identified type of policy")
    regulations_applied: List[str] = Field(default_factory=list, description="Every regulation/statute/guidance checked")
    last_updated_note: Optional[str] = Field(None, description="Note about recent regulatory updates")
    critical_count: int = Field(0, description="Number of critical findings")
    gap_count: int = Field(0, description="Number of gap findings")
    partial_count: int = Field(0, description="Number of partial findings")
    compliant_count: int = Field(0, description="Number of compliant areas")
    compliance_score: Optional[float] = Field(
        None,
        description="Overall compliance score as a percentage (0–100). Compliant=1.0pt, Partial=0.5pt, Gap/Critical=0pt."
    )
    priority_findings: List[str] = Field(default_factory=list, description="Top critical findings with citations")
    gap_table: List[GapRow] = Field(default_factory=list, description="Detailed gap analysis rows")
    audit_ready_summary: str = Field(..., description="Executive summary for board/regulator consumption")
    scope: Optional[str] = Field(None, description="Scope of the analysis")
    methodology: Optional[str] = Field(None, description="Methodology description for the report")
    # ── Source Attribution (Phase 3) ──
    source_attributions: Optional[List[SourceAttribution]] = Field(
        None,
        description="Source attributions for all citations in this analysis"
    )
    verification_summary: Optional[str] = Field(
        None,
        description="Summary of verification results for this analysis"
    )
    retrieved_sources_used: Optional[List[str]] = Field(
        None,
        description="Names of sources from the knowledge base used in this analysis"
    )
    live_research_used: bool = Field(
        False,
        description="Whether live research was used for this analysis"
    )
    review_frequency: Optional[str] = Field(
        None,
        description="Recommended review frequency: Annual, Bi-Annual, Quarterly, or Immediate Revision Required"
    )
    next_review_recommended: Optional[str] = Field(
        None,
        description="Specific recommended date or timeframe for the next policy review, e.g. 'April 2026 (within 12 months)'"
    )


# ── Phase 2: Complete Compliance Action Package ──

class RedlineChange(BaseModel):
    """A single change in the redline document."""
    type: str = Field(..., description="added, removed, or modified")
    original_text: Optional[str] = Field(None, description="Original text (for removed/unchanged)")
    revised_text: Optional[str] = Field(None, description="Revised text (for added/unchanged)")
    section: Optional[str] = Field(None, description="Section heading this change belongs to")
    regulation_ref: Optional[str] = Field(None, description="Regulation citation that triggered this change")
    # ── Source Attribution (Phase 3) ──
    source_attribution: Optional[SourceAttribution] = Field(
        None,
        description="Source attribution for this change"
    )


class RewrittenPolicySection(BaseModel):
    """A section of the rewritten policy."""
    section_title: str = Field(..., description="Section heading")
    original_text: str = Field(..., description="Original policy text for this section")
    rewritten_text: str = Field(..., description="Fully rewritten compliant text")
    changes_summary: str = Field(..., description="Brief summary of what was changed and why")
    regulation_refs: List[str] = Field(default_factory=list, description="Regulations addressed by this section")
    # ── Source Attribution (Phase 3) ──
    source_attribution: Optional[SourceAttribution] = Field(
        None,
        description="Source attribution for this section's rewrite"
    )


class RewrittenPolicy(BaseModel):
    """Complete rewritten version of the policy."""
    policy_title: str = Field(..., description="Title of the rewritten policy")
    effective_date: Optional[str] = Field(None, description="Suggested effective date")
    version_note: str = Field(..., description="Version/revision note explaining changes")
    sections: List[RewrittenPolicySection] = Field(default_factory=list, description="Rewritten policy sections")
    full_text: str = Field(..., description="Complete rewritten policy as a single document")
    change_summary: str = Field(..., description="Overall summary of all changes made")
    # ── Source Attribution (Phase 3) ──
    source_attributions: Optional[List[SourceAttribution]] = Field(
        None,
        description="Source attributions for all citations in the rewritten policy"
    )
    retrieved_sources_used: Optional[List[str]] = Field(
        None,
        description="Names of sources from the knowledge base used"
    )
    live_research_used: bool = Field(
        False,
        description="Whether live research was used"
    )



class RemediationTask(BaseModel):
    """A single task in the remediation plan."""
    task_id: str = Field(..., description="Task identifier (e.g., R-001)")
    title: str = Field(..., description="Short title for the task")
    description: str = Field(..., description="Detailed description of what needs to be done")
    phase: str = Field(..., description="Phase 1 (Days 1-30), Phase 2 (Days 31-60), or Phase 3 (Days 61-90)")
    risk_level: str = Field(..., description="Critical, High, Moderate, or Low")
    responsible_party: str = Field(..., description="Suggested role responsible (e.g., 'Privacy Officer', 'CISO')")
    deliverable: str = Field(..., description="What the completed task produces (document, training, etc.)")
    regulation_refs: List[str] = Field(default_factory=list, description="Regulations this task addresses")
    dependencies: List[str] = Field(default_factory=list, description="Task IDs that must be completed first")
    verification_method: str = Field(..., description="How to verify the task is properly completed")
    # ── Source Attribution (Phase 3) ──
    source_attribution: Optional[SourceAttribution] = Field(
        None,
        description="Source attribution for this task's regulatory references"
    )


class RemediationPhase(BaseModel):
    """A phase in the 90-day remediation plan."""
    phase_number: int = Field(..., description="1, 2, or 3")
    phase_name: str = Field(..., description="Phase name")
    time_range: str = Field(..., description="e.g., 'Days 1-30'")
    objective: str = Field(..., description="What this phase accomplishes")
    tasks: List[RemediationTask] = Field(default_factory=list, description="Tasks in this phase")


class RemediationPlan(BaseModel):
    """Complete 90-day remediation plan."""
    plan_title: str = Field(..., description="Title for the remediation plan")
    total_tasks: int = Field(..., description="Total number of remediation tasks")
    critical_tasks_first_30: int = Field(..., description="Critical tasks that must be done in first 30 days")
    phases: List[RemediationPhase] = Field(default_factory=list, description="Remediation phases")
    success_criteria: str = Field(..., description="How to measure overall success of the plan")
    resource_requirements: str = Field(..., description="Staff, budget, and tool requirements")
    # ── Source Attribution (Phase 3) ──
    source_attributions: Optional[List[SourceAttribution]] = Field(
        None,
        description="Source attributions for all citations in the plan"
    )
    retrieved_sources_used: Optional[List[str]] = Field(
        None,
        description="Names of sources from the knowledge base used"
    )
    live_research_used: bool = Field(
        False,
        description="Whether live research was used"
    )


class BoardSummary(BaseModel):
    """Board-ready executive summary."""
    headline: str = Field(..., description="One-line headline for board attention")
    overall_status: str = Field(..., description="e.g., 'Significant Gaps Identified', 'Substantially Compliant'")
    risk_summary: str = Field(..., description="2-3 sentence risk assessment")
    key_findings: List[str] = Field(default_factory=list, description="Top 3-5 findings for board awareness")
    regulatory_exposure: str = Field(..., description="Potential regulatory/financial exposure")
    remediation_status: str = Field(..., description="Status of remediation planning")
    recommended_actions: List[str] = Field(default_factory=list, description="2-3 board-level recommended actions")
    budget_impact: Optional[str] = Field(None, description="Estimated budget impact if known")
    next_review_date: Optional[str] = Field(None, description="Recommended date for next board review")
    prepared_by: Optional[str] = Field(None, description="Who prepared this summary")
    prepared_date: Optional[str] = Field(None, description="Date prepared")
    # ── Source Attribution (Phase 3) ──
    source_attributions: Optional[List[SourceAttribution]] = Field(
        None,
        description="Source attributions for claims in the board summary"
    )
    retrieved_sources_used: Optional[List[str]] = Field(
        None,
        description="Names of sources from the knowledge base used"
    )
    live_research_used: bool = Field(
        False,
        description="Whether live research was used"
    )


class ChecklistItem(BaseModel):
    """A single item in the implementation checklist."""
    item_id: str = Field(..., description="Checklist item identifier (e.g., C-001)")
    action: str = Field(..., description="What needs to be done")
    category: str = Field(..., description="Category: Policy, Training, Technical, Administrative, Documentation")
    priority: str = Field(..., description="Critical, High, Moderate, Low")
    responsible_role: str = Field(..., description="Who should do this")
    deadline: str = Field(..., description="Suggested deadline (e.g., 'Week 1', 'Day 30', 'Day 60')")
    regulation_ref: str = Field(..., description="Regulation that requires this action")
    verification: str = Field(..., description="How to confirm this is done correctly")
    evidence_needed: str = Field(..., description="What evidence/documents to retain")
    status: str = Field(default="pending", description="pending, in_progress, complete")
    # ── Source Attribution (Phase 3) ──
    source_attribution: Optional[SourceAttribution] = Field(
        None,
        description="Source attribution for this item's regulation reference"
    )


class ImplementationChecklist(BaseModel):
    """Complete implementation checklist."""
    total_items: int = Field(..., description="Total checklist items")
    critical_items: int = Field(..., description="Critical priority items")
    categories: List[str] = Field(default_factory=list, description="All categories represented")
    items: List[ChecklistItem] = Field(default_factory=list, description="Checklist items grouped by category")
    completion_timeline: str = Field(..., description="Expected completion timeline overview")
    # ── Source Attribution (Phase 3) ──
    source_attributions: Optional[List[SourceAttribution]] = Field(
        None,
        description="Source attributions for all citations in the checklist"
    )
    retrieved_sources_used: Optional[List[str]] = Field(
        None,
        description="Names of sources from the knowledge base used"
    )
    live_research_used: bool = Field(
        False,
        description="Whether live research was used"
    )


class ComplianceActionPackage(BaseModel):
    """
    The Complete Compliance Action Package — all 7 outputs from a single policy upload.
    This is the 'north star' product deliverable.
    """
    # Metadata
    package_id: str = Field(..., description="Unique package identifier")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Package creation timestamp")
    source_file_name: Optional[str] = Field(None, description="Original uploaded file name")
    policy_type: str = Field(..., description="Identified policy type")
    jurisdiction: Optional[str] = Field(None, description="Jurisdiction if specified")

    # Output 1: Gap Analysis
    gap_analysis: AnalysisResult = Field(..., description="Structured gap analysis results")

    # Output 2: Rewritten Policy
    rewritten_policy: Optional[RewrittenPolicy] = Field(None, description="Fully rewritten compliant policy")

    # Output 3: Redline Document
    redline_changes: Optional[List[RedlineChange]] = Field(None, description="Change-by-change redline")

    # Output 4: 90-Day Remediation Plan (retired — kept for backward compat with old responses)
    remediation_plan: Optional[RemediationPlan] = Field(None, description="Phased 90-day remediation plan")

    # Output 6: Board-Ready Summary
    board_summary: Optional[BoardSummary] = Field(None, description="Board-ready executive summary")

    # Output 7: Implementation Checklist
    implementation_checklist: Optional[ImplementationChecklist] = Field(None, description="Actionable implementation checklist")

    # Status tracking
    status: PackageStatus = Field(PackageStatus.pending, description="Current processing status")
    completed_outputs: List[str] = Field(default_factory=list, description="Which outputs have been generated")
    error_message: Optional[str] = Field(None, description="Error message if failed")

    # ── Knowledge Base & Retrieval Metadata (Phase 3) ──
    kb_sources_used: Optional[List[str]] = Field(
        None,
        description="Names of all sources from the knowledge base used across all outputs"
    )
    kb_source_urls: Optional[Dict[str, str]] = Field(
        None,
        description="Map of source name -> authoritative URL (e.g., ecfr.gov) for clickable citations"
    )
    live_research_used: bool = Field(
        False,
        description="Whether live research was used for any output"
    )
    verification_overall: Optional[str] = Field(
        None,
        description="Overall verification status summary across all outputs"
    )
    unverified_claim_count: Optional[int] = Field(
        None,
        description="Number of claims that could not be verified from source material"
    )


class ActionPackageRequest(BaseModel):
    """Request body for generating the complete action package."""
    text: str = Field(..., min_length=50, description="The policy text to analyze")
    file_name: Optional[str] = Field(None, description="Original file name, if uploaded")
    industry: Optional[str] = Field(
        "healthcare",
        description="Industry vertical: 'healthcare', 'education', 'hoa'. Determines which regulations are applied."
    )
    jurisdiction: Optional[str] = Field(None, description="State/jurisdiction code")
    outputs: Optional[List[str]] = Field(
        None,
        description="Which outputs to generate. Options: 'gap_analysis', 'rewritten_policy', 'redline'. If omitted, generates ALL active outputs."
    )
    enable_live_research: bool = Field(
        False,
        description="Whether to use controlled live research when the KB is insufficient"
    )


class PackageExportRequest(BaseModel):
    """Request body for exporting the complete action package."""
    package: ComplianceActionPackage
    file_name: Optional[str] = Field(None, description="Original file name")
    export_format: ExportFormat = Field(ExportFormat.docx, description="Export format")
    sections: Optional[List[str]] = Field(
        None,
        description="Which sections to include in the export. If omitted, includes ALL sections."
    )


class CertificateExportRequest(BaseModel):
    """Request body for exporting a compliance assessment certificate."""
    package: ComplianceActionPackage
    file_name: Optional[str] = Field(None, description="Original file name, used to customize the output filename")


class DraftPolicyRequest(BaseModel):
    """Request body for drafting a new policy from scratch."""
    policy_description: str = Field(..., min_length=5, description="Plain-English description of the policy needed")
    industry: Optional[str] = Field("healthcare", description="Industry vertical: 'healthcare', 'education', 'hoa'")
    jurisdiction: Optional[str] = Field(None, description="State/jurisdiction code (e.g., 'NY')")


class DraftedPolicySection(BaseModel):
    title: str
    content: str


class DraftedPolicy(BaseModel):
    """A fully drafted policy document generated from scratch."""
    policy_title: str
    effective_date: Optional[str] = None
    version: str = "1.0"
    scope: Optional[str] = None
    regulations_applied: List[str] = Field(default_factory=list)
    sections: List[DraftedPolicySection] = Field(default_factory=list)
    full_text: str
    drafting_notes: Optional[str] = None


class DraftPolicyExportRequest(BaseModel):
    """Request body for exporting a drafted policy to .docx."""
    policy: DraftedPolicy


class UpdatedPolicyExportRequest(BaseModel):
    """Request body for exporting just the rewritten policy as a clean .docx."""
    rewritten_policy: RewrittenPolicy
    source_file_name: Optional[str] = Field(None, description="Original uploaded file name (used in download filename)")


class ChatMessage(BaseModel):
    """A single message in a compliance chat conversation."""
    role: str = Field(..., description="'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Request body for chatting with analysis results or a drafted policy."""
    message: str = Field(..., min_length=1, description="The user's question or message")
    mode: str = Field("analysis", description="'analysis' for post-gap-analysis chat, 'draft' for post-policy-draft refinement")
    industry: Optional[str] = Field("healthcare", description="Industry context")
    jurisdiction: Optional[str] = Field(None, description="Jurisdiction context")
    context_summary: Optional[str] = Field(
        None,
        description="Compressed summary of the analysis results or full draft policy text"
    )
    conversation_history: List[ChatMessage] = Field(
        default_factory=list,
        description="Prior conversation messages (up to last 10)"
    )


class ChatResponse(BaseModel):
    """Response from the compliance chat endpoint."""
    response: str = Field(..., description="The AI assistant's response")
    suggested_follow_ups: Optional[List[str]] = Field(
        None,
        description="2-3 suggested follow-up questions relevant to the conversation"
    )


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    version: str = "3.0.0"


# ── Knowledge Base Management Models ──

class IngestRequest(BaseModel):
    """Request body for ingesting a source document into the knowledge base."""
    source_name: str = Field(..., description="Human-readable name of the source document")
    text: str = Field(..., min_length=10, description="Full text of the source document")
    category: str = Field(..., description="Source category: federal_regulation, ocr_guidance, state_law, policy_clause_library, policy_template, example_policy, enforcement_action, requirement_pack")
    jurisdiction: str = Field("federal", description="Jurisdiction: federal or state code (e.g., 'NY', 'CA')")
    citation: Optional[str] = Field(None, description="Formal citation string")
    url: Optional[str] = Field(None, description="Source URL")
    effective_date: Optional[str] = Field(None, description="Effective date")
    authority: Optional[str] = Field(None, description="Issuing authority")


class IngestResponse(BaseModel):
    """Response after ingesting a source document."""
    source_name: str
    chunks_created: int
    collection: str
    status: str = "ok"


class KnowledgeBaseStatsResponse(BaseModel):
    """Knowledge base statistics response."""
    total_chunks: int
    total_collections: int
    collections: Dict[str, int]
    embedding_model: str = "all-MiniLM-L6-v2 (local)"
