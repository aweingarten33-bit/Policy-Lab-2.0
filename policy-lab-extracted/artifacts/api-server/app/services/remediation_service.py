"""
Remediation Service — Generates a phased 90-day remediation plan
with concrete tasks, owners, deliverables, and verification methods.
"""

import json
import re
import logging
from typing import Optional

from app.config import settings
from app.services.provider import get_provider
from app.models.schemas import (
    AnalysisResult, AdjacentPolicyRecommendations,
    RemediationPlan, RemediationPhase, RemediationTask,
)
from app.services.retrieval.models import RetrievalContext

logger = logging.getLogger(__name__)

REMEDIATION_SYSTEM_PROMPT = """You are a senior healthcare compliance remediation strategist. You create actionable, phased remediation plans that compliance officers can execute immediately.

Based on the gap analysis findings, create a detailed 90-day remediation plan organized into 3 phases:

Phase 1 (Days 1-30): Critical and immediate remediation actions
Phase 2 (Days 31-60): High-priority gap closure and policy updates
Phase 3 (Days 61-90): Moderate findings, training, documentation, and verification

Return ONLY valid JSON — no markdown fences, no preamble:

{
  "plan_title": "90-Day Remediation Plan for [Policy Type]",
  "total_tasks": <number>,
  "critical_tasks_first_30": <number>,
  "phases": [
    {
      "phase_number": 1,
      "phase_name": "Critical Remediation",
      "time_range": "Days 1-30",
      "objective": "What this phase accomplishes in 2-3 sentences",
      "tasks": [
        {
          "task_id": "R-001",
          "title": "Short actionable title",
          "description": "Detailed description of what needs to be done (2-3 sentences)",
          "phase": "Phase 1 (Days 1-30)",
          "risk_level": "critical|high|moderate|low",
          "responsible_party": "Role title (e.g., Privacy Officer, CISO, Compliance Director)",
          "deliverable": "What this task produces when complete (e.g., 'Updated policy section IV', 'Completed workforce training log')",
          "regulation_refs": ["45 CFR §164.xxx"],
          "dependencies": ["R-000"],
          "verification_method": "How to verify this is done correctly (e.g., 'Policy reviewed and approved by Privacy Officer and General Counsel')"
        }
      ]
    }
  ],
  "success_criteria": "3-4 sentences describing what success looks like after 90 days",
  "resource_requirements": "Staff, budget, and tool requirements for executing this plan (3-4 sentences)"
}

Rules:
- Generate 8-15 total tasks across all 3 phases
- Phase 1 should have the most tasks (critical items)
- Every critical/high gap from the analysis MUST have a corresponding remediation task
- Dependencies must reference valid task_ids
- Responsible parties should be realistic healthcare compliance roles
- Verification methods must be specific and audit-ready
- Include tasks for: policy revision, training, documentation, approval workflows, and verification
- If adjacent policies are recommended, include tasks for creating those policies
- Make task descriptions actionable — not vague ("Draft and obtain approval for a new Breach Notification Policy" not "Address breach notification gap")"""


async def generate_remediation_plan(
    gap_analysis: AnalysisResult,
    adjacent_policies: Optional[AdjacentPolicyRecommendations] = None,
    jurisdiction: Optional[str] = None,
    retrieval_context: Optional[RetrievalContext] = None,
) -> RemediationPlan:
    """
    Generate a phased 90-day remediation plan from the gap analysis.
    If retrieval_context is provided, uses retrieved source material for grounded planning.
    """
    provider = get_provider()

    # Build context
    gap_context = _build_remediation_context(gap_analysis)
    adjacent_context = ""
    if adjacent_policies:
        adjacent_context = _build_adjacent_context(adjacent_policies)

    user_message = f"""GAP ANALYSIS RESULTS:
{gap_context}"""

    if adjacent_context:
        user_message += f"""

ADJACENT POLICIES NEEDED (include tasks for creating these):
{adjacent_context}"""

    if jurisdiction:
        user_message += f"\n\nJurisdiction: {jurisdiction} — include any state-specific remediation requirements."

    logger.info(f"Generating 90-day remediation plan — {gap_analysis.critical_count} critical, {gap_analysis.gap_count} gaps")

    # Inject retrieved source material
    if retrieval_context and retrieval_context.formatted_context:
        user_message += f"\n\n{retrieval_context.formatted_context}"
    else:
        user_message += "\n\n⚠️ No retrieved source material is available. Mark any regulatory citations as [MODEL INFERENCE]."

    raw_response = await provider.complete(
        system_prompt=REMEDIATION_SYSTEM_PROMPT,
        user_message=user_message,
        max_tokens=settings.llm_max_tokens_long,
        temperature=0.2,
    )

    data = _parse_json_response(raw_response)

    phases = []
    for phase_data in data.get("phases", []):
        tasks = []
        for task_data in phase_data.get("tasks", []):
            tasks.append(RemediationTask(
                task_id=task_data.get("task_id", "R-???"),
                title=task_data.get("title", ""),
                description=task_data.get("description", ""),
                phase=task_data.get("phase", f"Phase {phase_data.get('phase_number', 1)}"),
                risk_level=task_data.get("risk_level", "moderate"),
                responsible_party=task_data.get("responsible_party", "Compliance Officer"),
                deliverable=task_data.get("deliverable", ""),
                regulation_refs=task_data.get("regulation_refs", []),
                dependencies=task_data.get("dependencies", []),
                verification_method=task_data.get("verification_method", ""),
            ))
        phases.append(RemediationPhase(
            phase_number=phase_data.get("phase_number", 1),
            phase_name=phase_data.get("phase_name", ""),
            time_range=phase_data.get("time_range", ""),
            objective=phase_data.get("objective", ""),
            tasks=tasks,
        ))

    return RemediationPlan(
        plan_title=data.get("plan_title", "90-Day Remediation Plan"),
        total_tasks=data.get("total_tasks", sum(len(p.tasks) for p in phases)),
        critical_tasks_first_30=data.get("critical_tasks_first_30", 0),
        phases=phases,
        success_criteria=data.get("success_criteria", ""),
        resource_requirements=data.get("resource_requirements", ""),
    )


def _build_remediation_context(gap_analysis: AnalysisResult) -> str:
    """Build gap analysis context for remediation planning."""
    lines = [
        f"Policy Type: {gap_analysis.policy_type}",
        f"Critical: {gap_analysis.critical_count} | Gaps: {gap_analysis.gap_count} | Partial: {gap_analysis.partial_count} | Compliant: {gap_analysis.compliant_count}",
        "",
        "DETAILED FINDINGS:",
    ]
    for row in gap_analysis.gap_table:
        lines.append(
            f"- [{row.status.upper()} | {row.risk_level.upper() if row.risk_level else ''} | {row.remediation_priority or ''}] "
            f"{row.clause}: {row.finding}\n"
            f"  Suggested: {row.suggested_language[:200]}\n"
            f"  Citation: {row.citation}"
        )
    return "\n".join(lines)


def _build_adjacent_context(adjacent: AdjacentPolicyRecommendations) -> str:
    """Build adjacent policy context."""
    lines = []
    for p in adjacent.policies:
        lines.append(f"- [{p.priority.upper()}] {p.policy_name}: {p.why_recommended[:150]}")
    return "\n".join(lines)


def _parse_json_response(raw_text: str) -> dict:
    """Robustly parse JSON from LLM response."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_text)
    cleaned = re.sub(r"```\s*", "", cleaned)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        raise ValueError("No JSON object found in model response")
    return json.loads(match.group(0))
