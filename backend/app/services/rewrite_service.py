"""
Rewrite Service — Generates a fully rewritten compliant policy and redline document.
Uses the gap analysis to produce publication-ready policy text with tracked changes.
"""

import json
import re
import logging
from typing import Optional, List

from app.config import settings  # <-- FIX #1: ADDED THIS LINE
from app.services.provider import get_provider
from app.services.industry_config import get_industry
from app.models.schemas import (
    AnalysisResult, RewrittenPolicy, RewrittenPolicySection,
    RedlineChange, GapRow, RiskLevel,
)
from app.services.retrieval.models import RetrievalContext

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# System Prompts
# ──────────────────────────────────────────────

REWRITE_TASK_INSTRUCTIONS = """You will receive:
1. An original compliance policy document
2. A gap analysis identifying every compliance deficiency

Your task: Rewrite the ENTIRE policy from start to finish. The rewritten policy must:
- Fix every gap, missing element, and vague clause identified in the gap analysis
- Incorporate every piece of suggested_language from the gap findings
- Use precise, audit-proof language — no "may," "should," or "when appropriate" where the regulation requires "shall," "must," or mandatory action
- Include proper regulatory citations in brackets [45 CFR §164.xxx] where required by the regulation
- Follow standard healthcare compliance policy structure (Purpose, Scope, Definitions, Policy Statements, Procedures, Enforcement, References, Review Schedule)
- Be a COMPLETE, ready-to-adopt policy — not a partial draft

Return ONLY valid JSON — no markdown fences, no preamble:

{
  "policy_title": "Full title of the rewritten policy",
  "effective_date": "Suggested effective date (e.g., 'Upon adoption' or specific date)",
  "version_note": "Version note explaining this is a compliance rewrite and what changed at a high level",
  "change_summary": "2-3 sentence executive summary of all changes made and why",
  "sections": [
    {
      "section_title": "Section heading (e.g., 'I. PURPOSE')",
      "original_text": "The exact text from the original policy for this section (or 'NEW SECTION' if it didn't exist)",
      "rewritten_text": "The complete rewritten text for this section",
      "changes_summary": "Brief summary of what was changed in this section and why (regulatory citations)",
      "regulation_refs": ["45 CFR §164.xxx", "42 CFR Part 2.xxx"]
    }
  ],
  "full_text": "The complete rewritten policy as one continuous document, formatted for direct adoption. Include section headers, numbered paragraphs, and all regulatory citations inline."
}

CRITICAL RULES:
- Every gap finding from the analysis MUST be addressed in the rewrite
- Missing sections must be created from scratch with compliant language
- Partial sections must be completed with the missing mandatory elements
- Suggested language from the gap analysis should be incorporated but improved for policy-grade writing
- The full_text field must be a single cohesive document, not a list of fragments
- Aim for 6-12 sections minimum
- Use roman numerals for major sections (I, II, III...) and letters for subsections (A, B, C...)"""


def _build_rewrite_system_prompt(industry_slug: Optional[str] = None) -> str:
    """Industry-aware rewrite persona — same domain expertise as the gap analysis
    prompt, so a Home Health or Other rewrite doesn't get a hospital compliance voice."""
    cfg = get_industry(industry_slug or "healthcare")
    return cfg["persona"] + "\n\n" + REWRITE_TASK_INSTRUCTIONS


REDLINE_SYSTEM_PROMPT = """You are a legal redline comparison expert. You compare an original healthcare policy with its rewritten version and produce a detailed change-by-change analysis.

Return ONLY valid JSON — no markdown fences, no preamble:

{
  "changes": [
    {
      "type": "added|removed|modified",
      "section": "Which section this change belongs to",
      "original_text": "The original text (null if 'added')",
      "revised_text": "The new text (null if 'removed')",
      "regulation_ref": "The regulation citation that prompted this change, if applicable"
    }
  ]
}

Rules:
- Every substantive change must be captured
- Classify as 'added' (new text not in original), 'removed' (text deleted from original), or 'modified' (text changed)
- Include the section heading for each change
- Link changes to the regulation that required them where possible
- Do NOT capture purely cosmetic changes (whitespace, formatting)
- Capture ALL regulatory-mandated additions as 'added' type"""


