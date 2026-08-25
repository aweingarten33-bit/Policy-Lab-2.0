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

# ── Input size caps ──
# Every field below flows into a paid LLM call. Without an upper bound a single
# request can run up an unbounded bill, which matters most if authentication is
# ever left unconfigured. Generous enough for a long real policy document.
MAX_INPUT_CHARS = 100_000   # ~25k tokens / roughly a 40-page policy
MAX_CHAT_CHARS = 10_000     # a follow-up question, not a document


# ── Enums ──

class GapStatus(str, Enum):
    compliant = "compliant"
    partial = "partial"
    gap = "gap"
    missing = "missing"


# ── Four-axis classification ──
# The four axes are presence, specificity, operability and accountability.
#
# The prompt used to define the bands as: partial when 1-2 axes pass, gap when
# 0-1 pass. One passing axis therefore satisfied both definitions, and zero
# passing axes satisfied both "gap" and "missing" -- so for two of the five
# possible scores the correct label was whichever the model happened to pick.
# Severity, remediation priority and the compliance score all key off status,
# so the ambiguity propagated straight into the numbers a reader acts on.
#
# The bands below partition 0-4 exactly once each, and the mapping lives in
# code so it is the same on every request rather than a instruction the model
# may or may not follow.
AXIS_COUNT = 4


def classify_from_axes(axes_passed: int, topic_absent: bool = False) -> GapStatus:
    """Map the number of passing axes to exactly one status.

        4 → compliant, 3 or 2 → partial, 1 → gap, 0 → missing.

    ``topic_absent`` is a shortcut, not a second rule: presence is itself one
    of the four axes, so an obligation the policy never addresses scores zero
    and lands on ``missing`` either way. The flag exists so a caller that knows
    the topic is absent need not also compute the axes.
    """
    if topic_absent:
        return GapStatus.missing
    if not isinstance(axes_passed, int) or axes_passed < 0 or axes_passed > AXIS_COUNT:
        raise ValueError(f"axes_passed must be an integer 0..{AXIS_COUNT}, got {axes_passed!r}")
    if axes_passed == AXIS_COUNT:
        return GapStatus.compliant
    if axes_passed >= 2:
        return GapStatus.partial
    if axes_passed == 1:
        return GapStatus.gap
    return GapStatus.missing


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
    # The check could not be completed because the source's own standing is
    # unknown or non-current. Distinct from `unverified`: there, the source was
    # current and simply did not support the claim; here, no conclusion about
    # present law is possible from this material at all. Both are fail-closed --
    # neither may be presented as a confirmed legal requirement.
    cannot_determine = "cannot_determine"


class SourceStatus(str, Enum):
    """Standing of a source relative to present-day law.

    Verification previously had one bit for this (``is_current``), defaulted to
    True, and live search results set it to True purely because a search engine
    returned them. A proposed rule, a superseded version and an undated page all
    looked identical to a currently-in-force regulation.

    Only ``current_verified`` may support a claim about a present legal duty.
    """
    current_verified = "CURRENT_VERIFIED"  # in force now, and we know that from the source
    proposed = "PROPOSED"                  # NPRM / draft — not law yet, may never be
    superseded = "SUPERSEDED"              # replaced by a later version
    historical = "HISTORICAL"              # archived / of record only
    status_unknown = "STATUS_UNKNOWN"      # standing not established — the safe default


# The single set of source standings that can support a statement about what
# the law requires today. Everything else fails closed.
PRESENT_DUTY_STATUSES = frozenset({SourceStatus.current_verified})


def can_support_present_duty(status: "SourceStatus") -> bool:
    """True only for a source whose standing is established as currently in force."""
    return status in PRESENT_DUTY_STATUSES


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

class ExportRequest(BaseModel):
    """Request body for generating an export file."""
    result: "AnalysisResult"
    file_name: Optional[str] = Field(None, description="Original file name for the report header")
    export_format: ExportFormat = Field(ExportFormat.docx, description="Export format: docx or pdf")
    kb_sources_used: Optional[List[str]] = Field(
        None, description="Knowledge base source names used, so the report can show its verification section"
    )
    live_research_used: bool = Field(False, description="Whether live research was used")
    verification_overall: Optional[str] = Field(None, description="Verification summary sentence")


class ClaimSupport(str, Enum):
    """Result of testing whether an authoritative excerpt actually supports a claim."""
    supported = "SUPPORTED"
    partially_supported = "PARTIALLY_SUPPORTED"
    not_supported = "NOT_SUPPORTED"
    contradicted = "CONTRADICTED"
    not_checked = "NOT_CHECKED"


