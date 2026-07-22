"""
Compliance Chat Service.

Post-analysis and post-draft Q&A assistant. Answers questions about the
findings or the drafted policy using the context already generated — it does
not edit or rewrite policy text. Rewriting the gap analysis is handled by the
dedicated "Fix All Gaps" action; drafting a new policy is handled by the
dedicated Draft flow.
"""

import logging
from typing import Optional

from app.services.provider import get_provider
from app.models.schemas import ChatMessage

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """You are an expert compliance advisor built into a Policy Gap Analyzer tool. The user has just run a gap analysis or generated a policy draft and is asking follow-up questions about it.

Your role is Q&A only:
- Answer questions about the analysis findings or the drafted policy clearly and specifically
- Explain regulatory requirements in plain English, not legalese
- Give practical advice tailored to the findings (e.g. what to prioritize, what an auditor checks)
- Clarify what a regulation actually requires when asked

You do NOT rewrite or edit the policy document yourself — if the user wants the policy actually fixed or rewritten, tell them to use the "Fix All Gaps" button (for a gap analysis) or regenerate the draft, rather than attempting to produce replacement policy text yourself.

Tone: Confident, direct, helpful. Like a trusted compliance expert colleague, not a cautious legal bot. Don't over-hedge.

Note: You are not providing legal advice. Findings should be independently verified by qualified counsel for formal compliance determinations.

Keep responses concise — 2-3 paragraphs."""


async def chat(
    message: str,
    mode: str = "analysis",
    industry: Optional[str] = "healthcare",
    jurisdiction: Optional[str] = None,
    context_summary: Optional[str] = None,
    conversation_history: Optional[list[ChatMessage]] = None,
) -> str:
    """Run a single Q&A chat turn and return the response text."""
    provider = get_provider()
    history = conversation_history or []

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
            "content": "Understood — I have full context. What would you like to know?",
        })

    recent_history = history[-10:]
    for msg in recent_history:
        messages.append({"role": msg.role, "content": msg.content})

    messages.append({"role": "user", "content": message})

    logger.info(f"Chat turn — mode: {mode}, industry: {industry}, history: {len(history)}")

    response_text = await provider.complete_chat(
        system_prompt=CHAT_SYSTEM_PROMPT,
        messages=messages,
        max_tokens=2500,
        temperature=0.5,
    )

    return response_text.strip()
