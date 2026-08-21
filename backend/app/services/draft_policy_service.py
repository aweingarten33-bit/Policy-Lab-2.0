"""
Draft Policy Service — Generates a complete policy document from scratch.

The user provides a plain-English description of the policy they need.
The industry selection determines the regulatory framework.
No existing policy text is required.
"""

import logging
import re
import json
from typing import Optional

from app.config import settings
from app.services.orchestrator import GroundingUnavailableError
from app.services.provider import get_provider
from app.services.llm_service import CONFIDENTIALITY_RULE
from app.services.industry_config import get_industry, get_regulations
from app.services.retrieval.retriever import get_retriever
from app.services.retrieval.live_research import get_live_research_service
from app.services.retrieval.verification import get_verification_service
from app.services.retrieval.models import RetrievalContext

logger = logging.getLogger(__name__)


def _build_draft_system_prompt(industry_slug: str, jurisdiction: Optional[str]) -> str:
    cfg = get_industry(industry_slug)

    prompt = (
        f"You are the most senior {cfg['name']} compliance attorney and policy writer in the United States. "
        f"You write complete, professional, regulation-compliant policy documents for {cfg['description']}.\n\n"
        f"A user will describe a policy they need. Your job is to write the full policy document from scratch — "
        f"complete, professional, and ready to adopt. Not an outline. Not a template. The actual policy.\n\n"
        f"FIRST — is this actually a policy request? The description must be a genuine request for an "
        f"organizational policy or procedure. If it clearly isn't — random trivia, an off-topic question, a "
        f"story request, spam, or anything that was never attempting to describe a policy — do NOT invent a "
        f"policy to fill the schema. Instead return sections as an empty array, policy_title as 'Not a policy "
        f"request', and drafting_notes explaining that the description does not describe a policy and no draft "
        f"could be generated. A vague or minimal but genuine request ('a safety policy,' 'something about remote "
        f"work') is still real and should be drafted normally — this check is only for content that was never "
        f"attempting to describe a policy in the first place.\n\n"
        f"Requirements:\n"
        f"1. Write a COMPLETE policy — every section, every clause, fully fleshed out with real sentences.\n"
        f"2. Cite every applicable regulation inline ONLY where one genuinely exists (e.g., 'As required by 34 CFR "
        f"§99.30...'). Not every policy topic is regulation-driven — an attendance/lateness policy, a dress code, "
        f"or a communications style guide is mostly an organizational-design choice with narrow, specific "
        f"regulatory touchpoints (e.g. FLSA pay-docking rules, ADA accommodation) rather than a comprehensive "
        f"framework. Find the genuine touchpoints, but NEVER fabricate a citation to make a design preference "
        f"look like a legal requirement — write the clause as a professional best-practice recommendation instead, "
        f"with no citation attached. A user relying on this to know what's legally required is actively harmed by "
        f"an invented citation.\n"
        f"3. Use professional policy language — active voice, clear obligations, defined terms.\n"
        f"4. Include: Purpose, Scope, Definitions (if needed), Policy Statement, Procedures, Responsibilities, "
        f"Recordkeeping, Violations/Consequences, Review Schedule, Effective Date.\n"
        f"5. Tailor every clause to the specific regulatory requirements of {cfg['name']} where they genuinely apply.\n"
        f"6. Flag any 2024-2026 regulatory updates that affected this policy area.\n"
        f"7. Every obligation must be specific, operable, and accountable — not just present. This document will "
        f"later be run through an adversarial gap analysis that checks each obligation on exactly those three "
        f"axes, so write it to already pass: assign a specific named role or title (never 'appropriate staff,' "
        f"'management,' or 'the department'), give an exact timeframe, interval, or numeric threshold (never "
        f"'promptly,' 'periodically,' 'as needed,' 'as appropriate,' or 'in a timely manner'), and state what "
        f"evidence proves it happened wherever the obligation is the kind that gets audited (a log entry, a "
        f"signed form, a dated record, a specific retention period in days/months/years). A vague placeholder is "
        f"not acceptable anywhere a concrete answer is knowable — decide on a reasonable specific value rather "
        f"than hedging.\n"
        f"8. CRITICAL — separate REGULATORY deadlines from ORGANIZATIONAL ones. Requirement 7 tells you to pick "
        f"concrete numbers. That does NOT license you to present an invented number as legally mandated. Every "
        f"specific deadline, retention period, notification window, training frequency, or numeric threshold you "
        f"write falls into exactly one of two categories, and you must be honest about which:\n"
        f"   (a) LEGALLY MANDATED — the regulation itself fixes this number. Only state a number as a legal "
        f"requirement if that exact number appears in the REFERENCE MATERIAL provided below. Cite it. If the "
        f"reference material does not state the number, you do NOT know it — do not guess, and do not attach a "
        f"citation to a guess.\n"
        f"   (b) ORGANIZATIONAL CHOICE — the regulation requires that you have a policy, but the specific interval "
        f"is yours to set. Still pick a concrete, sensible value (per requirement 7), but write it as the "
        f"organization's chosen standard with NO citation attached — e.g. 'Records are retained for seven years "
        f"under this policy' rather than 'Records must be retained for seven years as required by [regulation].'\n"
        f"   When you are not certain which category a number falls into, treat it as (b). Overstating an "
        f"organizational preference as a legal mandate is the most harmful error you can make here: a user relying "
        f"on this to know what the law actually requires is misled about their real obligations.\n\n"
        f"Key regulations to consider for {cfg['name']} (apply only what's actually relevant to the requested policy):\n"
        + "\n".join(f"  • {r}" for r in get_regulations(industry_slug))
    )

    if jurisdiction:
        state_addendum = cfg.get("state_addendum", "")
        if state_addendum:
            prompt += "\n\n" + state_addendum.format(jurisdiction=jurisdiction)

    prompt += """

Return ONLY valid JSON — no markdown fences, no preamble. The sections array MUST follow this exact order:

{
  "policy_title": "Full formal title of the policy",
  "effective_date": "Suggested effective date (e.g., 'January 1, 2026')",
  "version": "1.0",
  "regulations_applied": ["Every regulation/statute/guidance this policy was written to satisfy"],
  "sections": [
    { "title": "I. Purpose", "content": "2-4 sentences — why this policy exists and what it achieves." },
    { "title": "II. Scope", "content": "2-4 sentences — who is covered, what activities, which locations/entities." },
    { "title": "III. Definitions", "content": "One sentence per term, only terms actually used elsewhere in this policy — not a general glossary." },
    { "title": "IV. Policy Statement", "content": "3-6 sentences — the core policy position and commitments." },
    { "title": "V. Procedures", "content": "Numbered steps, each one sentence: the action, the specific named role who performs it, and an exact timeframe (a number of hours/days, not 'promptly' or 'as needed'). Cover the real procedure end-to-end without enumerating every hypothetical edge case." },
    { "title": "VI. Roles and Responsibilities", "content": "One to two sentences per role — a specific named title (not 'management' or 'staff') and exactly what they are responsible for, including the authority they hold to do it." },
    { "title": "VII. Recordkeeping", "content": "2-4 sentences — what records must be kept, the exact retention period (a number of days/months/years, not 'an appropriate period'), storage requirements, and who is responsible for maintaining them." },
    { "title": "VIII. Violations and Consequences", "content": "2-4 sentences — what constitutes a violation, reporting process, disciplinary consequences." },
    { "title": "IX. References", "content": "A list of the statutes, regulations, and guidance documents actually cited above — no additional prose." },
    { "title": "X. Review and Revision Schedule", "content": "1-3 sentences — how often reviewed, who is responsible, version control." }
  ],
  "drafting_notes": "2-3 sentences: regulatory frameworks applied, any 2024-2026 updates incorporated, and what legal review is recommended before adoption."
}

Do NOT include a "full_text" field in your JSON output. It is assembled from "sections"
after parsing — writing the whole document a second time as one block wastes output
budget better spent on section depth.

Keep every section focused and complete, not exhaustive — this is a policy document,
not a training manual or a legal brief. State the rule, the responsible role, and the
timeframe; do not enumerate every hypothetical scenario or edge case. A tightly-written
real policy beats a padded one."""

    return prompt + "\n\n" + CONFIDENTIALITY_RULE


