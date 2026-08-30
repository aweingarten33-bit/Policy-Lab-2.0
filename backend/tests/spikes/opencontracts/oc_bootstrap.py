"""Bring real OpenContracts modules up in-process, with no server running.

The intended way to run OpenContracts is its supported Docker configuration
(``local.yml``). That is not available in this sandbox: the Docker registry
returns 403 for every image blob, so no base image can be pulled and the stack
cannot be built. See the spike test module for what that does and does not
prove.

The next-best fidelity is to import OpenContracts' actual modules and give them
the settings they read at import time. Nothing here reimplements any
OpenContracts behaviour -- ``candidate_keys``, ``AuthoritySourceRecord`` and
the CFR provider all run as they ship. What is missing is the server around
them: Postgres, Celery, GraphQL, guardian-enforced permissions.

Set ``OPENCONTRACTS_SRC`` to a checkout to enable the spike; without it the
spike tests skip, which is what happens in CI.
"""

from __future__ import annotations

import os
import sys

_INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "guardian",
    # OpenContracts' own LOCAL_APPS, copied from config/settings/base.py.
    # The authority modules import across most of them at load time, so a
    # trimmed list fails with "model doesn't declare an explicit app_label".
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


def source_root() -> str | None:
    root = os.environ.get("OPENCONTRACTS_SRC")
    if root and os.path.isdir(os.path.join(root, "opencontractserver")):
        return root
    return None


def available() -> bool:
    """Whether the spike can run here."""
    if source_root() is None:
        return False
    try:
        import django  # noqa: F401
        import guardian  # noqa: F401
    except ImportError:
        return False
    return True


def boot() -> None:
    """Configure Django and populate the app registry. Idempotent."""
    root = source_root()
    if root is None:
        raise RuntimeError("OPENCONTRACTS_SRC is not set to an OpenContracts checkout")
    if root not in sys.path:
        sys.path.insert(0, root)

    import django
    from django.conf import settings

    if settings.configured:
        return

    settings.configure(
        INSTALLED_APPS=_INSTALLED_APPS,
        # Never queried. The authority code read in this spike is pure; the
        # database exists only because Django refuses to populate an app
        # registry without one configured.
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        AUTH_USER_MODEL="users.User",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        TELEMETRY_ENABLED=False,
        POSTHOG_API_KEY="",
        POSTHOG_HOST="",
    )
    django.setup()
