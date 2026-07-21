"""
In-memory ephemeral job store for policy drafting, mirroring job_store.py's
pattern for action packages. Lets a client start a draft, leave the screen
(tab switch, phone lock, navigation), and reconnect later to either the
finished result or the live-in-progress text -- the generation itself keeps
running server-side regardless of what the browser does.

Jobs live only in process memory and self-expire after JOB_TTL. Lost on
server restart, same tradeoff as the action-package job store.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Literal
from uuid import uuid4

logger = logging.getLogger(__name__)

JOB_TTL = timedelta(minutes=30)
JobStatus = Literal["running", "complete", "error"]


@dataclass
class DraftJobState:
    job_id: str
    status: JobStatus = "running"
    partial_text: str = ""
    policy: Optional[dict] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    version: int = 0


class DraftJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, DraftJobState] = {}
        self._lock = asyncio.Lock()

    async def create(self) -> str:
        await self._purge_expired()
        job_id = str(uuid4())
        async with self._lock:
            self._jobs[job_id] = DraftJobState(job_id=job_id)
        logger.info(f"Created draft job {job_id}")
        return job_id

    async def get(self, job_id: str) -> Optional[DraftJobState]:
        await self._purge_expired()
        async with self._lock:
            return self._jobs.get(job_id)

    async def append_text(self, job_id: str, chunk: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.partial_text += chunk
            job.updated_at = datetime.utcnow()
            job.version += 1

    async def mark_complete(self, job_id: str, policy: dict) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "complete"
            job.policy = policy
            job.updated_at = datetime.utcnow()
            job.version += 1
        logger.info(f"Draft job {job_id} complete")

    async def mark_error(self, job_id: str, error: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "error"
            job.error = error
            job.updated_at = datetime.utcnow()
            job.version += 1
        logger.warning(f"Draft job {job_id} errored: {error}")

    async def _purge_expired(self) -> None:
        cutoff = datetime.utcnow() - JOB_TTL
        async with self._lock:
            expired = [jid for jid, j in self._jobs.items() if j.updated_at < cutoff]
            for jid in expired:
                del self._jobs[jid]
        if expired:
            logger.info(f"Purged {len(expired)} expired draft jobs")


_store: Optional[DraftJobStore] = None


def get_draft_job_store() -> DraftJobStore:
    global _store
    if _store is None:
        _store = DraftJobStore()
    return _store
