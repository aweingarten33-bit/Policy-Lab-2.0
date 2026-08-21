"""Rewrite Service — generates a full policy from verified gap findings.

The rewrite path must not launder an unverified finding into mandatory policy
language. Gap evidence and obligation type are treated as inputs to the safety
boundary, not merely as display metadata.
"""

import json
import logging
import re
from typing import Optional

from app.config import settings
from app.models.schemas import (
    AnalysisResult,
    ObligationType,
    RewrittenPolicy,
    RewrittenPolicySection,
    VerificationStatus,
)
from app.services.industry_config import get_industry
from app.services.llm_service import CONFIDENTIALITY_RULE
from app.services.provider import get_provider
from app.services.retrieval.models import RetrievalContext

logger = logging.getLogger(__name__)

REWRITE_TASK_INSTRUCTIONS = """You will receive:
1. An original policy document
2. A gap analysis whose findings have verification/obligation labels

Rewrite the entire policy from start to finish. The rewritten policy must fix
confirmed gaps while preserving a strict distinction between law, guidance,
best practice, and organizational choice.

TRUTH RULES — these override every other instruction:
- A finding marked CONFIRMED LEGAL REQUIREMENT may be written as mandatory law.
- A finding marked GUIDANCE may be incorporated as recommended practice, not as
  a binding statutory/regulatory command unless the supplied source text itself
  imposes the duty.
- A finding marked UNVERIFIED REQUIREMENT must NOT be presented as law. You may
  adopt a sensible organizational standard if useful, but identify it as the
  organization's chosen standard and do not attach the unverified citation as
  proof that the law requires it.
- Do not invent or strengthen a citation. A real section number does not prove
  the surrounding claim.
- A number, deadline, retention period, percentage, dollar amount, age, ratio,
  frequency, threshold, or distance may be described as legally mandated only
  when that exact fact is present in the supplied authoritative material.
- Do not turn MAY / SHOULD / RECOMMENDS into MUST / SHALL / REQUIRED.
- If source material is insufficient, choose conservative organizational
  language rather than guessing what the law says.

Writing requirements:
- Fix every CONFIRMED legal gap and every policy-operability problem.
- Use precise, audit-ready language.
- Use regulatory citations only where the supplied authoritative material
  actually supports the proposition.
- Follow a professional policy structure appropriate to the selected industry.
- Return a complete ready-to-review policy, not an outline.

Return ONLY valid JSON:
{
  "policy_title": "Full title",
  "effective_date": "Upon adoption or a date",
  "version_note": "Short version note",
  "change_summary": "2-3 sentence summary",
  "sections": [
    {
      "section_title": "I. PURPOSE",
      "rewritten_text": "Complete section text",
      "changes_summary": "What changed and why",
      "regulation_refs": ["Only authorities actually relied upon"]
    }
  ]
}

Do not include original_text or full_text fields. Use 6-10 focused sections.
"""


def _build_rewrite_system_prompt(industry_slug: Optional[str] = None) -> str:
    cfg = get_industry(industry_slug or "healthcare")
    return cfg["persona"] + "\n\n" + REWRITE_TASK_INSTRUCTIONS + "\n\n" + CONFIDENTIALITY_RULE


def _parse_json_response(raw_text: str) -> dict:
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
                logger.warning("JSON repaired after strict parse failure: %s", strict_err)
                return repaired
            raise ValueError(f"json_repair returned non-dict: {type(repaired).__name__}")
        except Exception as repair_err:
            logger.error("JSON repair also failed: %s", repair_err)
            raise strict_err


def _finding_truth_label(row) -> str:
    """Translate evidence into a rewrite instruction that cannot overstate law."""
    evidence = getattr(row, "evidence", None)
    fully_verified = evidence is not None and evidence.status is VerificationStatus.verified

    if row.obligation_type is ObligationType.required and fully_verified:
        return "CONFIRMED LEGAL REQUIREMENT"
    if row.obligation_type is ObligationType.guidance:
        return "GUIDANCE — recommended/interpreted, not automatically binding law"
    if row.obligation_type is ObligationType.best_practice:
        return "BEST PRACTICE — organizational design choice"
    if row.obligation_type is ObligationType.organizational_choice:
        return "ORGANIZATIONAL CHOICE — not a legal mandate"
    return "UNVERIFIED REQUIREMENT — do not present as law"


