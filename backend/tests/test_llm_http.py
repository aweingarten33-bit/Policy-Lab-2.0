"""
The direct provider calls that replaced LiteLLM (which cost ~200 MB of a 512 MB
instance and caused out-of-memory restarts).

The expected request bodies below were captured from LiteLLM 1.97.0 itself, by
intercepting the HTTP request it built for each model with the parameters the
app uses. Behaviour stays identical only if these match exactly.

Run: python -m pytest tests/test_llm_http.py -v
"""

import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.services import llm_http
from app.services.provider import LLMProvider

MSGS = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]

# Captured from LiteLLM 1.97.0 (completion / acompletion, thinking disabled).
LITELLM_DEEPSEEK = {
    "url": "https://api.deepseek.com/beta/chat/completions",
    "body": {"model": "deepseek-flash", "messages": MSGS, "temperature": 0.0, "max_tokens": 1234,
             "thinking": {"type": "disabled"}},
}
LITELLM_DEEPSEEK_STREAM_BODY = {"model": "deepseek-flash", "messages": MSGS, "temperature": 0.0, "stream": True,
                                "max_tokens": 1234, "thinking": {"type": "disabled"}}
LITELLM_GEMINI = {
    "url": "https://generativelanguage.googleapis.com/v1alpha/models/gemini-3.8-flash:generateContent",
    "body": {"contents": [{"role": "user", "parts": [{"text": "USER"}]}],
             "system_instruction": {"parts": [{"text": "SYS"}]},
             "generationConfig": {"temperature": 1.0, "max_output_tokens": 1234,
                                  "thinkingConfig": {"includeThoughts": False}}},
}
LITELLM_GEMINI_CHAT_CONTENTS = [
    {"role": "user", "parts": [{"text": "U1"}]},
    {"role": "model", "parts": [{"text": "A1"}]},
    {"role": "user", "parts": [{"text": "U2"}, {"text": "U3"}]},
]


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "dk-test")
    monkeypatch.setattr(settings, "gemini_api_key", "gk-test")
    monkeypatch.setattr(llm_http, "RETRY_BACKOFF", (0.0, 0.0))