class EvidenceSource(BaseModel):
    """The exact authoritative passage a claim was checked against."""
    name: Optional[str] = None
    url: Optional[str] = None
    version_date: Optional[str] = Field(
        None, description="Date the source text was published/retrieved — what version was checked"
    )
    excerpt: Optional[str] = Field(
        None, description="The exact passage. This is the source of truth for the check."
    )
    # Dates are kept apart rather than collapsed into one "date" field. A
    # publication date is when a document appeared; an effective date is when a
    # rule started binding anyone. Treating the first as the second is how a
    # newly published article *about* a rule made the rule look newly effective.
    publication_date: Optional[str] = Field(
        None, description="When the document was published. Never an effective date."
    )
    effective_date: Optional[str] = Field(
        None, description="When the provision took legal effect, when the source states it"
    )
    retrieved_date: Optional[str] = Field(
        None, description="When this text was fetched into the knowledge base"
    )
    last_verified_date: Optional[str] = Field(
        None, description="When the text was last confirmed against the authoritative publisher"
    )
    status: SourceStatus = Field(
        SourceStatus.status_unknown,
        description="Standing of this source relative to present-day law",
    )


class EvidenceChecks(BaseModel):
    """The independent checks behind a verification status.

    Kept separate so a status is auditable rather than a bare label: a reader
    can see which checks ran, which passed, and which were not applicable.
    """
    citation_exists: bool = Field(
        False, description="The cited section was found in retrieved authoritative material"
    )
    specifics_supported: Optional[bool] = Field(
        None, description="Concrete durations in the claim appear in the source (None = none stated)"
    )
    claim_support: ClaimSupport = Field(
        ClaimSupport.not_checked, description="Whether the excerpt entails the claim"
    )
    source_status: SourceStatus = Field(
        SourceStatus.status_unknown,
        description="Standing of the source the claim was checked against",
    )
    source_status_current: bool = Field(
        False,
        description=(
            "The source's standing is established as currently in force. Required "
            "for `verified`: a proposed, superseded, historical or unknown-status "
            "source can never confirm a present legal duty."
        ),
    )


class VerificationEvidence(BaseModel):
    """A durable, auditable record of how one claim was verified.

    Replaces treating 'the cited regulation exists' as proof the claim is true.
    A real citation attached to an unsupported statement is the failure mode
    this exists to catch.
    """
    claim_id: str
    claim_text: str
    status: VerificationStatus = VerificationStatus.unverified
    citation: Optional[str] = None
    source: EvidenceSource = Field(default_factory=EvidenceSource)
    checks: EvidenceChecks = Field(default_factory=EvidenceChecks)
    reason: str = ""


class ObligationType(str, Enum):
    """What kind of duty a finding asserts.

    A compliance officer's first question about any finding is "do I have to
    do this, or are you recommending it?" Collapsing the two is how an OSHA
    provision saying an allowance "may" be made was reported as a must-fix
    regulatory deficiency.
    """
    required = "required"                          # black-letter law imposes it
    guidance = "guidance"                          # agency recommends/interprets
    best_practice = "best_practice"                # sound practice, not mandated
    organizational_choice = "organizational_choice"  # the org's own decision
    # Assigned by the entailment gate, never by the model: the finding was
    # presented as legally required, but the cited source does not establish
    # that duty.
    unverified_requirement = "unverified_requirement"


