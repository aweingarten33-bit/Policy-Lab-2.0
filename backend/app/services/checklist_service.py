"""
Checklist Service — Generates an actionable implementation checklist
with concrete steps, owners, deadlines, and verification criteria.
"""

import json
import re
import logging
from typing import Optional

from app.config import settings
from app.services.provider import get_provider
from app.models.schemas import (
    AnalysisResult, RemediationPlan, RewrittenPolicy,
    ImplementationChecklist, ChecklistItem,
)
from app.services.retrieval.models import RetrievalContext

logger = logging.getLogger(__name__)

CHECKLIST_SYSTEM_PROMPT = """You are a healthcare compliance implementation specialist. You create detailed, actionable checklists that compliance officers can use to track every step needed to bring a policy into full compliance.

Based on the gap analysis, remediation plan, and rewritten policy, create a comprehensive implementation checklist.

Return ONLY valid JSON — no markdown fences, no preamble:

{
  "total_items": <number>,
  "critical_items": <number>,
  "categories": ["Policy", "Training", "Technical", "Administrative", "Documentation"],
  "items": [
    {
      "item_id": "C-001",
      "action": "Specific actionable step (e.g., 'Replace Section IV paragraph 2 with approved rewrite language')",
      "category": "Policy|Training|Technical|Administrative|Documentation",
      "priority": "critical|high|moderate|low",
      "responsible_role": "Specific role (e.g., 'Privacy Officer', 'IT Security Manager', 'HR Training Coordinator')",
      "deadline": "When this should be done (e.g., 'Week 1', 'Day 15', 'Day 45')",
      "regulation_ref": "The specific regulation requiring this action (e.g., '45 CFR §164.530(j)')",
      "verification": "How to verify completion (e.g., 'Policy section reviewed and approved by Privacy Officer; version date updated')",
      "evidence_needed": "What to retain as evidence (e.g., 'Signed policy with effective date; board meeting minutes showing approval')",
      "status": "pending"
    }
  ],
  "completion_timeline": "Brief timeline overview (e.g., '15 critical items by Day 30, 8 high items by Day 60, 6 moderate items by Day 90')"
}

Rules:
- Generate 12-25 checklist items
- Every critical and high gap must have at least one corresponding checklist item
- Items must be specific and actionable — not vague ("Update Section IV to include mandatory breach reporting timeline per 45 CFR §164.408" not "Fix breach section")
- Include items for: policy text changes, approvals/signatures, training delivery, documentation updates, technical configurations, and audit trail creation
- Verification criteria must be concrete and audit-proof
- Evidence items should be what an OCR auditor would ask to see
- Organize by priority within categories
- Include items for adopting the rewritten policy if one was provided
- Include items for creating recommended adjacent policies if applicable
- Deadlines should align with the remediation plan phases"""


async def generate_implementation_checklist(
    gap_analysis: AnalysisResult,
    remediation_plan: Optional[RemediationPlan] = None,
    rewritten_policy: Optional[RewrittenPolicy] = None,
    retrieval_context: Optional[RetrievalContext] = None,
) -> ImplementationChecklist:
    """
    Generate an actionable implementation checklist.
    If retrieval_context is provided, uses retrieved source material for grounded items.
    """
    provider = get_provider()

    user_message = f"""POLICY TYPE: {gap_analysis.policy_type}

GAP FINDINGS:
- Critical: {gap_analysis.critical_count}
- High/Gaps: {gap_analysis.gap_count}
- Moderate/Partial: {gap_analysis.partial_count}
- Compliant: {gap_analysis.compliant_count}

DETAILED GAPS:"""

    for row in gap_analysis.gap_table:
        if row.status != "compliant":
            user_message += f"\n- [{row.status.upper()} | {row.risk_level.upper() if row.risk_level else ''}] {row.clause}: {row.finding[:200]}"

    if remediation_plan:
        user_message += f"""

REMEDIATION PLAN SUMMARY:
- Total Tasks: {remediation_plan.total_tasks}
- Critical Tasks (first 30 days): {remediation_plan.critical_tasks_first_30}"""
        for phase in remediation_plan.phases:
            user_message += f"\n- Phase {phase.phase_number} ({phase.time_range}): {phase.objective[:150]}"

    if rewritten_policy:
        user_message += f"""

REWRITTEN POLICY AVAILABLE: Yes — {len(rewritten_policy.sections)} sections rewritten
Include checklist items for reviewing, approving, and adopting the rewritten policy."""

    logger.info(f"Generating implementation checklist — {len(gap_analysis.gap_table)} findings")

    # Inject retrieved source material
    if retrieval_context and retrieval_context.formatted_context:
        user_message += f"\n\n{retrieval_context.formatted_context}"
    else:
        user_message += "\n\n⚠️ No retrieved source material is available. Mark any regulatory citations as [MODEL INFERENCE]."

    raw_response = await provider.complete(
        system_prompt=CHECKLIST_SYSTEM_PROMPT,
        user_message=user_message,
        max_tokens=settings.llm_max_tokens_long,
        temperature=0.2,
    )

    data = _parse_json_response(raw_response)

    items = []
    for item_data in data.get("items", []):
        items.append(ChecklistItem(
            item_id=item_data.get("item_id", "C-???"),
            action=item_data.get("action", ""),
            category=item_data.get("category", "Administrative"),
            priority=item_data.get("priority", "moderate"),
            responsible_role=item_data.get("responsible_role", "Compliance Officer"),
            deadline=item_data.get("deadline", ""),
            regulation_ref=item_data.get("regulation_ref", ""),
            verification=item_data.get("verification", ""),
            evidence_needed=item_data.get("evidence_needed", ""),
            status=item_data.get("status", "pending"),
        ))

    # Sort by priority
    priority_order = {"critical": 0, "high": 1, "moderate": 2, "low": 3}
    items.sort(key=lambda i: priority_order.get(i.priority, 4))

    critical_count = sum(1 for i in items if i.priority == "critical")
    categories = list(dict.fromkeys(i.category for i in items))

    return ImplementationChecklist(
        total_items=len(items),
        critical_items=critical_count,
        categories=categories,
        items=items,
        completion_timeline=data.get("completion_timeline", ""),
    )


def _parse_json_response(raw_text: str) -> dict:
    """Robustly parse JSON from LLM response."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_text)
    cleaned = re.sub(r"```\s*", "", cleaned)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        raise ValueError("No JSON object found in model response")
    return json.loads(match.group(0))
