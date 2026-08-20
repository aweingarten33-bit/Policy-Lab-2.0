"""
Observable state for knowledge-base seeding.

Seeding moved off the startup path (it was blocking the app from accepting
traffic while it downloaded regulations, so the platform health check could
time out and restart the container before it ever finished). Running it in the
background fixes that but makes it invisible, so its progress and outcome are
recorded here and surfaced through /api/kb/diagnose.
"""

import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_lock = threading.Lock()

_state: Dict[str, Any] = {
    "status": "not_started",   # not_started | running | succeeded | failed | partial
    "started_at": None,
    "finished_at": None,
    "chunks_added": 0,
    "per_source": {},
    "error": None,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mark_started() -> None:
    with _lock:
        _state.update(
            status="running", started_at=_now(), finished_at=None,
            chunks_added=0, per_source={}, error=None,
        )


def mark_finished(per_source: Dict[str, int]) -> None:
    total = sum(per_source.values())
    with _lock:
        _state.update(
            status="succeeded" if total > 0 else "failed",
            finished_at=_now(),
            chunks_added=total,
            per_source=per_source,
            error=None if total > 0 else
            "Seeding ran to completion but produced zero chunks.",
        )


def mark_failed(error: str) -> None:
    with _lock:
        _state.update(status="failed", finished_at=_now(), error=error)


def get_state() -> Dict[str, Any]:
    with _lock:
        return dict(_state)


def describe() -> str:
    """One human-readable sentence about the current seeding state."""
    s = get_state()
    status = s["status"]
    if status == "not_started":
        return "Seeding has not run yet in this process."
    if status == "running":
        return f"Seeding is still running (started {s['started_at']}). Check back shortly."
    if status == "succeeded":
        return f"Seeding finished successfully: {s['chunks_added']} chunks loaded."
    return f"Seeding failed: {s.get('error') or 'unknown error'}"


def reset() -> None:
    """Return to the pre-seeding state. Intended for tests."""
    with _lock:
        _state.update(
            status="not_started", started_at=None, finished_at=None,
            chunks_added=0, per_source={}, error=None,
        )
