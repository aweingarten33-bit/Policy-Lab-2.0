"""
Board Summary Service — Generates a board-ready executive summary
that a compliance officer can hand to board members or senior leadership.
"""

import json
import re
import logging
from typing import Optional

from app.config import settings
from app.services.provider import get_provider
from app.models.schemas import (
    AnalysisResult, AdjacentPolicyRecommendations,
    RemediationPlan, BoardSummary,
)
from app.services.retrieval.models import RetrievalContext

logger = logging.getLogger(__name__)

BOARD_SUMMARY_SYSTEM_PROMPT = """You are a healthcare compliance executive writing a board-ready summary. Board members are not compliance experts — they need clear, concise, actionable information about risk exposure and what needs to happen.

Write a professional board summary that:
1. Opens with a clear headline summarizing the compliance posture
2. Provides an overall status assessment in plain language
3. Highlights the 3-5 most important findings (not all findings — just what the board needs to know)
4. Quantifies regulatory exposure in terms board members understand (fines, audit risk, reputational risk)
5. Explains the remediation plan in 2-3 sentences
6. Recommends 2-3 specific board-level actions (approve budget, authorize policy changes, schedule follow-up)
7. Suggests a budget impact range if possible

Return ONLY valid JSON — no markdown fences, no preamble:

{
  "headline": "One-line headline for board attention (e.g., 'Significant HIPAA Privacy Gaps Identified — Immediate Remediation Required')",
  "overall_status": "Brief status: 'Significant Gaps Identified', 'Moderate Gaps Require Attention', 'Substantially Compliant', or 'Fully Compliant'",
  "risk_summary": "2-3 sentence risk assessment explaining the compliance posture in terms of audit exposure and potential consequences",
  "key_findings": ["Finding 1 — concise, board-appropriate language", "Finding 2", "Finding 3-5"],
  "regulatory_exposure": "2-3 sentences about potential regulatory consequences, fine ranges, and audit risk based on the findings",
  "remediation_status": "2-3 sentences about the remediation plan and timeline",
  "recommended_actions": ["Action 1 — what the board should approve or authorize", "Action 2", "Action 3"],
  "budget_impact": "Estimated budget impact range with brief justification (e.g., '$5K-$15K for policy revisions and training materials')",
  "next_review_date": "Recommended date for next compliance review (e.g., '90 days from today')",
  "prepared_by": "Title/role of who would prepare this (e.g., 'Chief Compliance Officer')",
  "prepared_date": "Date this would be presented"
}

Rules:
- Language must be appropriate for a board of directors — professional, direct, no jargon
- Do not minimize risk — board members need to understand exposure
- Do not overstate risk — be factual and grounded in the findings
- Key findings should focus on WHAT the board needs to know, not technical details
- Recommended actions must be things the board can actually do (approve, authorize, direct)
- Budget impact should be a reasonable estimate based on the scope of remediation needed
- This should be immediately usable in a board packet without editing"""


async def generate_board_summary(
    gap_analysis: AnalysisResult,
    remediation_plan: Optional[RemediationPlan] = None,
    adjacent_policies: Optional[AdjacentPolicyRecommendations] = None,
    file_name: Optional[str] = None,
    retrieval_context: Optional[RetrievalContext] = None,
) -> BoardSummary:
    """
    Generate a board-ready executive summary.
    If retrieval_context is provided, uses retrieved source material for grounded assessment.
    """
    provider = get_provider()

    user_message = f"""POLICY ANALYZED: {file_name or 'Uploaded Policy'}
POLICY TYPE: {gap_analysis.policy_type}

FINDINGS SUMMARY:
- Critical: {gap_analysis.critical_count}
- High/Gaps: {gap_analysis.gap_count}
- Moderate/Partial: {gap_analysis.partial_count}
- Compliant: {gap_analysis.compliant_count}
- Total Regulations Checked: {len(gap_analysis.regulations_applied)}

PRIORITY FINDINGS:
{chr(10).join(f'- {f}' for f in gap_analysis.priority_findings[:5])}

EXECUTIVE SUMMARY FROM ANALYSIS:
{gap_analysis.audit_ready_summary}"""

    if remediation_plan:
        user_message += f"""

REMEDIATION PLAN:
- Total Tasks: {remediation_plan.total_tasks}
- Critical Tasks (first 30 days): {remediation_plan.critical_tasks_first_30}
- Phases: {len(remediation_plan.phases)}
- Success Criteria: {remediation_plan.success_criteria}
- Resource Requirements: {remediation_plan.resource_requirements}"""

    if adjacent_policies:
        user_message += f"""

ADDITIONAL POLICIES NEEDED: {adjacent_policies.total_recommended}
{chr(10).join(f'- [{p.priority.upper()}] {p.policy_name}' for p in adjacent_policies.policies[:5])}"""

    logger.info("Generating board-ready summary")

    # Inject retrieved source material
    if retrieval_context and retrieval_context.formatted_context:
        user_message += f"\n\n{retrieval_context.formatted_context}"
    else:
        user_message += "\n\n⚠️ No retrieved source material is available. Mark any regulatory exposure claims as [MODEL INFERENCE]."

    raw_response = await provider.complete(
        system_prompt=BOARD_SUMMARY_SYSTEM_PROMPT,
        user_message=user_message,
        max_tokens=settings.llm_max_tokens,
        temperature=0.3,
    )

    data = _parse_json_response(raw_response)

    return BoardSummary(
        headline=data.get("headline", ""),
        overall_status=data.get("overall_status", ""),
        risk_summary=data.get("risk_summary", ""),
        key_findings=data.get("key_findings", []),
        regulatory_exposure=data.get("regulatory_exposure", ""),
        remediation_status=data.get("remediation_status", ""),
        recommended_actions=data.get("recommended_actions", []),
        budget_impact=data.get("budget_impact"),
        next_review_date=data.get("next_review_date"),
        prepared_by=data.get("prepared_by", "Chief Compliance Officer"),
        prepared_date=data.get("prepared_date"),
    )


def _parse_json_response(raw_text: str) -> dict:
    """Robustly parse JSON from LLM response."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_text)
    cleaned = re.sub(r"```\s*", "", cleaned)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        raise ValueError("No JSON object found in model response")
    return json.loads(match.group(0))
