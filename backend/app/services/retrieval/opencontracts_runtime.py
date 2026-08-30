"""Calling OpenContracts' real code from a FastAPI process.

The integration is the simplest one that runs their actual maintained code:
import OpenContracts as a library. No Docker stack, no Postgres, no Celery, no
GraphQL — Policy Lab needs four things from OpenContracts and all four are
importable Python:

  * ``candidate_keys``                     canonical-key resolution order
  * ``CFRAuthoritySourceProvider``         eCFR fetch + XML section extraction
  * ``AuthoritySourceRecord``              the canonical authority record
  * ``safe_fetch_bytes``                   their SSRF-hardened HTTP client

Their authority modules import Django models at load time, so the app registry
has to be populated before any of them can be imported. That is what this
module does, once, lazily, on first use. Nothing here reimplements or wraps
OpenContracts behaviour; it only supplies the settings their imports read.

Deliberately lazy. Booting Django costs a second and pulls a large dependency
tree, and a deployment that never verifies a federal citation should not pay
for it at import time.

Deliberately fail-closed. If OpenContracts is not installed, or the boot fails,
``available()`` returns False and the callers below return nothing. An
authority that cannot be fetched is an authority that cannot support a claim —
the correct outcome is "unverified", never a guess.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state: Optional[bool] = None          # None = not attempted, True/False = outcome
_boot_error: str = ""

# OpenContracts' own LOCAL_APPS (config/settings/base.py). The authority modules
# import across most of them at load time, so a trimmed list fails at app-registry
# population with "model doesn't declare an explicit app_label".
_OC_APPS = [
    "opencontractserver.users",
    "opencontractserver.documents",
    "opencontractserver.corpuses",
    "opencontractserver.annotations",
    "opencontractserver.analyzer",
    "opencontractserver.extracts",
    "opencontractserver.feedback",
    "opencontractserver.conversations",
    "opencontractserver.badges",
    "opencontractserver.notifications",
    "opencontractserver.agents",
    "opencontractserver.worker_uploads",
    "opencontractserver.document_imports",
    "opencontractserver.discovery",
    "opencontractserver.benchmarks",
    "opencontractserver.research",
]


def _boot() -> bool:
    """Populate Django's app registry so OpenContracts modules can import."""
    global _boot_error

    src = (settings.opencontracts_src or "").strip()
    if src:
        if not os.path.isdir(os.path.join(src, "opencontractserver")):
            _boot_error = f"OPENCONTRACTS_SRC={src!r} is not an OpenContracts checkout"
            return False
        if src not in sys.path:
            sys.path.insert(0, src)

    try:
        import django
        from django.conf import settings as django_settings
    except ImportError as e:
        _boot_error = f"Django is not installed ({e}); OpenContracts cannot be imported"
        return False

    try:
        if not django_settings.configured:
            django_settings.configure(
                INSTALLED_APPS=[
                    "django.contrib.contenttypes",
                    "django.contrib.auth",
                    "guardian",
                    *_OC_APPS,
                ],
                # Never queried. The authority code Policy Lab calls is pure --
                # locate/fetch/parse and a dataclass projection -- but Django
                # will not populate an app registry without a database
                # configured, so it gets one that goes nowhere.
                DATABASES={
                    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
                },
                AUTH_USER_MODEL="users.User",
                USE_TZ=True,
                DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
                TELEMETRY_ENABLED=False,
                POSTHOG_API_KEY="",
                POSTHOG_HOST="",
            )
            django.setup()
        # Import the modules we actually use, now, so a missing transitive
        # dependency surfaces here as "unavailable" rather than as an exception
        # thrown in the middle of verifying a claim.
        from opencontractserver.enrichment import authorities  # noqa: F401
        from opencontractserver.enrichment import authority_sources  # noqa: F401
        from opencontractserver.pipeline.authority_source_providers import (  # noqa: F401
            cfr_provider,
        )
    except Exception as e:  # noqa: BLE001 - any import failure means unavailable
        _boot_error = f"{type(e).__name__}: {e}"
        return False

    return True


def available() -> bool:
    """Whether OpenContracts can be called in this process. Cached."""
    global _state
    if _state is None:
        with _lock:
            if _state is None:
                _state = _boot()
                if _state:
                    logger.info("OpenContracts runtime ready")
                else:
                    logger.error("OpenContracts runtime unavailable: %s", _boot_error)
    return _state


def unavailable_reason() -> str:
    """Why the runtime could not start, for logs and evidence reasons."""
    available()
    return _boot_error


def reset_for_tests() -> None:
    """Forget the cached outcome. Only used by tests that toggle availability."""
    global _state, _boot_error
    with _lock:
        _state = None
        _boot_error = ""


# ── the four things Policy Lab calls ──
#
# Thin accessors, not wrappers: each returns the OpenContracts object itself so
# no Policy Lab code sits between their implementation and its caller. They
# raise if the runtime is unavailable; every call site checks ``available()``
# first and fails closed rather than catching.

def candidate_keys(canonical_key: str) -> list[str]:
    """OpenContracts' canonical-key resolution order, including subsection rollup."""
    from opencontractserver.enrichment.authorities import candidate_keys as _ck

    return _ck(canonical_key)


def cfr_provider_module() -> Any:
    """Their eCFR provider module (also the patch point for its HTTP call)."""
    from opencontractserver.pipeline.authority_source_providers import cfr_provider

    return cfr_provider


def cfr_provider() -> Any:
    """A fresh ``CFRAuthoritySourceProvider``."""
    return cfr_provider_module().CFRAuthoritySourceProvider()


def authority_sources() -> Any:
    """Their ``authority_sources`` module: the record, statuses and enums."""
    from opencontractserver.enrichment import authority_sources as module

    return module


def safe_fetch_bytes(*args, **kwargs):
    """Their SSRF-hardened fetch. Used for the one eCFR call they do not make."""
    from opencontractserver.utils.safe_http import safe_fetch_bytes as _fetch

    return _fetch(*args, **kwargs)


def authority_user_agent() -> str:
    from opencontractserver.constants.safe_http import AUTHORITY_PROVIDER_USER_AGENT

    return AUTHORITY_PROVIDER_USER_AGENT
