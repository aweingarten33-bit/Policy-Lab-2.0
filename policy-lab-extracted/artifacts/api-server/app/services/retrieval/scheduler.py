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

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler = None


async def refresh_ecfr_knowledge_base():
    """
    Pull current eCFR content and update the ChromaDB knowledge base.
    Replaces existing eCFR-sourced content with today's version.
    """
    logger.info("Scheduled eCFR refresh starting...")
    started_at = datetime.utcnow()

    try:
        from app.services.retrieval.ecfr_client import get_ecfr_client, parts_to_source_chunks, ECFR_TARGETS
        from app.services.retrieval.store import get_store
        from app.services.retrieval.ingestion import ingest_source_document

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
                    store.delete_by_prefix(collection_name, f"ecfr_{title}_{part}_")
                except Exception:
                    pass  # delete_by_prefix may not exist yet — handled below

                # Ingest fresh content
                fetched_date = part_data.get("fetched_date", datetime.utcnow().date().isoformat())
                result = ingest_source_document(
                    store=store,
                    title=f"{label} [eCFR {fetched_date}]",
                    text="\n\n".join(c.text for c in chunks),
                    category=category,
                    citation=f"{title} CFR Part {part}",
                    url=f"https://www.ecfr.gov/current/title-{title}/part-{part}",
                    authority="eCFR — Electronic Code of Federal Regulations",
                    effective_date=fetched_date,
                    jurisdiction="federal",
                    source_type="curated_source",
                )

                n = result.get("chunks_added", 0)
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
    """Stop the scheduler gracefully."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
