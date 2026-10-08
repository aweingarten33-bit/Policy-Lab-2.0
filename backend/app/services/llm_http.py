"""
Direct HTTP calls to the model providers, replacing LiteLLM.

LiteLLM cost ~200 MB of memory on import -- most of a 512 MB Render instance --
and the out-of-memory restarts it caused were killing analyses and exports
mid-run. The app only ever calls two providers, and both are plain JSON over
HTTPS, so they are called here with httpx, which was already a dependency.

The requests are the ones LiteLLM 1.97.0 sent, captured byte-for-byte:

  deepseek/<model>  POST https://api.deepseek.com/beta/chat/completions
                    Bearer DEEPSEEK_API_KEY
                    {"model", "messages", "temperature", "max_tokens",
                     "thinking": {"type": "disabled"}, ["stream": true]}

  gemini/<model>    POST https://generativelanguage.googleapis.com/v1alpha/
                         models/<model>:generateContent
                         (streamGenerateContent?alt=sse when streaming)
                    x-goog-api-key: GEMINI_API_KEY
                    {"contents", "system_instruction",
                     "generationConfig": {"temperature", "max_output_tokens",
                                          "thinkingConfig": {"includeThoughts": false}}}
                    System messages become system_instruction, "assistant"
                    becomes "model", and consecutive same-role turns merge.

Both functions return (text, finish_reason) with finish_reason normalised to
the OpenAI vocabulary the provider layer checks ("stop", "length", ...).

Transient failures -- connection errors, 429 and 5xx -- are retried here, with
backoff, before the cascade moves to the next model. A read timeout is not
retried: the model was working and a second wait would only double the delay
before the cascade's fallback.
"""

from __future__ import annotations

import json
import logging
import time
import asyncio
from typing import AsyncIterator, Dict, List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

DEEPSEEK_URL = "https://api.deepseek.com/beta/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1alpha/models/{model}:{method}"

RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_ATTEMPTS = 3            # one call plus two retries
RETRY_BACKOFF = (1.0, 2.0)    # seconds before the 2nd and 3rd attempts
RETRY_AFTER_CAP = 8.0
# Tests substitute an httpx.MockTransport here; None means the real network.
_TRANSPORT = None

_RETRY_EXCEPTIONS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ReadError)

_GEMINI_FINISH = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "SPII": "content_filter",
}


class ProviderHTTPError(Exception):
    """A provider answered with an error status. The message carries the status
    and the provider's own body, so "Insufficient Balance", "429" and "401" stay
    visible to the cascade summary and the provider check."""

    def __init__(self, provider: str, status: int, body: str):
        self.status = status
        super().__init__(f"{provider} HTTP {status}: {body[:600]}")


# ── Requests ──────────────────────────────────────────────────────────────────

def _split(model: str) -> Tuple[str, str]:
    provider, _, name = model.partition("/")
    if not name:
        raise ValueError(f"Model {model!r} has no provider prefix (expected 'deepseek/...' or 'gemini/...')")
    return provider, name


def _deepseek_request(name: str, messages: List[dict], max_tokens: int, temperature: float, stream: bool):
    if not settings.deepseek_api_key:
        raise ValueError("DEEPSEEK_API_KEY is not set")
    body: Dict = {"model": name, "messages": messages, "temperature": temperature}
    if stream:
        body["stream"] = True
    body["max_tokens"] = max_tokens
    body["thinking"] = {"type": "disabled"}
    headers = {"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"}
    return DEEPSEEK_URL, headers, body


def _gemini_contents(messages: List[dict]) -> Tuple[List[dict], List[dict]]:
    system_parts, contents = [], []
    for m in messages:
        text = m.get("content") or ""
        if m.get("role") == "system":
            system_parts.append({"text": text})
            continue
        role = "model" if m.get("role") == "assistant" else "user"
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].append({"text": text})
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    return system_parts, contents


def _gemini_request(name: str, messages: List[dict], max_tokens: int, temperature: float, stream: bool):
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not set")
    system_parts, contents = _gemini_contents(messages)
    body: Dict = {"contents": contents}
    if system_parts:
        body["system_instruction"] = {"parts": system_parts}
    body["generationConfig"] = {
        "temperature": temperature,
        "max_output_tokens": max_tokens,
        "thinkingConfig": {"includeThoughts": False},
    }
    method = "streamGenerateContent?alt=sse" if stream else "generateContent"
    url = GEMINI_URL.format(model=name, method=method)
    headers = {"x-goog-api-key": settings.gemini_api_key, "Content-Type": "application/json"}
    return url, headers, body


def build_request(model: str, messages: List[dict], max_tokens: int, temperature: float, stream: bool = False):
    """(provider, url, headers, json body) for one call."""
    provider, name = _split(model)
    if provider == "deepseek":
        return (provider, *_deepseek_request(name, messages, max_tokens, temperature, stream))
    if provider == "gemini":
        return (provider, *_gemini_request(name, messages, max_tokens, temperature, stream))
    raise ValueError(
        f"Model provider {provider!r} is not supported. The cascade uses deepseek/ and gemini/ models."
    )


# ── Responses ─────────────────────────────────────────────────────────────────

