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
from app.services.provider import get_provider
from app.services.industry_config import get_industry
from app.services.retrieval.retriever import get_retriever

logger = logging.getLogger(__name__)


def _build_draft_system_prompt(industry_slug: str, jurisdiction: Optional[str]) -> str:
    cfg = get_industry(industry_slug)

    prompt = (
        f"You are the most senior {cfg['name']} compliance attorney and policy writer in the United States. "
        f"You write complete, professional, regulation-compliant policy documents for {cfg['description']}.\n\n"
        f"A user will describe a policy they need. Your job is to write the full policy document from scratch — "
        f"complete, professional, and ready to adopt. Not an outline. Not a template. The actual policy.\n\n"
        f"Requirements:\n"
        f"1. Write a COMPLETE policy — every section, every clause, fully fleshed out with real sentences.\n"
        f"2. Cite every applicable regulation inline (e.g., 'As required by 34 CFR §99.30...' or 'Per NY OCFS 18 NYCRR §418-1.11...').\n"
        f"3. Use professional policy language — active voice, clear obligations, defined terms.\n"
        f"4. Include: Purpose, Scope, Definitions (if needed), Policy Statement, Procedures, Responsibilities, "
        f"Recordkeeping, Violations/Consequences, Review Schedule, Effective Date.\n"
        f"5. Tailor every clause to the specific regulatory requirements of {cfg['name']}.\n"
        f"6. Flag any 2024-2026 regulatory updates that affected this policy area.\n\n"
        f"Key regulations to consider for {cfg['name']}:\n"
        + "\n".join(f"  • {r}" for r in cfg.get("regulations", []))
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
    { "title": "V. Procedures", "content": "Numbered steps, each one sentence: the action, the actor, and the timeframe. Cover the real procedure end-to-end without enumerating every hypothetical edge case." },
    { "title": "VI. Roles and Responsibilities", "content": "One to two sentences per role — who is responsible for what." },
    { "title": "VII. Recordkeeping", "content": "2-4 sentences — what records must be kept, retention periods, storage requirements." },
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

    return prompt


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
    return prompt


async def _prepare_draft(
    policy_description: str,
    industry: Optional[str],
    jurisdiction: Optional[str],
) -> tuple[str, str]:
    """Build the system/user prompts, injecting KB reference material if found."""
    industry_slug = industry or "healthcare"

    system_prompt = _build_draft_system_prompt(industry_slug, jurisdiction)
    user_message = _build_draft_user_prompt(policy_description, industry_slug, jurisdiction)

    logger.info(f"Drafting policy — industry: {industry_slug}, description: {policy_description[:80]}")

    # Retrieve reference material: real policy examples + templates from KB
    retriever = get_retriever()
    ctx = retriever.retrieve_for_step(
        step_name="draft_policy",
        policy_text=policy_description,
        policy_type="compliance_policy",
        jurisdiction=jurisdiction,
    )
    if ctx.total_sources_found > 0:
        user_message += (
            f"\n\nREFERENCE MATERIAL — use these real policy examples and templates "
            f"for language, structure, section headings, and depth. Match this caliber "
            f"of writing:\n\n{ctx.formatted_context}"
        )
        logger.info(f"Draft KB: {ctx.total_sources_found} reference chunks injected")

    return system_prompt, user_message


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

    # Normalize sections: GPT sometimes returns content as a nested dict of subsections.
    # Flatten any dict content into a plain string so the schema validates cleanly.
    for section in data.get("sections", []):
        content = section.get("content", "")
        if isinstance(content, dict):
            section["content"] = "\n\n".join(
                f"{k}\n{v}" if isinstance(v, str) else f"{k}\n{json.dumps(v, indent=2)}"
                for k, v in content.items()
            )
        elif not isinstance(content, str):
            section["content"] = str(content)

    # Built here instead of by the model — asking it to write the entire document a
    # second time as one block roughly doubled output size and was the main driver
    # of responses getting truncated before the JSON could close.
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
    """
    Generate a complete policy document from a plain-English description.
    Returns a dict with policy_title, full_text, sections, regulations_applied, etc.
    """
    provider = get_provider()
    system_prompt, user_message = await _prepare_draft(policy_description, industry, jurisdiction)

    raw_text = await provider.complete(
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=settings.llm_max_tokens_long,
        temperature=0.3,
        models=settings.llm_cascade_models_draft,
    )
    return parse_draft_response(raw_text)


async def draft_policy_stream(
    policy_description: str,
    industry: Optional[str] = None,
    jurisdiction: Optional[str] = None,
):
    """
    Same as draft_policy(), but yields raw text chunks as they're generated
    instead of waiting for the full response. The caller is responsible for
    accumulating the chunks and calling parse_draft_response() once the
    generator is exhausted.
    """
    provider = get_provider()
    system_prompt, user_message = await _prepare_draft(policy_description, industry, jurisdiction)

    async for chunk in provider.complete_stream(
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=settings.llm_max_tokens_long,
        temperature=0.3,
        models=settings.llm_cascade_models_draft,
    ):
        yield chunk
