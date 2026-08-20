"""
Every prompt that touches untrusted text needs the confidentiality rule.

Assessment finding #15: no anti-disclosure instruction existed in any system
prompt, so the internal prompt engineering was likely extractable by asking.

A first pass added it to the analysis and chat prompts only. The drafting and
rewrite prompts build their own system prompts -- and both are handed uploaded
policy text and retrieved source documents, which are exactly the places an
injected instruction arrives. They carried no such rule at all.

Run: python -m pytest tests/test_prompt_confidentiality.py -v
"""

import pytest


def _analysis_prompt():
    from app.services.llm_service import _build_system_prompt
    return _build_system_prompt("healthcare", None)


def _chat_prompt():
    import app.services.chat_service as chat
    import inspect
    return inspect.getsource(chat)


def _draft_prompt():
    from app.services.draft_policy_service import _build_draft_system_prompt
    return _build_draft_system_prompt("manufacturing", None)


def _rewrite_prompt():
    from app.services.rewrite_service import _build_rewrite_system_prompt
    return _build_rewrite_system_prompt("healthcare")


ALL_PROMPTS = {
    "analysis": _analysis_prompt,
    "chat": _chat_prompt,
    "draft": _draft_prompt,
    "rewrite": _rewrite_prompt,
}


@pytest.mark.parametrize("name", sorted(ALL_PROMPTS))
def test_every_prompt_refuses_to_disclose_itself(name):
    text = ALL_PROMPTS[name]()
    assert "CONFIDENTIALITY" in text, f"the {name} prompt has no confidentiality rule"
    assert "never reveal" in text.lower() or "Never reveal" in text


@pytest.mark.parametrize("name", ["analysis", "draft", "rewrite"])
def test_document_text_is_data_not_instructions(name):
    """The prompts that receive uploaded documents must say so explicitly --
    that is the indirect prompt-injection path."""
    text = ALL_PROMPTS[name]()
    lowered = text.lower()
    assert "never instructions to follow" in lowered or "not a command" in lowered, (
        f"the {name} prompt does not tell the model that document text is data"
    )


def test_the_rule_is_shared_not_copied():
    """Four near-identical copies drift. One of them already had."""
    from app.services.llm_service import CONFIDENTIALITY_RULE
    assert "CONFIDENTIALITY" in CONFIDENTIALITY_RULE
    for name in ("draft", "rewrite"):
        assert CONFIDENTIALITY_RULE in ALL_PROMPTS[name]()