def _gemini_text(payload: dict) -> Tuple[str, Optional[str]]:
    candidates = payload.get("candidates") or []
    if not candidates:
        return "", None
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    reason = cand.get("finishReason")
    return text, (_GEMINI_FINISH.get(reason, reason.lower()) if reason else None)


def parse_response(provider: str, payload: dict) -> Tuple[str, Optional[str]]:
    if provider == "gemini":
        return _gemini_text(payload)
    choice = (payload.get("choices") or [{}])[0]
    return (choice.get("message") or {}).get("content") or "", choice.get("finish_reason")


def parse_stream_event(provider: str, data: str) -> Tuple[str, Optional[str]]:
    """(text delta, finish_reason) from one SSE data line."""
    payload = json.loads(data)
    if provider == "gemini":
        return _gemini_text(payload)
    choice = (payload.get("choices") or [{}])[0]
    return (choice.get("delta") or {}).get("content") or "", choice.get("finish_reason")


def _sse_data(line: str) -> Optional[str]:
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    return None if not data or data == "[DONE]" else data


# ── Retry ─────────────────────────────────────────────────────────────────────

def _retry_delay(attempt: int, response: Optional[httpx.Response]) -> float:
    delay = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
    if response is not None:
        try:
            delay = max(delay, min(float(response.headers.get("retry-after", "")), RETRY_AFTER_CAP))
        except ValueError:
            pass
    return delay


def _timeout(seconds: float) -> httpx.Timeout:
    return httpx.Timeout(seconds, connect=min(seconds, 15.0))


# ── Calls ─────────────────────────────────────────────────────────────────────

def complete_sync(model: str, messages: List[dict], max_tokens: int, temperature: float, timeout: float):
    """One non-streaming call, with retries. Returns (text, finish_reason)."""
    provider, url, headers, body = build_request(model, messages, max_tokens, temperature)
    with httpx.Client(timeout=_timeout(timeout), transport=_TRANSPORT) as client:
        for attempt in range(RETRY_ATTEMPTS):
            last = attempt == RETRY_ATTEMPTS - 1
            try:
                response = client.post(url, headers=headers, json=body)
            except _RETRY_EXCEPTIONS as e:
                if last:
                    raise
                logger.info("%s: %s, retrying", model, type(e).__name__)
                time.sleep(_retry_delay(attempt, None))
                continue
            if response.status_code in RETRY_STATUSES and not last:
                logger.info("%s: HTTP %s, retrying", model, response.status_code)
                time.sleep(_retry_delay(attempt, response))
                continue
            if response.status_code >= 400:
                raise ProviderHTTPError(provider, response.status_code, response.text)
            return parse_response(provider, response.json())
    raise RuntimeError("unreachable")


async def complete_async(model: str, messages: List[dict], max_tokens: int, temperature: float, timeout: float):
    """Async twin of complete_sync."""
    provider, url, headers, body = build_request(model, messages, max_tokens, temperature)
    async with httpx.AsyncClient(timeout=_timeout(timeout), transport=_TRANSPORT) as client:
        for attempt in range(RETRY_ATTEMPTS):
            last = attempt == RETRY_ATTEMPTS - 1
            try:
                response = await client.post(url, headers=headers, json=body)
            except _RETRY_EXCEPTIONS as e:
                if last:
                    raise
                logger.info("%s: %s, retrying", model, type(e).__name__)
                await asyncio.sleep(_retry_delay(attempt, None))
                continue
            if response.status_code in RETRY_STATUSES and not last:
                logger.info("%s: HTTP %s, retrying", model, response.status_code)
                await asyncio.sleep(_retry_delay(attempt, response))
                continue
            if response.status_code >= 400:
                raise ProviderHTTPError(provider, response.status_code, response.text)
            return parse_response(provider, response.json())
    raise RuntimeError("unreachable")


async def stream_async(
    model: str, messages: List[dict], max_tokens: int, temperature: float, timeout: float
) -> AsyncIterator[Tuple[str, Optional[str]]]:
    """Yield (text delta, finish_reason) pairs. Retries only before the stream opens."""
    provider, url, headers, body = build_request(model, messages, max_tokens, temperature, stream=True)
    yielded = False
    async with httpx.AsyncClient(timeout=_timeout(timeout), transport=_TRANSPORT) as client:
        for attempt in range(RETRY_ATTEMPTS):
            last = attempt == RETRY_ATTEMPTS - 1
            try:
                async with client.stream("POST", url, headers=headers, json=body) as response:
                    if response.status_code in RETRY_STATUSES and not last:
                        logger.info("%s: HTTP %s, retrying", model, response.status_code)
                        delay = _retry_delay(attempt, response)
                        await response.aread()
                    elif response.status_code >= 400:
                        raise ProviderHTTPError(provider, response.status_code, (await response.aread()).decode(errors="replace"))
                    else:
                        async for line in response.aiter_lines():
                            data = _sse_data(line)
                            if data is not None:
                                yielded = True
                                yield parse_stream_event(provider, data)
                        return
            except _RETRY_EXCEPTIONS as e:
                # Once output has reached the caller a retry would repeat it.
                if last or yielded:
                    raise
                logger.info("%s: %s, retrying", model, type(e).__name__)
                delay = _retry_delay(attempt, None)
            await asyncio.sleep(delay)