class Fake:
    """An httpx transport that records requests and replays canned responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _install(monkeypatch, fake):
    monkeypatch.setattr(llm_http, "_TRANSPORT", httpx.MockTransport(fake))


def _deepseek_ok(text="hello", finish="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": finish}]})


def _gemini_ok(text="hello", finish="STOP"):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]})


class TestRequestsMatchLiteLLM:
    def test_deepseek(self):
        provider, url, headers, body = llm_http.build_request("deepseek/deepseek-flash", MSGS, 1234, 0.0)
        assert url == LITELLM_DEEPSEEK["url"]
        assert body == LITELLM_DEEPSEEK["body"]
        assert list(body) == list(LITELLM_DEEPSEEK["body"])
        assert headers["Authorization"] == "Bearer dk-test"

    def test_deepseek_stream(self):
        _, _, _, body = llm_http.build_request("deepseek/deepseek-flash", MSGS, 1234, 0.0, stream=True)
        assert body == LITELLM_DEEPSEEK_STREAM_BODY

    def test_gemini(self):
        _, url, headers, body = llm_http.build_request("gemini/gemini-3.8-flash", MSGS, 1234, 1.0)
        assert url == LITELLM_GEMINI["url"]
        assert body == LITELLM_GEMINI["body"]
        assert headers["x-goog-api-key"] == "gk-test"

    def test_gemini_stream_url(self):
        _, url, _, _ = llm_http.build_request("gemini/gemini-3.8-flash", MSGS, 1234, 1.0, stream=True)
        assert url.endswith("gemini-3.8-flash:streamGenerateContent?alt=sse")

    def test_gemini_multi_turn(self):
        msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "U1"},
                {"role": "assistant", "content": "A1"}, {"role": "user", "content": "U2"},
                {"role": "user", "content": "U3"}]
        _, _, _, body = llm_http.build_request("gemini/gemini-3.8-flash", msgs, 1234, 1.0)
        assert body["contents"] == LITELLM_GEMINI_CHAT_CONTENTS

    def test_gemini_3_still_gets_temperature_1(self, monkeypatch):
        fake = Fake(_gemini_ok())
        _install(monkeypatch, fake)
        LLMProvider()._call_model("gemini/gemini-3.8-flash", MSGS, 100, 0.0)
        assert json.loads(fake.requests[0].content)["generationConfig"]["temperature"] == 1.0

    def test_unknown_provider_is_refused(self):
        with pytest.raises(ValueError, match="not supported"):
            llm_http.build_request("anthropic/claude", MSGS, 10, 0.0)


class TestResponses:
    def test_deepseek_text(self, monkeypatch):
        _install(monkeypatch, Fake(_deepseek_ok("the answer")))
        assert LLMProvider()._call_model("deepseek/deepseek-flash", MSGS, 100, 0.0) == "the answer"

    def test_gemini_skips_thought_parts(self, monkeypatch):
        resp = httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": "thinking...", "thought": True}, {"text": "the answer"}]}, "finishReason": "STOP"}]})
        _install(monkeypatch, Fake(resp))
        assert LLMProvider()._call_model("gemini/gemini-3.8-flash", MSGS, 100, 1.0) == "the answer"

    def test_max_tokens_is_still_an_error(self, monkeypatch):
        _install(monkeypatch, Fake(_gemini_ok("cut off", "MAX_TOKENS")))
        with pytest.raises(ValueError, match="max_tokens"):
            LLMProvider()._call_model("gemini/gemini-3.8-flash", MSGS, 100, 1.0)

    def test_empty_response_is_an_error(self, monkeypatch):
        _install(monkeypatch, Fake(httpx.Response(200, json={"candidates": []})))
        with pytest.raises(ValueError, match="Empty response"):
            LLMProvider()._call_model("gemini/gemini-3.8-flash", MSGS, 100, 1.0)

    def test_provider_error_message_survives(self, monkeypatch):
        body = {"error": {"message": "Insufficient Balance", "type": "unknown_error"}}
        _install(monkeypatch, Fake(httpx.Response(402, json=body)))
        with pytest.raises(llm_http.ProviderHTTPError, match="402.*Insufficient Balance"):
            LLMProvider()._call_model("deepseek/deepseek-flash", MSGS, 100, 0.0)


class TestRetries:
    def test_transient_errors_are_retried(self, monkeypatch):
        fake = Fake(httpx.Response(503, text="busy"), httpx.ConnectError("reset"), _deepseek_ok("ok"))
        _install(monkeypatch, fake)
        assert LLMProvider()._call_model("deepseek/deepseek-flash", MSGS, 100, 0.0) == "ok"
        assert len(fake.requests) == 3

    def test_retries_are_bounded(self, monkeypatch):
        fake = Fake(*[httpx.Response(429, text="slow down")] * 3)
        _install(monkeypatch, fake)
        with pytest.raises(llm_http.ProviderHTTPError, match="429"):
            LLMProvider()._call_model("deepseek/deepseek-flash", MSGS, 100, 0.0)
        assert len(fake.requests) == 3

    def test_client_errors_are_not_retried(self, monkeypatch):
        fake = Fake(httpx.Response(401, text="bad key"))
        _install(monkeypatch, fake)
        with pytest.raises(llm_http.ProviderHTTPError):
            LLMProvider()._call_model("deepseek/deepseek-flash", MSGS, 100, 0.0)
        assert len(fake.requests) == 1

    def test_cascade_falls_back_after_retries(self, monkeypatch):
        fake = Fake(*[httpx.Response(500, text="down")] * 3, _gemini_ok("from gemini"))
        _install(monkeypatch, fake)
        out = LLMProvider()._cascade(MSGS, 100, 0.0, ["deepseek/deepseek-flash", "gemini/gemini-3.8-flash"])
        assert out == "from gemini"


def _sse(*events):
    return httpx.Response(200, content="".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
                          + b"data: [DONE]\n\n", headers={"content-type": "text/event-stream"})


class TestStreaming:
    def _collect(self, model, temperature=0.0):
        async def go():
            return [c async for c in LLMProvider().complete_stream("SYS", "USER", max_tokens=100,
                                                                   temperature=temperature, models=[model])]
        return asyncio.run(go())

    def test_deepseek_stream(self, monkeypatch):
        _install(monkeypatch, Fake(_sse(
            {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
        )))
        assert "".join(self._collect("deepseek/deepseek-flash")) == "Hello"

    def test_gemini_stream(self, monkeypatch):
        _install(monkeypatch, Fake(_sse(
            {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "lo"}]}, "finishReason": "STOP"}]},
        )))
        assert "".join(self._collect("gemini/gemini-3.8-flash", 1.0)) == "Hello"

    def test_stream_retries_before_output(self, monkeypatch):
        fake = Fake(httpx.Response(503, text="busy"),
                    _sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}))
        _install(monkeypatch, fake)
        assert self._collect("deepseek/deepseek-flash") == ["ok"]
        assert len(fake.requests) == 2

    def test_stream_hitting_max_tokens_raises(self, monkeypatch):
        _install(monkeypatch, Fake(_sse({"choices": [{"delta": {"content": "x"}, "finish_reason": "length"}]})))
        with pytest.raises(ValueError, match="max_tokens"):
            self._collect("deepseek/deepseek-flash")


def test_ensemble_uses_the_same_path(monkeypatch):
    # The ensemble runs both models at once, so answer by host, not by order.
    monkeypatch.setattr(llm_http, "_TRANSPORT", httpx.MockTransport(
        lambda req: _deepseek_ok("a") if req.url.host == "api.deepseek.com" else _gemini_ok("b")))
    out = asyncio.run(LLMProvider().complete_ensemble(
        "SYS", "USER", ["deepseek/deepseek-flash", "gemini/gemini-3.8-flash"], max_tokens=100))
    assert sorted(t for _, t in out) == ["a", "b"]