def _parse_json_response(raw_text: str) -> dict:
    """Robustly parse JSON from LLM response.

    LLMs (especially Mistral / Llama) frequently produce slightly malformed JSON:
    trailing commas, unescaped newlines inside string values, missing closing
    braces, smart quotes, etc. We try strict parse first, then fall back to
    json_repair which handles all of the above without losing data.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_text)
    cleaned = re.sub(r"```\s*", "", cleaned)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        raise ValueError("No JSON object found in model response")
    candidate = match.group(0)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as strict_err:
        try:
            from json_repair import repair_json
            repaired = repair_json(candidate, return_objects=True)
            if isinstance(repaired, dict):
                logger.warning(f"JSON repaired after strict parse failure: {strict_err}")
                return repaired
            raise ValueError(f"json_repair returned non-dict: {type(repaired).__name__}")
        except Exception as repair_err:
            logger.error(f"JSON repair also failed: {repair_err}")
            raise strict_err


async def generate_rewritten_policy(
    original_text: str,
    gap_analysis: AnalysisResult,
    jurisdiction: Optional[str] = None,
    retrieval_context: Optional[RetrievalContext] = None,
    industry: Optional[str] = None,
) -> RewrittenPolicy:
    """
    Generate a fully rewritten compliant version of the policy.
    Uses the gap analysis to fix every identified deficiency.
    If retrieval_context is provided, uses retrieved source material for grounded writing.
    """
    provider = get_provider()

    # Build context from gap analysis
    gap_summary = _build_gap_context(gap_analysis)

    user_message = f"""ORIGINAL POLICY:
{original_text}

GAP ANALYSIS FINDINGS (fix ALL of these):
{gap_summary}"""

    if jurisdiction:
        user_message += f"\n\nJurisdiction: {jurisdiction} — ensure state-specific requirements are incorporated."

    # Inject retrieved source material
    if retrieval_context and retrieval_context.formatted_context:
        user_message += f"\n\n{retrieval_context.formatted_context}"
    else:
        user_message += "\n\n⚠️ No retrieved source material is available. You MUST mark any regulatory text you incorporate as [MODEL INFERENCE] since it is not verified from loaded sources."

    logger.info(f"Generating rewritten policy — {len(original_text)} chars original, {len(gap_analysis.gap_table)} gaps to fix")

    raw_response = await provider.complete(
        system_prompt=_build_rewrite_system_prompt(industry),
        user_message=user_message,
        # Capped at 2500 (down from 4000) to keep rewrite wall-clock under ~45s
        # on gpt-4o-mini. Andrew explicitly chose shorter rewrites over longer
        # waits. Action-plan, checklist, and remediation calls still use the
        # full long ceiling — this cap is rewrite-only.
        max_tokens=2500,
        temperature=0.2,
    )

    data = _parse_json_response(raw_response)

    sections = []
    for sec_data in data.get("sections", []):
        sections.append(RewrittenPolicySection(
            section_title=sec_data.get("section_title", ""),
            original_text=sec_data.get("original_text", ""),
            rewritten_text=sec_data.get("rewritten_text", ""),
            changes_summary=sec_data.get("changes_summary", ""),
            regulation_refs=sec_data.get("regulation_refs", []),
        ))

    return RewrittenPolicy(
        policy_title=data.get("policy_title", "Rewritten Policy"),
        effective_date=data.get("effective_date", "Upon adoption"),
        version_note=data.get("version_note", "Compliance rewrite based on gap analysis"),
        sections=sections,
        full_text=data.get("full_text", ""),
        change_summary=data.get("change_summary", ""),
    )


async def generate_redline(
    original_text: str,
    rewritten_policy: RewrittenPolicy,
    gap_analysis: AnalysisResult,
    retrieval_context: Optional[RetrievalContext] = None,
) -> List[RedlineChange]:
    """
    Generate a redline document comparing original vs rewritten policy.
    If retrieval_context is provided, uses retrieved source material for verification.
    """
    provider = get_provider()

    user_message = f"""ORIGINAL POLICY:
{original_text}

REWRITTEN POLICY:
{rewritten_policy.full_text}

Compare these two documents and identify every substantive change. Focus on regulatory compliance changes."""

    # Inject retrieved source material for verification
    if retrieval_context and retrieval_context.formatted_context:
        user_message += f"\n\n{retrieval_context.formatted_context}"

    logger.info("Generating redline document")

    raw_response = await provider.complete(
        system_prompt=REDLINE_SYSTEM_PROMPT,
        user_message=user_message,
        max_tokens=settings.llm_max_tokens_long,
        temperature=0.1,
    )

    data = _parse_json_response(raw_response)

    changes = []
    for change_data in data.get("changes", []):
        changes.append(RedlineChange(
            type=change_data.get("type", "modified"),
            original_text=change_data.get("original_text"),
            revised_text=change_data.get("revised_text"),
            section=change_data.get("section"),
            regulation_ref=change_data.get("regulation_ref"),
        ))

    return changes


def _build_gap_context(gap_analysis: AnalysisResult) -> str:
    """Build a condensed gap analysis context for the rewrite prompt."""
    lines = []
    for row in gap_analysis.gap_table:
        if row.status == "compliant":
            continue
        lines.append(
            f"- [{row.status.upper()} | {row.risk_level.upper() if row.risk_level else ''}] "
            f"{row.clause}: {row.finding}\n"
            f"  Required by: {', '.join(row.regulations)}\n"
            f"  Suggested fix: {row.suggested_language}"
        )
    return "\n".join(lines) if lines else "No gaps found — policy is compliant."