def _build_draft_user_prompt(policy_description: str, industry_slug: str, jurisdiction: Optional[str]) -> str:
    cfg = get_industry(industry_slug)
    org_type = cfg.get("description", cfg["name"] + " organization")

    prompt = f"Write a complete, regulation-compliant policy for a {org_type}"
    if jurisdiction:
        prompt += f" in {jurisdiction}"
    prompt += f".\n\nPolicy needed: {policy_description}\n\n"
    prompt += (
        "Write the full policy document. Every section must be complete — real sentences, real procedures, "
        "real regulatory citations. Make it ready to sign and adopt."
    )
    return prompt + "\n\n" + CONFIDENTIALITY_RULE


async def _prepare_draft(
    policy_description: str,
    industry: Optional[str],
    jurisdiction: Optional[str],
) -> tuple[str, str, RetrievalContext]:
    """Build the system/user prompts, injecting KB reference material if found.
    Also returns the RetrievalContext so callers can attach source attribution
    to the final drafted policy, the same way gap analysis does."""
    industry_slug = industry or "healthcare"

    system_prompt = _build_draft_system_prompt(industry_slug, jurisdiction)
    user_message = _build_draft_user_prompt(policy_description, industry_slug, jurisdiction)

    logger.info(f"Drafting policy — industry: {industry_slug}, description: {policy_description[:80]}")

    retriever = get_retriever()
    ctx = retriever.retrieve_for_step(
        step_name="draft_policy",
        policy_text=policy_description,
        policy_type="compliance_policy",
        jurisdiction=jurisdiction,
        industry=industry_slug,
    )

    ctx = await get_live_research_service().augment_retrieval_context(
        context=ctx,
        policy_type="compliance_policy",
        industry=industry_slug,
        jurisdiction=jurisdiction,
    )
    if ctx.live_research_used:
        logger.info(f"Draft live research: {len(ctx.live_research_results)} results injected")

    if settings.require_grounding and not ctx.get_all_sources():
        logger.error("Draft BLOCKED: zero sources retrieved — refusing ungrounded output.")
        raise GroundingUnavailableError(
            "Regulatory source verification is temporarily unavailable, so this "
            "draft was not generated. Please try again shortly."
        )

    if ctx.total_sources_found > 0:
        user_message += (
            "\n\nREFERENCE MATERIAL follows. Treat primary statutes, regulations, and official "
            "agency material as the only evidence that a legal obligation exists. Policy "
            "examples, templates, clause libraries, and peer language are structural/writing "
            "references only and MUST NOT be used to establish that something is legally "
            "required. If the primary authority does not state the requirement, do not present "
            "it as law.\n\n"
            f"{ctx.formatted_context}"
        )
        logger.info(f"Draft KB: {ctx.total_sources_found} reference chunks injected")

    return system_prompt, user_message, ctx


