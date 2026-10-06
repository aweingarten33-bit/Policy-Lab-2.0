"""
LLM Provider Abstraction — Cascade fallback across multiple AI providers.

Cascade order (uses whichever keys are configured):
  1. Groq / Llama 3.3 70B   — free, very fast, high limits
  2. Gemini / Flash          — free tier, Google
  3. Mistral Small           — free tier, European
  4. OpenRouter / Llama free — aggregator, free models
  5. Claude Sonnet           — paid, best quality for compliance
  6. GPT-4o Mini             — paid, OpenAI

If a model fails for any reason (rate limit, quota, error), the next one
in the list is tried automatically. Only fails completely if ALL are exhausted.

FIXES APPLIED:
  1. Added timeout=45.0 + num_retries=0 to ALL litellm calls — prevents
     models from hanging indefinitely and wasting time before cascade kicks in.
  2. Made complete() and complete_chat() async — the original sync versions
     blocked FastAPI's event loop on Replit, starving all other requests.
     They now offload to a thread executor so the loop stays free.
  3. complete_ensemble() already used acompletion — unchanged, just got timeout fix.
"""

import asyncio
import re
import logging
from typing import Optional, List

import litellm

from app.config import settings

logger = logging.getLogger(__name__)

litellm.suppress_debug_info = True
# Some models (e.g. claude-opus-4-8) reject params other models accept fine
# (it only supports temperature=1). Drop unsupported params per-model instead
# of erroring out, so the same call site works across the whole cascade.
litellm.drop_params = True


def _temperature_for(model: str, requested: float) -> float:
    """Gemini 3 models are documented to loop or degrade below temperature
    1.0, so they always get 1.0; every other model gets what was asked for."""
    if model.startswith("gemini/gemini-3"):
        return 1.0
    return requested


def _summarize_error(error: Exception) -> str:
    """Short, readable reason for one model's failure.

    Provider errors usually embed a JSON body; pull out its "message" field
    (e.g. "Insufficient Balance") instead of the raw dump, so every model's
    reason fits in the final cascade error rather than only the last one's.
    """
    text = str(error)
    match = re.search(r'"message"\s*:\s*"([^"]+)"', text)
    reason = match.group(1) if match else text.split("\n", 1)[0]
    reason = re.sub(r"\s*\(request_id:[^)]*\)?", "", reason).strip()
    return f"{type(error).__name__}: {reason[:160]}"

# How long (seconds) to wait for any single model before giving up and trying the next.
# Long-form policy generation (max_tokens >= 2000) uses _MODEL_TIMEOUT_LONG since
# 4000-token rewrites legitimately take 60-90s on gpt-4o-mini and we don't want to
# bail to a worse model just because the primary is producing a real long answer.
_MODEL_TIMEOUT = 45.0
_MODEL_TIMEOUT_LONG = 240.0


def _timeout_for(max_tokens: int) -> float:
    return _MODEL_TIMEOUT_LONG if max_tokens >= 2000 else _MODEL_TIMEOUT


def _safe_message_dump(message) -> str:
    """
    Dump every field on a litellm response message for diagnosing an empty
    content field -- e.g. some reasoning-capable models expose chain-of-thought
    in a separate field (reasoning_content, thinking, etc.) distinct from the
    final-answer content field. If that's happening, this shows it; if it's
    genuinely empty across the board, this rules that theory out instead of
    leaving it as an unconfirmed guess.
    """
    try:
        if hasattr(message, "model_dump"):
            return repr(message.model_dump())
        return repr(vars(message))
    except Exception as e:
        return f"<could not introspect message: {e}>"


