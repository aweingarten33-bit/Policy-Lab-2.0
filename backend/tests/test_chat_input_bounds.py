"""Tests that every user-controlled chat context field is bounded before LLM use."""

import pytest

from app.models.schemas import ChatMessage, MAX_CHAT_CHARS, MAX_INPUT_CHARS
from app.services.chat_service import _validate_chat_inputs, MAX_CHAT_HISTORY_MESSAGES


def test_context_summary_is_bounded():
    with pytest.raises(ValueError, match="Chat context is too large"):
        _validate_chat_inputs("question", "x" * (MAX_INPUT_CHARS + 1), [])


def test_individual_history_message_is_bounded():
    history = [ChatMessage(role="user", content="x" * (MAX_CHAT_CHARS + 1))]
    with pytest.raises(ValueError, match="history message"):
        _validate_chat_inputs("question", None, history)


def test_only_the_last_ten_history_messages_are_used():
    history = [
        ChatMessage(role="user", content=f"message-{i}")
        for i in range(MAX_CHAT_HISTORY_MESSAGES + 5)
    ]

    recent = _validate_chat_inputs("question", None, history)

    assert len(recent) == MAX_CHAT_HISTORY_MESSAGES
    assert recent[0].content == "message-5"
    assert recent[-1].content == "message-14"