def attach_attribution(data: dict, ctx: RetrievalContext) -> dict:
    """Attach a fail-closed citation-verification summary to a draft.

    ``verified`` means fully verified. A citation that merely exists is only
    partially verified and must count as requiring review. This prevents the
    Draft flow from saying "All citations verified" when every citation only
    passed the citation-existence check.
    """
    verifier = get_verification_service()
    report = verifier.verify_section(
        section_name="draft_policy",
        section_text=data.get("full_text", ""),
        retrieval_context=ctx,
    )
    sources = ctx.get_source_names()
    data["kb_sources_used"] = sources or None
    data["kb_source_urls"] = ctx.get_source_url_map() or None
    data["source_snippets"] = ctx.get_source_snippets() or None
    data["live_research_used"] = ctx.live_research_used

    not_fully_verified = (
        report.partially_verified_claims
        + report.unverified_claims
        + report.contradicted_claims
    )
    data["unverified_claim_count"] = not_fully_verified

    if report.total_claims == 0:
        if ctx.total_sources_found > 0:
            data["verification_overall"] = (
                f"No specific citations detected for verification. Draft based on "
                f"{ctx.total_sources_found} retrieved source chunks. All content "
                f"should be independently verified."
            )
        else:
            data["verification_overall"] = (
                "No source material was available in the knowledge base. This draft "
                "is model inference only and MUST be independently verified."
            )
    elif not_fully_verified > 0:
        details = []
        if report.partially_verified_claims:
            details.append(f"{report.partially_verified_claims} partially verified")
        if report.unverified_claims:
            details.append(f"{report.unverified_claims} unverified")
        if report.contradicted_claims:
            details.append(f"{report.contradicted_claims} contradicted")
        data["verification_overall"] = (
            f"{not_fully_verified} of {report.total_claims} citation-backed claim(s) are "
            f"not fully verified ({', '.join(details)}). Do not treat those statements "
            f"as confirmed legal requirements until the cited authority is checked."
        )
    elif report.verified_claims == report.total_claims:
        data["verification_overall"] = (
            f"All {report.total_claims} citation-backed claim(s) are fully verified against "
            f"{len(sources)} authoritative source(s). Content should still be independently "
            f"confirmed before adoption."
        )
    else:
        # Defensive branch: aggregate counts should normally cover every claim.
        data["unverified_claim_count"] = report.total_claims
        data["verification_overall"] = (
            "Verification results were internally incomplete. Treat every citation-backed "
            "claim in this draft as unverified until independently confirmed."
        )

    return data


