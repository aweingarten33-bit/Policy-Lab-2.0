"""
Adjacent Policy Service — Recommends policies the organization likely also needs
based on the analyzed policy type, identified gaps, and regulatory requirements.
"""

import json
import re
import logging
from typing import Optional

from app.config import settings
from app.services.provider import get_provider
from app.models.schemas import (
    AnalysisResult, AdjacentPolicy, AdjacentPolicyRecommendations,
)
from app.services.retrieval.models import RetrievalContext

logger = logging.getLogger(__name__)

ADJACENT_POLICY_SYSTEM_PROMPT = """You are a senior healthcare compliance consultant. A hospital or healthcare organization has uploaded one of their policies for gap analysis. Based on the policy type and the gaps found, you must recommend the OTHER policies this organization likely also needs — policies they may not have, or policies that are required by the same regulations but cover different aspects.

Think about the full regulatory ecosystem:
- If they uploaded a Privacy Policy, they likely also need: Security Rule policies, Breach Notification policy, Sanctions policy, Workforce Training policy, etc.
- If they uploaded an Access Control policy, they likely also need: Audit Log policy, Transmission Security policy, Disaster Recovery policy, etc.
- Consider what OCR audit protocols require as a SET of policies, not just one

For each recommended policy, provide a detailed draft outline that a compliance officer could use to create the policy from scratch.

Return ONLY valid JSON — no markdown fences, no preamble:

{
  "analysis_basis": "Brief explanation of what this analysis is based on",
  "total_recommended": <number>,
  "policies": [
    {
      "policy_name": "Full name of the recommended policy",
      "category": "Privacy|Security|Workforce|Administrative|Clinical|Billing|Research|Vendor",
      "why_recommended": "2-3 sentences explaining why this organization needs this policy, referencing their gaps or regulatory requirements",
      "key_requirements": ["Key requirement 1 this policy must address", "Key requirement 2", ...],
      "applicable_regulations": ["45 CFR §164.xxx", "42 CFR Part 2.xxx", ...],
      "priority": "critical|high|moderate",
      "draft_outline": "Detailed multi-section outline for this policy. Include: Purpose, Scope, Definitions, Policy Statements (with specific requirements), Procedures, Enforcement/ sanctions, References, Review Schedule. Be specific about what each section should contain. At least 500 words of outline detail."
    }
  ]
}

Rules:
- Recommend 4-7 adjacent policies
- Prioritize policies that are legally REQUIRED (critical) vs best practice (moderate)
- The draft_outline must be detailed enough that a compliance officer can write the full policy from it
- Include specific regulatory citations for each recommended policy
- Consider state-specific requirements if a jurisdiction is provided
- Do NOT recommend a policy that is the same type as the one analyzed"""

STATE_ADJACENT_ADDENDUM = """

IMPORTANT: The organization is in {jurisdiction}. Include {jurisdiction}-specific policy requirements:
- State breach notification law requirements
- State privacy act provisions
- State health code mandated policies
- State attorney general enforcement patterns
Cite specific state statutes and codes."""


async def generate_adjacent_policies(
    original_text: str,
    gap_analysis: AnalysisResult,
    jurisdiction: Optional[str] = None,
    retrieval_context: Optional[RetrievalContext] = None,
) -> AdjacentPolicyRecommendations:
    """
    Recommend adjacent policies the organization likely also needs.
    If retrieval_context is provided, uses retrieved source material for grounded recommendations.
    """
    provider = get_provider()

    # Build context
    gap_summary = _build_adjacent_context(gap_analysis)

    user_message = f"""ANALYZED POLICY TYPE: {gap_analysis.policy_type}

ORIGINAL POLICY (excerpt — first 3000 chars):
{original_text[:3000]}

GAP ANALYSIS SUMMARY:
{gap_summary}"""

    if jurisdiction:
        user_message += f"\n\nJurisdiction: {jurisdiction}"

    system_prompt = ADJACENT_POLICY_SYSTEM_PROMPT
    if jurisdiction:
        system_prompt += STATE_ADJACENT_ADDENDUM.format(jurisdiction=jurisdiction)

    logger.info(f"Generating adjacent policy recommendations for {gap_analysis.policy_type}")

    # Inject retrieved source material
    if retrieval_context and retrieval_context.formatted_context:
        user_message += f"\n\n{retrieval_context.formatted_context}"
    else:
        user_message += "\n\n⚠️ No retrieved source material is available. Mark any regulatory citations as [MODEL INFERENCE]."

    raw_response = await provider.complete(
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=settings.llm_max_tokens_long,
        temperature=0.3,
    )

    data = _parse_json_response(raw_response)

    policies = []
    for p_data in data.get("policies", []):
        policies.append(AdjacentPolicy(
            policy_name=p_data.get("policy_name", ""),
            category=p_data.get("category", "Administrative"),
            why_recommended=p_data.get("why_recommended", ""),
            key_requirements=p_data.get("key_requirements", []),
            applicable_regulations=p_data.get("applicable_regulations", []),
            priority=p_data.get("priority", "moderate"),
            draft_outline=p_data.get("draft_outline", ""),
        ))

    # Sort by priority
    priority_order = {"critical": 0, "high": 1, "moderate": 2}
    policies.sort(key=lambda p: priority_order.get(p.priority, 3))

    return AdjacentPolicyRecommendations(
        analysis_basis=data.get("analysis_basis", f"Based on analysis of {gap_analysis.policy_type}"),
        total_recommended=len(policies),
        policies=policies,
    )


def _build_adjacent_context(gap_analysis: AnalysisResult) -> str:
    """Build context about the gaps for adjacent policy recommendations."""
    lines = [
        f"Policy Type: {gap_analysis.policy_type}",
        f"Critical Findings: {gap_analysis.critical_count}",
        f"Gaps: {gap_analysis.gap_count}",
        f"Partial Compliance: {gap_analysis.partial_count}",
        f"Regulations Checked: {', '.join(gap_analysis.regulations_applied[:15])}",
        "",
        "Key Gaps Identified:",
    ]
    for row in gap_analysis.gap_table:
        if row.status != "compliant":
            lines.append(f"- {row.clause}: {row.finding[:200]}")
    return "\n".join(lines)


def _parse_json_response(raw_text: str) -> dict:
    """Robustly parse JSON from LLM response."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_text)
    cleaned = re.sub(r"```\s*", "", cleaned)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        raise ValueError("No JSON object found in model response")
    return json.loads(match.group(0))
