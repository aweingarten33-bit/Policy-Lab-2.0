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
import logging
from typing import Optional, List

import litellm

from app.config import settings

logger = logging.getLogger(__name__)

litellm.suppress_debug_info = True

# How long (seconds) to wait for any single model before giving up and trying the next.
# Long-form policy generation (max_tokens >= 2000) uses _MODEL_TIMEOUT_LONG since
# 4000-token rewrites legitimately take 60-90s on gpt-4o-mini and we don't want to
# bail to a worse model just because the primary is producing a real long answer.
_MODEL_TIMEOUT = 45.0
_MODEL_TIMEOUT_LONG = 120.0


def _timeout_for(max_tokens: int) -> float:
    return _MODEL_TIMEOUT_LONG if max_tokens >= 2000 else _MODEL_TIMEOUT


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
    ) -> str:
        """
        Send a completion request. Returns the raw text response.
        Automatically cascades through all configured providers on failure.

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
            None, lambda: self._cascade(messages, tokens, temperature)
        )

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

    def _cascade(self, messages: List[dict], tokens: int, temperature: float) -> str:
        """Try each model in the cascade until one succeeds."""
        cascade = settings.llm_cascade_models
        last_error: Optional[Exception] = None

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
                continue

        logger.error(f"All {len(cascade)} models in cascade failed. Last error: {last_error}")
        raise RuntimeError(
            f"All AI providers are currently unavailable. "
            f"Tried: {', '.join(cascade)}. "
            f"Last error: {type(last_error).__name__}: {str(last_error)[:200]}"
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
            temperature=temperature,
            timeout=_timeout_for(max_tokens),
            num_retries=0,
        )

        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ValueError(f"Empty response from {model}")

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
            temperature=temperature,
            timeout=_timeout_for(max_tokens),
            num_retries=0,
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ValueError(f"Empty response from {model}")
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