class LLMProvider:
    """
    Cascade LLM provider — tries each configured model in order until one succeeds.
    """

    def __init__(self):
        self._max_tokens = settings.llm_max_tokens
        self._max_tokens_long = settings.llm_max_tokens_long

    # ── Public async API ──────────────────────────────────────────────────────

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: Optional[int] = None,
        temperature: float = 0.3,
        models: Optional[List[str]] = None,
    ) -> str:
        """
        Send a completion request. Returns the raw text response.
        Automatically cascades through all configured providers on failure.

        `models` overrides the default cascade (settings.llm_cascade_models)
        when a specific call site needs a different model preference order
        (e.g. drafting prefers a different primary than gap analysis).

        FIX: Previously synchronous — blocked the event loop on every call.
        Now runs the blocking cascade in a thread so FastAPI stays responsive.
        """
        tokens = max_tokens or self._max_tokens
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self._cascade(messages, tokens, temperature, models)
        )

    async def complete_stream(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: Optional[int] = None,
        temperature: float = 0.3,
        models: Optional[List[str]] = None,
    ):
        """
        Same cascade as complete(), but yields text chunks as they arrive instead
        of waiting for the full response. Falls back to the next model in the
        cascade only if a model fails before yielding any content — once a model
        has started streaming, later models are not attempted, since the client
        has already seen partial output from this one.

        `models` overrides the default cascade, same as in complete().
        """
        tokens = max_tokens or self._max_tokens
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        cascade = models or settings.llm_cascade_models
        last_error: Optional[Exception] = None
        failures: List[str] = []

        for model in cascade:
            started = False
            try:
                logger.info(f"Streaming LLM: {model} (max_tokens={tokens})")
                stream = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    max_tokens=tokens,
                    temperature=_temperature_for(model, temperature),
                    timeout=_timeout_for(tokens),
                    num_retries=0,
                    stream=True,
                    thinking={"type": "disabled"},
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        started = True
                        yield delta
                    finish_reason = chunk.choices[0].finish_reason
                    if finish_reason == "length":
                        logger.warning(f"{model} stream hit max_tokens={tokens} before finishing")
                        raise ValueError(
                            f"{model} hit the max_tokens={tokens} limit before finishing its answer. "
                            f"Raise max_tokens or shorten the request."
                        )
                return
            except Exception as e:
                if started:
                    # Already streamed partial content to the client — can't cleanly
                    # switch models mid-stream, so surface the failure instead of
                    # silently retrying with a different model's output spliced on.
                    raise
                short_err = str(e)[:120].replace("\n", " ")
                logger.warning(f"Stream model {model} failed before any output [{type(e).__name__}]: {short_err}")
                last_error = e
                failures.append(f"{model} ({_summarize_error(e)})")
                continue

        raise RuntimeError(
            f"All AI providers are currently unavailable. Tried: {'; '.join(failures)}."
        ) from last_error

    async def complete_chat(
        self,
        system_prompt: str,
        messages: List[dict],
        max_tokens: Optional[int] = None,
        temperature: float = 0.5,
    ) -> str:
        """
        Send a multi-turn chat completion. Cascades through all providers on failure.

        FIX: Previously synchronous — now async via thread executor.
        """
        tokens = max_tokens or self._max_tokens
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self._cascade(full_messages, tokens, temperature)
        )

    # ── Internal cascade (runs in thread) ────────────────────────────────────

    def _cascade(self, messages: List[dict], tokens: int, temperature: float, models: Optional[List[str]] = None) -> str:
        """Try each model in the cascade until one succeeds."""
        cascade = models or settings.llm_cascade_models
        last_error: Optional[Exception] = None
        failures: List[str] = []

        logger.info(f"Cascade: {len(cascade)} model(s) available: {', '.join(cascade)}")

        for i, model in enumerate(cascade):
            try:
                result = self._call_model(model, messages, tokens, temperature)
                if i > 0:
                    logger.info(f"Cascade succeeded on model #{i + 1}: {model}")
                return result
            except Exception as e:
                short_err = str(e)[:120].replace("\n", " ")
                logger.warning(f"Model {model} failed [{type(e).__name__}]: {short_err}")
                last_error = e
                failures.append(f"{model} ({_summarize_error(e)})")
                continue

        summary = "; ".join(failures)
        logger.error(f"All {len(cascade)} models in cascade failed: {summary}")
        raise RuntimeError(
            f"All AI providers are currently unavailable. Tried: {summary}."
        ) from last_error

    def _call_model(
        self,
        model: str,
        messages: List[dict],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """
        Call a specific model via LiteLLM (synchronous).

        FIX: Added timeout=45.0 and num_retries=0.
        Without timeout, a hanging model blocks the entire cascade.
        Without num_retries=0, LiteLLM silently retries internally which
        multiplies the hang time before we can move to the next model.
        """
        logger.info(f"Calling LLM: {model} (max_tokens={max_tokens})")

        response = litellm.completion(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=_temperature_for(model, temperature),
            timeout=_timeout_for(max_tokens),
            num_retries=0,
            thinking={"type": "disabled"},
        )

        content = response.choices[0].message.content
        if not content or not content.strip():
            logger.error(
                f"Empty response from {model}. finish_reason={response.choices[0].finish_reason!r}, "
                f"usage={getattr(response, 'usage', None)!r}, "
                f"message_fields={_safe_message_dump(response.choices[0].message)}"
            )
            raise ValueError(f"Empty response from {model}")

        finish_reason = response.choices[0].finish_reason
        if finish_reason == "length":
            raise ValueError(
                f"{model} hit the max_tokens={max_tokens} limit before finishing its answer "
                f"(response was {len(content)} chars). Raise max_tokens or shorten the request."
            )

        logger.info(f"LLM response: {len(content)} chars from {model}")
        return content

    async def _call_model_async(
        self,
        model: str,
        messages: List[dict],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """
        Call a specific model asynchronously via LiteLLM.

        FIX: Added timeout=45.0 and num_retries=0.
        """
        logger.info(f"Ensemble calling: {model} (max_tokens={max_tokens})")
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=_temperature_for(model, temperature),
            timeout=_timeout_for(max_tokens),
            num_retries=0,
            thinking={"type": "disabled"},
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            logger.error(
                f"Ensemble empty response from {model}. finish_reason={response.choices[0].finish_reason!r}, "
                f"usage={getattr(response, 'usage', None)!r}, "
                f"message_fields={_safe_message_dump(response.choices[0].message)}"
            )
            raise ValueError(f"Empty response from {model}")
        if response.choices[0].finish_reason == "length":
            raise ValueError(
                f"{model} hit the max_tokens={max_tokens} limit before finishing its answer "
                f"(response was {len(content)} chars). Raise max_tokens or shorten the request."
            )
        logger.info(f"Ensemble response: {len(content)} chars from {model}")
        return content

    async def complete_ensemble(
        self,
        system_prompt: str,
        user_message: str,
        models: List[str],
        max_tokens: Optional[int] = None,
        temperature: float = 0.3,
    ) -> List[tuple]:
        """
        Run multiple models simultaneously and return all successful (model, response) pairs.
        Failed models are silently skipped — caller must handle the case where fewer
        results than requested come back.
        """
        tokens = max_tokens or self._max_tokens
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        tasks = [
            self._call_model_async(model, messages, tokens, temperature)
            for model in models
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        successes = []
        for model, result in zip(models, raw_results):
            if isinstance(result, Exception):
                short = str(result)[:120].replace("\n", " ")
                logger.warning(f"Ensemble: {model} failed — {short}")
            else:
                successes.append((model, result))

        logger.info(f"Ensemble: {len(successes)}/{len(models)} models succeeded")
        return successes


# Singleton instance
_provider: Optional[LLMProvider] = None


def get_provider() -> LLMProvider:
    """Get the singleton LLM provider instance."""
    global _provider
    if _provider is None:
        _provider = LLMProvider()
    return _provider
