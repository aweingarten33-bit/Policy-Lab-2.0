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
from app.services.draft_facts import ground_draft_facts
from app.services.obligation_checklists import checklist_for, missing_obligations, prompt_block
from app.services.state_coverage import state_coverage

logger = logging.getLogger(__name__)


def _build_draft_system_prompt(industry_slug: str, jurisdiction: Optional[str]) -> str:
    cfg = get_industry(industry_slug)

    prompt = (
        f"You are the most senior {cfg['name']} compliance attorney and policy writer in the United States. "
        f"You write complete, professional, regulation-compliant policy documents for {cfg['description']}.\n\n"
        f"A user will describe a policy they need. Your job is to write the full policy document from scratch — "
        f"complete and professional. Not an outline. You know almost nothing about this organization beyond the "
        f"description, so every organizational fact you were not given (who holds a role, what an internal "
        f"deadline is, when the policy takes effect) stays an explicit placeholder for the organization to fill "
        f"in. The draft is a starting point for the organization's own decisions, not a finished policy.\n\n"
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
        f"2. Cite an applicable regulation inline ONLY where one genuinely exists and appears in the REFERENCE "
        f"MATERIAL below, in the form 'As required by <exact citation>...'. Not every policy topic is "
        f"regulation-driven — an attendance policy, a dress code, or a communications style guide is mostly an "
        f"organizational-design choice with a narrow regulatory surface rather than a comprehensive framework. "
        f"Look for the genuine touchpoints in the reference material, but NEVER fabricate a citation to make a "
        f"design preference look like a legal requirement — write the clause as a professional best-practice "
        f"recommendation instead, with no citation attached. A user relying on this to know what's legally "
        f"required is actively harmed by an invented citation.\n"
        f"3. Use professional policy language — active voice, clear obligations, defined terms.\n"
        f"4. Include: Purpose, Scope, Definitions (if needed), Policy Statement, Procedures, Responsibilities, "
        f"Recordkeeping, Violations/Consequences, Review Schedule, Effective Date.\n"
        f"5. Tailor every clause to the specific regulatory requirements of {cfg['name']} where they genuinely apply.\n"
        f"6. Flag a recent regulatory update affecting this policy area ONLY if one appears in the REFERENCE "
        f"MATERIAL below, and only with its citation. Do not name a rule, a status, or a date from memory: "
        f"whether a rule is current, proposed, delayed or superseded is precisely the kind of fact that changes "
        f"after training. If the reference material shows no such update, say nothing about recent changes.\n"
        f"7. Every obligation must be specific, operable, and accountable — but NEVER invent the organization's "
        f"facts to get there. Each obligation names who does it, by when, and what record proves it happened. "
        f"When the description did not tell you who, write a placeholder instead of a title: "
        f"[ACCOUNTABLE ROLE 1], [ACCOUNTABLE ROLE 2] and so on, one number per distinct role, reused "
        f"consistently. Never make up a job title such as 'Director of Clinical Services' or 'Quality "
        f"Improvement Coordinator'. When the timing is an internal choice nobody gave you, write [TIMEFRAME]. "
        f"Never write a specific date unless the description supplied it; the effective date is [EFFECTIVE DATE]. "
        f"Vague words ('promptly', 'periodically', 'appropriate staff') are still not acceptable: a placeholder "
        f"is precise about what must be decided, a vague word hides it.\n"
        f"8. CRITICAL — separate REGULATORY deadlines from ORGANIZATIONAL ones. Requirement 7 tells you to pick "
        f"concrete numbers. That does NOT license you to present an invented number as legally mandated. Every "
        f"specific deadline, retention period, notification window, training frequency, or numeric threshold you "
        f"write falls into exactly one of two categories, and you must be honest about which:\n"
        f"   (a) LEGALLY MANDATED — the regulation itself fixes this number. Only state a number as a legal "
        f"requirement if that exact number appears in the REFERENCE MATERIAL provided below. Cite it. If the "
        f"reference material does not state the number, you do NOT know it — do not guess, and do not attach a "
        f"citation to a guess.\n"
        f"   (b) ORGANIZATIONAL CHOICE — the regulation requires that you have a policy, but the specific interval "
        f"is the organization's to set. Write [TIMEFRAME] with NO citation attached, and list it under "
        f"decisions_required — e.g. 'Records are retained for [TIMEFRAME] under this policy', never a number you "
        f"picked and never 'as required by [regulation]'.\n"
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
  "effective_date": "[EFFECTIVE DATE] — unless the description states the date, in which case that exact date",
  "version": "1.0",
  "regulations_applied": ["Only the regulations/statutes/guidance this policy was actually written to satisfy and that genuinely apply. No target number — list one if one applies. Never add an authority to lengthen the list."],
  "sections": [
    { "title": "I. Purpose", "content": "2-4 sentences — why this policy exists and what it achieves." },
    { "title": "II. Scope", "content": "2-4 sentences — who is covered, what activities, which locations/entities." },
    { "title": "III. Definitions", "content": "One sentence per term, only terms actually used elsewhere in this policy — not a general glossary." },
    { "title": "IV. Policy Statement", "content": "3-6 sentences — the core policy position and commitments." },
    { "title": "V. Procedures", "content": "Numbered steps, each one sentence: the action, who performs it ([ACCOUNTABLE ROLE n] unless the description named the role), and when (a period the cited regulation states, or [TIMEFRAME]). Cover the real procedure end-to-end without enumerating every hypothetical edge case." },
    { "title": "VI. Roles and Responsibilities", "content": "One to two sentences per role — the role ([ACCOUNTABLE ROLE n] unless named in the description) and exactly what it is responsible for, including the authority it needs to do it." },
    { "title": "VII. Recordkeeping", "content": "2-4 sentences — what records must be kept, the retention period (the period the cited regulation states, otherwise [TIMEFRAME]), storage requirements, and which role maintains them." },
    { "title": "VIII. Violations and Consequences", "content": "2-4 sentences — what constitutes a violation, reporting process, disciplinary consequences." },
    { "title": "IX. References", "content": "A list of the statutes, regulations, and guidance documents actually cited above — no additional prose." },
    { "title": "X. Review and Revision Schedule", "content": "1-3 sentences — how often reviewed, who is responsible, version control." }
  ],
  "decisions_required": ["One short line per placeholder or open choice: the placeholder, then what the organization must decide — e.g. '[ACCOUNTABLE ROLE 1] — who receives, logs and investigates complaints'. List every placeholder you used."],
  "drafting_notes": "2-3 sentences: which regulatory frameworks were applied, any recent update incorporated (only if it appeared in the reference material, with its citation — otherwise omit that clause entirely), and what legal review is recommended before adoption."
}

