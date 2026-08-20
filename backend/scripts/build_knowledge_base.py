#!/usr/bin/env python3
"""
Build the knowledge base at IMAGE BUILD TIME, not at container start.

Why this exists
---------------
Seeding used to happen when a container booted: download every CFR part from
eCFR, embed it, write it to local disk. That is the wrong time to do it.

  * The platform filesystem is ephemeral, so the work was thrown away and
    redone on every single deploy.
  * A cold container had to finish minutes of downloading and embedding before
    it could serve traffic, which is long enough for the health check to fail
    and restart it -- so the corpus never finished building at all.
  * Every restart re-hammered a government API for content that changes at most
    daily.

Production retrieval systems build the corpus once, ahead of time, and ship it.
App servers stay stateless and boot instantly against an already-populated
store. This script is that build step: run during `docker build`, its output is
baked into the image, and every container starts with a complete knowledge base
and zero network dependency.

Runtime seeding still exists as a fallback and for the nightly refresh, so a
build-time failure degrades rather than breaks.

Usage:
    python scripts/build_knowledge_base.py [--require-success]

    --require-success  exit non-zero if no chunks were loaded (fails the build)
"""

import argparse
import asyncio
import os
import sys

# Ensure the app package is importable when run as a script from backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _build() -> tuple:
    """Build the corpus. Returns (total_chunks, regulation_chunks).

    The two numbers are reported separately on purpose. The OIG/HCCA guidance
    is bundled with the repo and loads from disk with no network at all, so a
    total chunk count is never zero even when eCFR was completely unreachable
    and not one regulation was downloaded. A build gate that only checked the
    total would therefore always pass -- protecting against nothing.

    The regulation count is what a strict build must actually require.
    """
    from app.services.retrieval.seed_data import _async_seed
    from app.services.retrieval.store import get_store

    store = get_store()
    stats = store.get_all_stats()
    existing = sum(v for v in stats.values() if v > 0)
    if existing:
        print(f"[build-kb] Knowledge base already has {existing} chunks — nothing to do.")
        return existing, max(stats.get("federal_regulation", 0), 0)

    print("[build-kb] Downloading regulations from eCFR and embedding them...")
    results = await _async_seed()

    total = 0
    for label, count in sorted(results.items()):
        marker = "ok  " if count else "MISS"
        print(f"[build-kb]   {marker} {label}: {count} chunks")
        total += count

    regulation_chunks = max(get_store().get_all_stats().get("federal_regulation", 0), 0)
    print(
        f"[build-kb] Total: {total} chunks across {len(results)} sources "
        f"({regulation_chunks} of them regulatory text from eCFR)."
    )
    return total, regulation_chunks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-success", action="store_true",
                        help="Fail the build unless regulatory text was downloaded from eCFR.")
    args = parser.parse_args()

    try:
        total, regulation_chunks = asyncio.run(_build())
    except Exception as e:
        print(f"[build-kb] ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        total, regulation_chunks = 0, 0

    # Strict mode requires the regulations, not merely "some content". The
    # bundled guidance loads from disk regardless, so checking the total would
    # pass a build in which eCFR was unreachable and nothing was downloaded.
    if args.require_success and regulation_chunks == 0:
        print(
            "[build-kb] FAILED: no regulatory text was downloaded from eCFR. "
            f"({total} chunks of bundled guidance loaded, but a strict build "
            "requires the regulations.) Refusing to ship an image whose "
            "citations cannot be verified against current CFR text.",
            file=sys.stderr,
        )
        return 1

    if total > 0:
        print(
            f"[build-kb] SUCCESS — {total} chunks baked into the image "
            f"({regulation_chunks} regulatory)."
        )
        if regulation_chunks == 0:
            print(
                "[build-kb] WARNING: guidance loaded but eCFR returned no regulatory "
                "text. This image ships with guidance-only grounding. Set "
                "KB_SEED_REQUIRED=true to make this a build failure.",
                file=sys.stderr,
            )
        return 0

    message = (
        "[build-kb] WARNING: no content was loaded at all. The image will ship "
        "without a prebuilt knowledge base and will fall back to seeding at runtime."
    )
    if args.require_success:
        print(message.replace("WARNING", "FAILED"), file=sys.stderr)
        return 1

    # Default: warn but let the build through. A transient eCFR outage should
    # not block shipping a fix.
    print(message, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