class GapRow(BaseModel):
    """A single gap finding row."""
    clause: str = Field(..., description="Policy section or topic area")
    regulations: List[str] = Field(default_factory=list, description="Applicable regulation citations")
    status: GapStatus = Field(..., description="Compliance status")
    axes_passed: Optional[int] = Field(
        None,
        ge=0,
        le=AXIS_COUNT,
        description=(
            "How many of the four axes (presence, specificity, operability, "
            "accountability) this obligation passes. When present, `status` is "
            "derived from it by classify_from_axes() rather than taken from the "
            "model, so the banding is identical on every request."
        ),
    )
    risk_level: Optional[RiskLevel] = Field(None, description="Risk level for OCR/audit context")
    current_state: Optional[str] = Field(
        None,
        description="What the policy currently says about this topic (direct quote or close paraphrase)"
    )
    finding: str = Field(..., description="What is wrong or missing")
    suggested_language: str = Field(..., description="Ready-to-paste policy text to remediate the gap")
    citation: str = Field(..., description="Full citation with source and year")
    obligation_type: ObligationType = Field(
        ObligationType.required,
        description=(
            "Whether the cited authority actually mandates this, merely recommends it, "
            "is sound practice, or is the organization's own choice. Downgraded to "
            "'unverified_requirement' by the entailment gate if a claimed mandate is not "
            "established by the source text."
        )
    )
    obligation_note: Optional[str] = Field(
        None,
        description="Why the obligation type changed, when the entailment gate downgraded it"
    )
    remediation_priority: Optional[str] = Field(
        None,
        description="Immediate / 30-day / 90-day / Next-review remediation timeline"
    )
    oig_element: Optional[str] = Field(
        None,
        description="OIG GCPG element this finding relates to (1–7), e.g. '3 — Training & Education'"
    )
    verification_warning: Optional[str] = Field(
        None,
        description=(
            "Set when this finding states a concrete deadline/retention period that does not "
            "appear in the retrieved source material -- i.e. a real citation carrying a number "
            "the sources don't support. Surfaced inline on the finding itself."
        )
    )
    evidence: Optional[VerificationEvidence] = Field(
        None,
        description="Auditable record of how this finding's citation and claim were checked"
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
    # Set server-side, not by the model, so the export's closing disclaimers
    # match the sector this policy is actually for.
    industry: Optional[str] = Field(None, description="Industry slug this rewrite was generated for")
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


class SourceSnippet(BaseModel):
    """A single retrieved source chunk exposed to the UI -- the actual text
    behind a citation, not just a source-name badge."""
    citation: Optional[str] = None
    source_name: str
    url: Optional[str] = None
    text: str


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
    # Carried through to the export so closing disclaimers match the sector.
    # Without it the exporter defaulted to healthcare wording, and a factory
    # noise policy was signed off with "consult qualified healthcare
    # compliance counsel" and a statement about protected health information.
    industry: Optional[str] = Field(None, description="Industry slug this package was generated for")

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
    source_snippets: Optional[List[SourceSnippet]] = Field(
        None,
        description="Actual retrieved source passages, so the UI can show the real text behind a citation"
    )


class ActionPackageRequest(BaseModel):
    """Request body for generating the complete action package."""
    text: str = Field(..., min_length=50, max_length=MAX_INPUT_CHARS, description="The policy text to analyze")
    file_name: Optional[str] = Field(None, description="Original file name, if uploaded")
    industry: Optional[str] = Field(
        "healthcare",
        description="Industry vertical: 'healthcare', 'home_health', or 'pharmacy'. Determines which regulations are applied."
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


class RewritePolicyRequest(BaseModel):
    """Request body for the 'Fix All Gaps' action — rewrite the policy to resolve every finding from an existing gap analysis."""
    text: str = Field(..., min_length=50, max_length=MAX_INPUT_CHARS, description="The original policy text that was analyzed")
    gap_analysis: AnalysisResult = Field(..., description="The gap analysis results to fix")
    industry: Optional[str] = Field("healthcare", description="Industry vertical: 'healthcare', 'home_health', or 'pharmacy'")
    jurisdiction: Optional[str] = Field(None, description="State/jurisdiction code")


class PackageExportRequest(BaseModel):
    """Request body for exporting the complete action package."""
    package: ComplianceActionPackage
    file_name: Optional[str] = Field(None, description="Original file name")
    export_format: ExportFormat = Field(ExportFormat.docx, description="Export format")
    sections: Optional[List[str]] = Field(
        None,
        description="Which sections to include in the export. If omitted, includes ALL sections."
    )


class DraftPolicyRequest(BaseModel):
    """Request body for drafting a new policy from scratch."""
    policy_description: str = Field(..., min_length=5, max_length=MAX_INPUT_CHARS, description="Plain-English description of the policy needed")
    industry: Optional[str] = Field("healthcare", description="Industry vertical: 'healthcare', 'home_health', or 'pharmacy'")
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
    kb_sources_used: Optional[List[str]] = Field(
        None, description="Knowledge base / live research source names used to ground this draft"
    )
    kb_source_urls: Optional[Dict[str, str]] = Field(
        None, description="Mapping of source name to URL for the sources above"
    )
    live_research_used: bool = Field(
        False, description="Whether live research was used to ground this draft"
    )
    verification_overall: Optional[str] = Field(
        None, description="Human-readable summary of how well-grounded this draft is"
    )
    unverified_claim_count: Optional[int] = Field(
        None, description="Number of citations in the draft that could not be verified against loaded sources"
    )
    source_snippets: Optional[List[SourceSnippet]] = Field(
        None, description="Actual retrieved source passages, so the UI can show the real text behind a citation"
    )


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
    message: str = Field(..., min_length=1, max_length=MAX_CHAT_CHARS, description="The user's question or message")
    mode: str = Field("analysis", description="'analysis' for post-gap-analysis Q&A, 'draft' for post-policy-draft Q&A")
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


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    version: str = "3.0.0"
    # Knowledge-base grounding state. An empty KB is the app's most consequential
    # silent failure: output still looks fully source-grounded (same UI, same
    # citation formatting) while nothing is actually backing it. Surfacing the
    # chunk count here makes that state monitorable instead of invisible.
    kb_enabled: bool = True
    kb_chunks: int = 0
    kb_grounded: bool = False
    kb_unreadable: bool = False
    # Date the stored regulations were downloaded. The corpus is built into the
    # image, so it is exactly as current as the last rebuild -- and a chunk
    # count alone cannot distinguish a fresh corpus from a years-old one.
    kb_corpus_date: Optional[str] = None
    kb_corpus_age_days: Optional[int] = None
    # Which commit is actually running. Without this there is no way to tell a
    # fix that has not deployed yet from a fix that did not work -- and the
    # two need completely different responses.
    build_commit: Optional[str] = None


# ── Knowledge Base Management Models ──

class IngestRequest(BaseModel):
    """Request body for ingesting a source document into the knowledge base."""
    source_name: str = Field(..., description="Human-readable name of the source document")
    text: str = Field(..., min_length=10, max_length=MAX_INPUT_CHARS, description="Full text of the source document")
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
