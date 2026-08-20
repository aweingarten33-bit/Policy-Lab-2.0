"""
Regulatory Refresh Scheduler — Nightly background job to keep the KB current.

Schedule:
  - 2:00 AM UTC daily: Re-pull all eCFR targets and update ChromaDB collections.

The eCFR API returns in-force regulatory text as of today's date, so this
ensures the KB always reflects the current state of the Code of Federal Regulations.

Enforcement actions and Federal Register guidance are pulled live on every
analysis (not cached), so they don't need a scheduled refresh.
"""

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler = None


async def refresh_ecfr_knowledge_base():
    """
    Pull current eCFR content and update the ChromaDB knowledge base.
    Replaces existing eCFR-sourced content with today's version.
    """
    # Same memory reasoning as startup seeding: a refresh re-embeds the whole
    # corpus, so on a small instance it would kill a container that is
    # currently serving traffic -- turning a stale knowledge base into an
    # outage. Refreshing happens when the image is rebuilt.
    if not settings.kb_seed_at_runtime:
        logger.info(
            "Scheduled eCFR refresh skipped: runtime seeding is disabled to protect "
            "service memory. Rebuild the image to refresh the corpus."
        )
        return

    logger.info("Scheduled eCFR refresh starting...")
    started_at = datetime.utcnow()

    try:
        from app.services.retrieval.ecfr_client import get_ecfr_client, parts_to_source_chunks, ECFR_TARGETS
        from app.services.retrieval.store import get_store
        from app.services.retrieval.ingestion import ingest_source_document
        from app.services.retrieval.models import Jurisdiction, SourceType

        store = get_store()
        client = get_ecfr_client()

        total_chunks = 0
        total_sources = 0

        for title, part, label, category in ECFR_TARGETS:
            try:
                part_data = await client.fetch_part(title, part)
                if not part_data or not part_data.get("sections"):
                    logger.warning(f"Refresh: no data returned for {label}")
                    continue

                part_data["label"] = label
                part_data["category"] = category
                chunks = parts_to_source_chunks(part_data, category)

                if not chunks:
                    logger.warning(f"Refresh: no chunks extracted for {label}")
                    continue

                # Clear old eCFR content for this part from the collection
                # Use the collection matching the category
                collection_name = category.value
                try:
                    removed = store.delete_by_prefix(collection_name, f"ecfr_{title}_{part}_")
                    if removed:
                        logger.info(f"Refresh: cleared {removed} superseded chunk(s) for {label}")
                except Exception as e:
                    # Log it. Swallowing this silently meant stale, superseded
                    # regulatory text stayed retrievable alongside the fresh copy.
                    logger.error(f"Refresh: could not clear old chunks for {label}: {e}", exc_info=True)

                # Ingest fresh content
                fetched_date = part_data.get("fetched_date", datetime.utcnow().date().isoformat())
                # Same signature mismatch that silently broke initial seeding
                # (see seed_data.py). It broke the nightly refresh identically,
                # so even a knowledge base that somehow got populated could
                # never be updated.
                n = ingest_source_document(
                    source_name=f"{label} [eCFR {fetched_date}]",
                    text="\n\n".join(c.text for c in chunks),
                    category=category,
                    jurisdiction=Jurisdiction.federal,
                    citation=f"{title} CFR Part {part}",
                    url=f"https://www.ecfr.gov/current/title-{title}/part-{part}",
                    authority="eCFR — Electronic Code of Federal Regulations",
                    effective_date=fetched_date,
                    source_type=SourceType.retrieved_source,
                )
                total_chunks += n
                total_sources += 1
                logger.info(f"Refresh: {label} — {n} chunks ingested")

            except Exception as e:
                logger.error(f"Refresh failed for {label}: {e}")
                continue

        elapsed = (datetime.utcnow() - started_at).total_seconds()
        logger.info(
            f"eCFR refresh complete: {total_chunks} chunks across {total_sources} sources "
            f"in {elapsed:.1f}s"
        )

    except Exception as e:
        logger.error(f"eCFR refresh job failed: {e}", exc_info=True)


def start_scheduler():
    """Start the background scheduler. Call once at app startup."""
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        logger.info("Scheduler already running")
        return _scheduler

    _scheduler = AsyncIOScheduler(timezone="UTC")

    # Nightly eCFR refresh at 2:00 AM UTC
    _scheduler.add_job(
        refresh_ecfr_knowledge_base,
        trigger=CronTrigger(hour=2, minute=0, timezone="UTC"),
        id="ecfr_nightly_refresh",
        name="Nightly eCFR Regulatory Refresh",
        replace_existing=True,
        misfire_grace_time=3600,  # Allow up to 1 hour late
    )

    _scheduler.start()
    logger.info("Regulatory refresh scheduler started — eCFR refresh runs nightly at 02:00 UTC")
    return _scheduler


def stop_scheduler():
    """Stop the scheduler gracefully.

    Clears the module global as well: shutdown(wait=False) does not reliably
    flip `.running` straight away, so leaving the dead instance in place made
    start_scheduler() report "already running" and silently refuse to start a
    fresh one on restart.
    """
    global _scheduler
    if _scheduler is not None:
        try:
            if _scheduler.running:
                _scheduler.shutdown(wait=False)
                logger.info("Scheduler stopped")
        except Exception as e:
            logger.warning(f"Scheduler shutdown raised: {e}")
        finally:
            _scheduler = None