Do NOT include a "full_text" field in your JSON output. It is assembled from "sections"
after parsing — writing the whole document a second time as one block wastes output
budget better spent on section depth.

Keep every section focused and complete, not exhaustive — this is a policy document,
not a training manual or a legal brief. State the rule, the responsible role, and the
timeframe (or its placeholder); do not enumerate every hypothetical scenario or edge case.
A tightly-written real policy beats a padded one."""

    return prompt + "\n\n" + CONFIDENTIALITY_RULE


def _build_draft_user_prompt(policy_description: str, industry_slug: str, jurisdiction: Optional[str]) -> str:
    cfg = get_industry(industry_slug)
    org_type = cfg.get("description", cfg["name"] + " organization")

    prompt = f"Write a complete, regulation-compliant policy for a {org_type}"
    if jurisdiction:
        prompt += f" in {jurisdiction}"
    prompt += f".\n\nPolicy needed: {policy_description}\n\n"
    prompt += (
        "Write the full policy document. Every section must be complete — real sentences and real procedures, "
        "with regulatory citations only where the reference material supports them. Do not invent facts about "
        "this organization: any role, internal deadline or date not stated above is a placeholder "
        "([ACCOUNTABLE ROLE n], [TIMEFRAME], [EFFECTIVE DATE]) listed in decisions_required."
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
    obligations = prompt_block(checklist_for(industry_slug, policy_description))
    if obligations:
        user_message += "\n\n" + obligations

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


def finalize_draft(
    data: dict,
    ctx: Optional[RetrievalContext],
    policy_description: str,
    industry: Optional[str] = None,
    jurisdiction: Optional[str] = None,
) -> dict:
    """Everything a parsed draft goes through before anyone sees it.

    Order matters: invented facts are replaced first, so verification and the
    obligation check both run on the text the reader will actually get.
    """
    supplied = "\n".join(filter(None, [policy_description, jurisdiction]))
    reference = ctx.formatted_context if ctx is not None else ""
    data = ground_draft_facts(data, supplied, reference or "")

    checklist = checklist_for(industry or "healthcare", policy_description)
    data["missing_obligations"] = [
        f"{o.citation}: {o.requirement}" for o in missing_obligations(data.get("full_text", ""), checklist)
    ]

    if ctx is not None:
        data = attach_attribution(data, ctx)
    coverage = state_coverage(ctx, jurisdiction)
    data["state_coverage"] = coverage.model_dump() if coverage else None
    return data


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
    return finalize_draft(data, ctx, policy_description, industry, jurisdiction)


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
