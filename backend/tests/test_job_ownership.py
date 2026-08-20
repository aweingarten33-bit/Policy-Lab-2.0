"""
A job id alone must not grant access to a job.

Assessment finding #14: background job ids had no ownership binding. Anyone
holding an id could read the job -- and a job holds somebody's uploaded policy
text and the analysis of it. UUIDv4 ids are impractical to guess, so the
practical risk was low, but "unguessable" is not "authorized".

Closing it did not need the user system the app lacks. The client sends a
random per-session id; a job is readable only by the session that created it.
This is not authentication -- everyone shares one app password -- it answers
the narrower question the job endpoints actually need to ask.

Run: python -m pytest tests/test_job_ownership.py -v
"""

import asyncio

import pytest

from app.services.draft_job_store import DraftJobStore
from app.services.job_store import JobStore
from app.request_identity import ANONYMOUS, client_id


STORES = [JobStore, DraftJobStore]


@pytest.mark.parametrize("Store", STORES, ids=lambda s: s.__name__)
class TestOwnership:
    @pytest.mark.asyncio
    async def test_the_owner_can_read_its_job(self, Store):
        store = Store()
        job_id = await store.create(owner="alice")
        assert await store.get(job_id, owner="alice") is not None

    @pytest.mark.asyncio
    async def test_another_client_cannot(self, Store):
        """The finding itself: holding the id is not enough."""
        store = Store()
        job_id = await store.create(owner="alice")
        assert await store.get(job_id, owner="bob") is None

    @pytest.mark.asyncio
    async def test_internal_callers_bypass_the_check(self, Store):
        """The background task that fills the job in has no client identity."""
        store = Store()
        job_id = await store.create(owner="alice")
        assert await store.get(job_id) is not None

    @pytest.mark.asyncio
    async def test_anonymous_clients_are_isolated_from_named_ones(self, Store):
        store = Store()
        job_id = await store.create(owner=ANONYMOUS)
        assert await store.get(job_id, owner=ANONYMOUS) is not None
        assert await store.get(job_id, owner="alice") is None

    @pytest.mark.asyncio
    async def test_a_missing_job_is_still_none(self, Store):
        store = Store()
        assert await store.get("no-such-job", owner="alice") is None


class TestClientIdHeader:
    def _request(self, headers):
        from starlette.requests import Request
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})

    def test_reads_the_header(self):
        assert client_id(self._request({"x-client-id": "session-123"})) == "session-123"

    def test_missing_header_is_anonymous(self):
        """An older cached frontend must keep working across a deploy."""
        assert client_id(self._request({})) == ANONYMOUS

    def test_blank_header_is_anonymous(self):
        assert client_id(self._request({"x-client-id": "   "})) == ANONYMOUS

    def test_an_overlong_header_is_bounded(self):
        """It is untrusted input that becomes a dict key and a log line."""
        assert len(client_id(self._request({"x-client-id": "x" * 5000}))) <= 128


class TestEndpointsRequireIt:
    """The stores enforcing ownership is useless if the routes don't pass it."""

    @pytest.mark.parametrize("module_name,func_names", [
        ("app.main", ["get_draft_job_status", "stream_draft_job", "cancel_draft_job"]),
        ("app.routers.action_package", ["get_action_package_job_status",
                                        "stream_action_package_job",
                                        "cancel_action_package_job"]),
    ])
    def test_read_routes_pass_an_owner(self, module_name, func_names):
        import importlib, inspect
        module = importlib.import_module(module_name)
        for name in func_names:
            source = inspect.getsource(getattr(module, name))
            assert "client_id(http_request)" in source, (
                f"{name} reads a job without checking who is asking"
            )

    @pytest.mark.parametrize("module_name,func_name", [
        ("app.main", "start_draft_job"),
        ("app.routers.action_package", "start_action_package_job"),
    ])
    def test_start_routes_bind_an_owner(self, module_name, func_name):
        import importlib, inspect
        source = inspect.getsource(getattr(importlib.import_module(module_name), func_name))
        assert "owner=client_id(http_request)" in source
