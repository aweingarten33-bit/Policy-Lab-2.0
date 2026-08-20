"""
Hardening items from an external code audit.

Each of these was a real finding, verified in the code before being fixed.
They are kept as tests because several are the kind of thing a later change
quietly undoes: a header string edited, a default flipped, a callback
signature changed.

Run: python -m pytest tests/test_audit_hardening.py -v
"""

import inspect
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


class TestContentSecurityPolicy:
    def _csp(self):
        import app.main as main
        return inspect.getsource(main.security_headers_middleware)

    def test_scripts_may_not_run_inline(self):
        """The built page loads one module from our own origin and has no
        inline script, so 'unsafe-inline' bought nothing while removing the
        main protection CSP offers."""
        csp = self._csp()
        assert '"script-src \'self\'; "' in csp

    def test_styles_may_still_be_inline(self):
        """Radix and the animation layer insert rules at runtime. Blocking
        that breaks the interface rather than hardening it -- the audit
        recommended removing it for styles too, which would have been wrong."""
        assert "style-src 'self' 'unsafe-inline'" in self._csp()

    def test_pdf_worker_is_still_allowed(self):
        """PDF extraction runs in a blob worker. This is how PDF upload broke
        the first time."""
        csp = self._csp()
        assert "worker-src 'self' blob:" in csp


class TestForwardedForIsNotBlindlyTrusted:
    """X-Forwarded-For is a plain request header, so anyone can send one.
    Trusting it unconditionally let a caller vary it per request and never hit
    a rate limit -- defeating the control that caps spend on a paid API."""

    def test_the_setting_exists_and_defaults_on(self):
        from app.config import Settings
        assert Settings().trust_proxy is True  # this deploys behind a proxy

    def test_the_header_is_ignored_when_not_trusted(self, monkeypatch):
        import app.main as main
        from starlette.requests import Request

        monkeypatch.setattr(main.settings, "trust_proxy", False)
        request = Request({
            "type": "http", "method": "GET", "path": "/",
            "headers": [(b"x-forwarded-for", b"1.2.3.4")],
            "client": ("10.0.0.1", 1234),
        })
        assert main._client_ip(request) == "10.0.0.1"

    def test_the_header_is_used_when_trusted(self, monkeypatch):
        import app.main as main
        from starlette.requests import Request

        monkeypatch.setattr(main.settings, "trust_proxy", True)
        request = Request({
            "type": "http", "method": "GET", "path": "/",
            "headers": [(b"x-forwarded-for", b"1.2.3.4, 10.0.0.9")],
            "client": ("10.0.0.1", 1234),
        })
        assert main._client_ip(request) == "1.2.3.4"


class TestStatusCallbacksToleratePlainFunctions:
    """Registering an ordinary function raised TypeError on the first status
    change, and the error was swallowed as a warning -- so progress updates
    just stopped, with no indication why."""

    @pytest.mark.asyncio
    async def test_a_sync_callback_is_called(self):
        from app.services.orchestrator import PackageOrchestrator

        orch = PackageOrchestrator()
        seen = []
        orch.on_status_update(lambda pid, status, name: seen.append(status))
        await orch._notify_status("pkg-1", "analyzing", "gap_analysis")
        assert seen == ["analyzing"]

    @pytest.mark.asyncio
    async def test_an_async_callback_still_works(self):
        from app.services.orchestrator import PackageOrchestrator

        orch = PackageOrchestrator()
        seen = []

        async def cb(pid, status, name):
            seen.append(status)

        orch.on_status_update(cb)
        await orch._notify_status("pkg-1", "verifying", "")
        assert seen == ["verifying"]


class TestRepositoryHygiene:
    def test_ci_runs_the_test_suite(self):
        """200+ tests existed and nothing ran them."""
        workflow = REPO / ".github" / "workflows" / "ci.yml"
        assert workflow.exists(), "no CI workflow"
        text = workflow.read_text()
        assert "pytest" in text
        assert "npm run build" in text

    def test_there_is_a_licence(self):
        assert (REPO / "LICENSE").exists()

    def test_only_one_dockerfile(self):
        """Two Dockerfiles, one of them dead, left it ambiguous which builds
        production."""
        assert (REPO / "Dockerfile").exists()
        assert not (REPO / "backend" / "Dockerfile").exists()

    def test_the_env_example_is_not_a_production_footgun(self):
        # Settings only -- the comments explain what production changes, and a
        # naive substring check matches the explanation as well as the value.
        settings = [
            line.strip()
            for line in (REPO / "backend" / ".env.example").read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert "CORS_ORIGINS=*" not in settings, "wildcard CORS set as the default"
        assert "ENVIRONMENT=production" not in settings, (
            "a file copied for local work should not default to production"
        )

    def test_a_release_build_can_require_a_populated_corpus(self):
        """`|| true` alone meant a failed corpus build still shipped an image."""
        dockerfile = (REPO / "Dockerfile").read_text()
        assert "KB_SEED_REQUIRED" in dockerfile
        assert "--require-success" in dockerfile