def parse_draft_response(raw_text: str) -> dict:
    """Parse the model's raw JSON response into the drafted-policy dict."""
    if not raw_text.strip():
        raise ValueError("Empty response from model")

    cleaned = re.sub(r"```(?:json)?\s*", "", raw_text)
    cleaned = re.sub(r"```\s*", "", cleaned)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        raise ValueError("No JSON found in model response")

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        logger.error(f"Draft JSON parse error: {e}. Response length: {len(match.group(0))} chars. Tail: {match.group(0)[-300:]!r}")
        raise ValueError(f"Invalid JSON from model: {e}")

    for section in data.get("sections", []):
        content = section.get("content", "")
        if isinstance(content, dict):
            section["content"] = "\n\n".join(
                f"{k}\n{v}" if isinstance(v, str) else f"{k}\n{json.dumps(v, indent=2)}"
                for k, v in content.items()
            )
        elif not isinstance(content, str):
            section["content"] = str(content)

    data["full_text"] = "\n\n".join(
        f"{s.get('title', '')}\n\n{s.get('content', '')}" for s in data.get("sections", [])
    )

    logger.info(f"Policy drafted: {data.get('policy_title', 'Untitled')} — {len(data.get('sections', []))} sections")
    return data


async def draft_policy(
    policy_description: str,
    industry: Optional[str] = None,
    jurisdiction: Optional[str] = None,
) -> dict:
    """Generate a complete policy document from a plain-English description."""
    provider = get_provider()
    system_prompt, user_message, ctx = await _prepare_draft(policy_description, industry, jurisdiction)

    raw_text = await provider.complete(
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=settings.llm_max_tokens_long,
        temperature=0.3,
        models=settings.llm_cascade_models_draft,
    )
    data = parse_draft_response(raw_text)
    return attach_attribution(data, ctx)


async def draft_policy_stream(
    policy_description: str,
    industry: Optional[str] = None,
    jurisdiction: Optional[str] = None,
    context_holder: Optional[dict] = None,
):
    """Stream a draft while exposing retrieval context to the caller."""
    provider = get_provider()
    system_prompt, user_message, ctx = await _prepare_draft(policy_description, industry, jurisdiction)
    if context_holder is not None:
        context_holder["ctx"] = ctx

    async for chunk in provider.complete_stream(
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=settings.llm_max_tokens_long,
        temperature=0.3,
        models=settings.llm_cascade_models_draft,
    ):
        yield chunk