def _build_gap_context(gap_analysis: AnalysisResult) -> str:
    """Build truth-aware rewrite context from the gap analysis."""
    lines = []
    for row in gap_analysis.gap_table:
        if str(row.status) == "compliant" or getattr(row.status, "value", row.status) == "compliant":
            continue

        label = _finding_truth_label(row)
        evidence = getattr(row, "evidence", None)
        excerpt = getattr(getattr(evidence, "source", None), "excerpt", None) if evidence else None
        evidence_note = getattr(evidence, "reason", "") if evidence else "No verification evidence available."

        lines.append(
            f"- [{label}] {row.clause}: {row.finding}\n"
            f"  Citation(s) asserted: {', '.join(row.regulations) if row.regulations else row.citation}\n"
            f"  Evidence status: {getattr(getattr(evidence, 'status', None), 'value', 'missing')}\n"
            f"  Evidence note: {evidence_note}\n"
            + (f"  Authoritative excerpt: {excerpt[:800]}\n" if excerpt else "")
            + f"  Suggested language from analysis: {row.suggested_language}\n"
            f"  REWRITE RULE: {'Treat as mandatory law only to the extent the excerpt supports it.' if label == 'CONFIRMED LEGAL REQUIREMENT' else 'Do not use the asserted citation to claim this is legally mandatory.'}"
        )
    return "\n\n".join(lines) if lines else "No gaps found — policy is compliant."


async def generate_rewritten_policy(
    original_text: str,
    gap_analysis: AnalysisResult,
    jurisdiction: Optional[str] = None,
    retrieval_context: Optional[RetrievalContext] = None,
    industry: Optional[str] = None,
) -> RewrittenPolicy:
    provider = get_provider()
    gap_summary = _build_gap_context(gap_analysis)

    user_message = f"""ORIGINAL POLICY:
{original_text}

VERIFIED / CLASSIFIED GAP ANALYSIS (follow the truth labels exactly):
{gap_summary}"""

    if jurisdiction:
        user_message += f"\n\nJurisdiction: {jurisdiction}. Do not import another state's law."

    if retrieval_context and retrieval_context.formatted_context:
        user_message += (
            "\n\nAUTHORITATIVE / RETRIEVED REFERENCE MATERIAL:\n"
            + retrieval_context.formatted_context
        )
    else:
        user_message += (
            "\n\nNo retrieved source material is available. Do not state new regulatory "
            "requirements or citations from model memory; use organizational language instead."
        )

    logger.info(
        "Generating truth-aware rewritten policy — %s chars original, %s findings",
        len(original_text),
        len(gap_analysis.gap_table),
    )

    raw_response = await provider.complete(
        system_prompt=_build_rewrite_system_prompt(industry),
        user_message=user_message,
        max_tokens=settings.llm_max_tokens_long,
        temperature=0.2,
    )
    data = _parse_json_response(raw_response)

    sections = []
    for sec_data in data.get("sections", []):
        sections.append(RewrittenPolicySection(
            section_title=sec_data.get("section_title", ""),
            original_text="",
            rewritten_text=sec_data.get("rewritten_text", ""),
            changes_summary=sec_data.get("changes_summary", ""),
            regulation_refs=sec_data.get("regulation_refs", []),
        ))

    full_text = "\n\n".join(
        f"{s.section_title}\n\n{s.rewritten_text}" for s in sections
    )

    return RewrittenPolicy(
        policy_title=data.get("policy_title", "Rewritten Policy"),
        effective_date=data.get("effective_date", "Upon adoption"),
        version_note=data.get("version_note", "Compliance rewrite based on verified gap analysis"),
        industry=industry,
        sections=sections,
        full_text=full_text,
        change_summary=data.get("change_summary", ""),
    )
