"""
The frontend must load in a browser.

Production symptom: the site went completely blank. Not an error page, not a
degraded page -- nothing.

Cause: making authentication fail closed put every path behind the x-api-key
header, and only "/" was allowlisted. So the HTML shell returned 200 while
every asset it referenced returned 401.

A browser cannot attach a header to the requests it makes for <script src>,
stylesheets, or the favicon. There is no configuration or password entry that
could have fixed this from the client side -- the app's own JavaScript was
unreachable, so the PasswordGate never even ran.

The rule: the password guards the API. Static frontend files are public, and
the gate inside that bundle is what protects the API behind it.

Run: python -m pytest tests/test_frontend_reachable.py -v
"""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """Configure the running app in place.

    Deliberately does not reload app.config/app.main: reloading rebinds those
    modules for every test that runs afterwards, which made an unrelated test
    in another file fail depending on collection order.
    """
    import app.main as main

    monkeypatch.setattr(main.settings, "api_key", "secret123")
    monkeypatch.setattr(main.settings, "environment", "production")
    monkeypatch.setattr(main.settings, "kb_auto_seed", False)
    return TestClient(main.app)


class TestBrowserCanLoadTheApp:
    """Every request a browser makes on its own, without any header."""

    @pytest.mark.parametrize("path", ["/", "/legal", "/some/client/route"])
    def test_spa_routes_are_served(self, client, path):
        assert client.get(path).status_code != 401

    @pytest.mark.parametrize("path", [
        "/assets/index-abc123.js",
        "/assets/index-abc123.css",
        "/favicon.ico",
        "/robots.txt",
    ])
    def test_static_assets_are_never_401(self, client, path):
        """404 is fine (the file may not exist in a test checkout).
        401 is the bug: it means the browser is forbidden from loading the app."""
        assert client.get(path).status_code != 401, (
            f"{path} requires a header a browser cannot send — the page will render blank"
        )


class TestApiIsStillProtected:
    """The fix must not reopen what fail-closed auth was for."""

    @pytest.mark.parametrize("path", ["/api/chat", "/api/draft-policy", "/api/action-package"])
    def test_api_rejects_missing_key(self, client, path):
        assert client.post(path, json={}).status_code == 401

    def test_api_rejects_wrong_key(self, client):
        r = client.post("/api/chat", headers={"x-api-key": "wrong"}, json={})
        assert r.status_code == 401

    def test_admin_routes_need_the_admin_key(self, client):
        r = client.post("/api/kb/seed", headers={"x-api-key": "secret123"})
        assert r.status_code == 403

    def test_health_stays_public(self, client):
        assert client.get("/api/health").status_code == 200

    def test_a_non_api_path_cannot_reach_an_api_handler(self, client):
        """Guards the shape of the rule itself: only /api/ is exempt from
        being treated as frontend content."""
        import app.main as main
        from starlette.requests import Request

        def _req(path):
            return Request({"type": "http", "path": path, "method": "GET", "headers": []})

        assert main._is_api_request(_req("/api/chat")) is True
        assert main._is_api_request(_req("/assets/x.js")) is False
        assert main._is_api_request(_req("/")) is False
