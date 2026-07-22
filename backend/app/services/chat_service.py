"""
Compliance Chat Service.

Post-analysis and post-draft conversational assistant.
Uses Gemini (free tier) via the same provider as the rest of the system.
Maintains context from analysis results or drafted policy so the AI can answer
specific follow-up questions about findings or refine the drafted document.
"""

import logging
from typing import Optional

from app.services.provider import get_provider
from app.models.schemas import ChatMessage

logger = logging.getLogger(__name__)

ANALYSIS_SYSTEM_PROMPT = """You are an expert compliance advisor built into a Policy Gap Analyzer tool.

You have just completed a detailed analysis of a compliance policy and have full context on the findings. The user is asking follow-up questions about the findings, regulations, or how to fix specific gaps.

Your role:
- Answer questions about the analysis results clearly and specifically
- Explain regulatory requirements in plain English, not legalese
- Give actionable, practical advice tailored to the findings
- Help prioritize which gaps to fix first when asked
- Draft additional policy language on request
- Clarify what a regulation actually requires when asked
- Help the user understand what an auditor or inspector would actually check

Tone: Confident, direct, helpful. Like a trusted compliance expert colleague, not a cautious legal bot. Don't over-hedge. Give real answers.

Note: You are not providing legal advice. Findings should be independently verified by qualified counsel for formal compliance determinations.

Keep responses concise — 2-4 paragraphs unless the user asks for something detailed like a policy draft."""

DRAFT_SYSTEM_PROMPT = """You are an expert compliance policy writer built into a Policy Gap Analyzer tool.

You have just drafted a compliance policy document and have its full content in context. The user wants to refine, expand, or modify it.

Your role:
- Make specific edits to sections the user identifies
- Add new sections or subsections on request
- Rewrite content in simpler or more formal language on request
- Tailor the policy to specific contexts (a particular site, population, or scenario)
- Ensure all edits maintain regulatory compliance
- Explain why you wrote something a particular way if asked

When the user asks for a rewrite or addition, provide the actual revised text — not a description of what to change. Write ready-to-paste policy language.

Tone: Precise, professional, practical. This is a working policy document.

Note: All drafts should be reviewed by qualified compliance counsel before formal adoption."""

FOLLOW_UP_SUGGESTIONS: dict = {
    "analysis": {
        "healthcare": [
            "Which gap should I fix first given limited staff time?",
            "Draft the suggested language for the highest-risk finding",
            "What would an OCR auditor actually look for during a site visit?",
            "What training do we need to document to satisfy OIG Element 3?",
            "How quickly do we need to report a breach to HHS?",
            "Explain the minimum necessary standard in plain English",
        ],
        "home_health": [
            "What would a state surveyor actually check during a Conditions of Participation survey?",
            "Draft the suggested language for the highest-risk finding",
            "Which gaps put our Medicare certification at risk first?",
            "How often do RN supervisory visits need to happen for aide-only patients?",
            "What OASIS documentation do we need to support this finding?",
        ],
        "other": [
            "Which policy should we write first?",
            "Draft a whistleblower policy for our organization",
            "What would an employment attorney look for in an audit?",
            "How do we document conflict of interest disclosures properly?",
        ],
    },
    "draft": {
        "healthcare": [
            "Make the patient rights section more specific to telehealth",
            "Add a section covering our satellite clinic location",
            "Rewrite section 3 in simpler language for frontline staff",
            "Add a breach notification timeline to the security section",
            "Make the training requirements section more specific",
        ],
        "home_health": [
            "Add a section covering telehealth and remote patient monitoring visits",
            "Make the RN supervisory visit cadence more specific",
            "Rewrite the OASIS assessment section in simpler language for new clinicians",
            "Add a section for missed or late supervisory visits",
            "Make the emergency preparedness section specific to our patient population",
        ],
        "other": [
            "Make this more specific to our remote workforce",
            "Add a section covering contractor and vendor obligations",
            "Rewrite in simpler language for all staff to understand",
            "Add an incident reporting procedure",
            "Make the enforcement section more specific",
        ],
    },
}


def _get_follow_ups(mode: str, industry: Optional[str], message: str, response: str) -> list[str]:
    mode_suggestions = FOLLOW_UP_SUGGESTIONS.get(mode, FOLLOW_UP_SUGGESTIONS["analysis"])
    industry_key = (industry or "healthcare").lower()
    suggestions = mode_suggestions.get(industry_key, mode_suggestions.get("other", []))
    lower_msg = (message + response).lower()
    filtered = [s for s in suggestions if s.lower()[:30] not in lower_msg]
    return filtered[:3]


async def chat(
    message: str,
    mode: str = "analysis",
    industry: Optional[str] = "healthcare",
    jurisdiction: Optional[str] = None,
    context_summary: Optional[str] = None,
    conversation_history: Optional[list[ChatMessage]] = None,
) -> tuple[str, list[str]]:
    """
    Run a single chat turn.

    Args:
        mode: "analysis" (post-gap-analysis) or "draft" (post-policy-draft)

    Returns:
        (response_text, suggested_follow_ups)
    """
    provider = get_provider()
    history = conversation_history or []
    system_prompt = DRAFT_SYSTEM_PROMPT if mode == "draft" else ANALYSIS_SYSTEM_PROMPT

    messages: list[dict] = []

    if context_summary:
        parts = []
        if industry:
            parts.append(f"Industry: {industry}")
        if jurisdiction:
            parts.append(f"Jurisdiction: {jurisdiction}")
        parts.append(f"\n{context_summary}")
        context_block = "CONTEXT:\n" + "\n".join(parts)
        messages.append({"role": "user", "content": context_block})
        messages.append({
            "role": "assistant",
            "content": "Understood — I have full context. What would you like to know or change?",
        })

    recent_history = history[-10:]
    for msg in recent_history:
        messages.append({"role": msg.role, "content": msg.content})

    messages.append({"role": "user", "content": message})

    logger.info(f"Chat turn — mode: {mode}, industry: {industry}, history: {len(history)}")

    response_text = await provider.complete_chat(
        system_prompt=system_prompt,
        messages=messages,
        max_tokens=1200,
        temperature=0.5,
    )

    follow_ups = _get_follow_ups(mode, industry, message, response_text)
    return response_text.strip(), follow_ups
