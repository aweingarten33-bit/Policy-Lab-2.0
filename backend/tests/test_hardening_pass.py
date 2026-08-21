"""Regression tests for API-boundary hardening.

These tests intentionally exercise behavior rather than merely asserting that a
function name appears somewhere in source code. The production bug that
prompted this pass existed precisely because isolated checks had tests while the
path users actually called did not enforce the intended invariant.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.models.schemas import PackageStatus, VerificationStatus
from app.services.package_integrity import reconcile_package_verification


def _package(*evidence_statuses, missing=0, status=PackageStatus.complete):
    rows = [
        SimpleNamespace(evidence=SimpleNamespace(status=evidence_status))
        for evidence_status in evidence_statuses
    ]
    rows.extend(SimpleNamespace(evidence=None) for _ in range(missing))
    return SimpleNamespace(
        status=status,
        gap_analysis=SimpleNamespace(gap_table=rows),
        unverified_claim_count=999,
        verification_overall="stale summary",
        error_message=None,
    )


class TestPackageVerificationReconciliation:
    def test_interim_stream_snapshot_is_not_rewritten(self):
        package = _package(
            VerificationStatus.unverified,
            status=PackageStatus.analyzing,
        )
        reconcile_package_verification(package)
        assert package.unverified_claim_count == 999
        assert package.verification_overall == "stale summary"

    def test_final_count_comes_from_finding_evidence_not_stale_attribution_count(self):
        package = _package(
            VerificationStatus.verified,
            VerificationStatus.unverified,
            VerificationStatus.contradicted,
        )
        reconcile_package_verification(package)
        assert package.unverified_claim_count == 2
        assert "2 finding(s)" in package.verification_overall

    def test_missing_evidence_is_never_reported_as_success(self):
        package = _package(VerificationStatus.verified, missing=2)
        reconcile_package_verification(package)
        assert package.unverified_claim_count == 2
        assert "Verification incomplete" in package.verification_overall
        assert "did not receive an evidence record" in package.verification_overall

    def test_fully_verified_package_gets_consistent_summary(self):
        package = _package(
            VerificationStatus.verified,
            VerificationStatus.verified,
        )
        reconcile_package_verification(package)
        assert package.unverified_claim_count == 0
        assert "All 2 finding(s) completed" in package.verification_overall


class _CaptureStore:
    def __init__(self):
        self.package = None
        self.completed = False
        self.error = None

    async def update_package(self, job_id, package):
        self.package = package

    async def mark_complete(self, job_id):
        self.completed = True

    async def mark_error(self, job_id, error):
        self.error = error


class _OnePackageOrchestrator:
    def __init__(self, package):
        self.package = package

    async def generate_full_package_stream(self, **kwargs):
        yield self.package


@pytest.mark.asyncio
async def test_background_job_path_reconciles_the_final_package(monkeypatch):
    """The API path used by the frontend must actually run the integrity guard."""
    import app.routers.action_package as module

    package = _package(
        VerificationStatus.verified,
        VerificationStatus.unverified,
    )
    store = _CaptureStore()
    orchestrator = _OnePackageOrchestrator(package)

    monkeypatch.setattr(module, "get_job_store", lambda: store)
    monkeypatch.setattr(module, "get_orchestrator", lambda: orchestrator)

    request = SimpleNamespace(
        text="x" * 100,
        file_name="policy.txt",
        industry="healthcare",
        jurisdiction="NY",
        outputs=["gap_analysis"],
        enable_live_research=False,
    )

    await module._run_action_package_job("job-1", request)

    assert store.completed is True
    assert store.error is None
    assert store.package is package
    assert store.package.unverified_claim_count == 1
    assert "1 finding(s)" in store.package.verification_overall


class _FakeTask:
    def __init__(self):
        self.cancel_called = False

    def done(self):
        return False

    def cancel(self):
        self.cancel_called = True


class _UnauthorizedStore:
    async def get(self, job_id, owner=None):
        return None


def _request(path: str) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"x-client-id", b"attacker-session")],
    })


@pytest.mark.asyncio
async def test_action_package_cancel_authorizes_before_touching_running_task(monkeypatch):
    """Knowing an analysis job id must not cancel another client's task."""
    import app.routers.action_package as module

    job_id = "victim-job"
    task = _FakeTask()
    module._running_tasks[job_id] = task
    monkeypatch.setattr(module, "get_job_store", lambda: _UnauthorizedStore())

    try:
        with pytest.raises(HTTPException) as exc:
            await module.cancel_action_package_job(
                job_id, _request(f"/api/action-package/cancel/{job_id}")
            )
        assert exc.value.status_code == 404
        assert task.cancel_called is False
    finally:
        module._running_tasks.pop(job_id, None)


@pytest.mark.asyncio
async def test_draft_cancel_authorizes_before_touching_running_task(monkeypatch):
    """Draft jobs have the same ownership invariant as analysis jobs."""
    import app.main as module
    import app.services.draft_job_store as store_module

    job_id = "victim-draft"
    task = _FakeTask()
    module._draft_running_tasks[job_id] = task
    monkeypatch.setattr(store_module, "get_draft_job_store", lambda: _UnauthorizedStore())

    try:
        with pytest.raises(HTTPException) as exc:
            await module.cancel_draft_job(
                job_id, _request(f"/api/draft-policy/cancel/{job_id}")
            )
        assert exc.value.status_code == 404
        assert task.cancel_called is False
    finally:
        module._draft_running_tasks.pop(job_id, None)


def test_production_image_requires_request_level_grounding():
    repo_root = Path(__file__).resolve().parents[2]
    dockerfile = (repo_root / "Dockerfile").read_text(encoding="utf-8")
    assert "ENV REQUIRE_GROUNDING=true" in dockerfile


def test_diagnostics_are_not_anonymous_api_surface():
    import app.main as main

    assert "/api/kb/diagnose" not in main._PUBLIC_PATHS
