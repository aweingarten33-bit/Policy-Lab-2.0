"""
Client-safe error handling.

Returning str(e) straight to the caller leaked the LLM vendor, the exact model
in use, the full configured fallback cascade, and raw Python exception text —
none of it secret, but all of it useful to someone fingerprinting the stack
before crafting a targeted attack, and none of it meaningful to a real user.

Every handler routes through here instead: the full exception is logged
server-side for debugging, the caller gets a generic message. The one thing
still passed through is upstream rate limiting, which is genuinely actionable
("wait and retry") rather than an internal detail.
"""

import logging
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def _is_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "rate_limit" in text


def http_error(
    exc: Exception,
    *,
    context: str,
    user_message: str = "Something went wrong processing your request. Please try again.",
    status_code: int = 500,
) -> HTTPException:
    """Log the real exception, return a sanitized HTTPException for the client.

    Args:
        exc: The caught exception. Logged in full, never sent to the client.
        context: Short server-side label for the failing operation.
        user_message: What the caller actually sees.
        status_code: HTTP status for the sanitized response.
    """
    if _is_rate_limit(exc):
        logger.warning("%s: upstream rate limit — %s", context, exc)
        return HTTPException(
            status_code=429,
            detail="The service is busy right now. Please wait a moment and try again.",
        )

    logger.exception("%s failed: %s", context, exc)
    return HTTPException(status_code=status_code, detail=user_message)
