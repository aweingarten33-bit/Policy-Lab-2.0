"""
In-memory ephemeral job store for long-running action package generations.

Jobs live only in process memory and self-expire after JOB_TTL. This preserves
the "no policy text persisted" guarantee while letting clients reconnect to
in-flight jobs after navigation, tab-switch, or transient disconnects.

NOTE: Jobs are lost on server restart. With uvicorn --reload in dev this means
mid-flight jobs die when code changes; in production (no --reload) jobs only
disappear on a true restart.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Literal
from uuid import uuid4

from app.models.schemas import ComplianceActionPackage

logger = logging.getLogger(__name__)

JOB_TTL = timedelta(minutes=30)
JobStatus = Literal["running", "complete", "error"]


@dataclass
class JobState:
    job_id: str
    status: JobStatus = "running"
    package: Optional[ComplianceActionPackage] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    version: int = 0


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}
        self._lock = asyncio.Lock()

    async def create(self) -> str:
        await self._purge_expired()
        job_id = str(uuid4())
        async with self._lock:
            self._jobs[job_id] = JobState(job_id=job_id)
        logger.info(f"Created job {job_id}")
        return job_id

    async def get(self, job_id: str) -> Optional[JobState]:
        await self._purge_expired()
        async with self._lock:
            return self._jobs.get(job_id)

    async def update_package(self, job_id: str, package: ComplianceActionPackage) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.package = package
            job.updated_at = datetime.utcnow()
            job.version += 1

    async def mark_complete(self, job_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "complete"
            job.updated_at = datetime.utcnow()
            job.version += 1
        logger.info(f"Job {job_id} complete")

    async def mark_error(self, job_id: str, error: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "error"
            job.error = error
            job.updated_at = datetime.utcnow()
            job.version += 1
        logger.warning(f"Job {job_id} errored: {error}")

    async def _purge_expired(self) -> None:
        cutoff = datetime.utcnow() - JOB_TTL
        async with self._lock:
            expired = [jid for jid, j in self._jobs.items() if j.updated_at < cutoff]
            for jid in expired:
                del self._jobs[jid]
        if expired:
            logger.info(f"Purged {len(expired)} expired jobs")


_store: Optional[JobStore] = None


def get_job_store() -> JobStore:
    global _store
    if _store is None:
        _store = JobStore()
    return _store
