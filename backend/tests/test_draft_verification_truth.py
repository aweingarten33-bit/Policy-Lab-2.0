"""Draft verification must never call partial evidence fully verified."""

from types import SimpleNamespace

import app.services.draft_policy_service as draft_service


class _Ctx:
    total_sources_found = 1
    live_research_used = False

    def get_source_names(self):
        return ["Official regulation"]

    def get_source_url_map(self):
        return {"Official regulation": "https://www.ecfr.gov/"}

    def get_source_snippets(self):
        return []


class _Verifier:
    def __init__(self, report):
        self.report = report

    def verify_section(self, **kwargs):
        return self.report


def _report(*, verified=0, partial=0, unverified=0, contradicted=0):
    return SimpleNamespace(
        total_claims=verified + partial + unverified + contradicted,
        verified_claims=verified,
        partially_verified_claims=partial,
        unverified_claims=unverified,
        contradicted_claims=contradicted,
    )


def test_partial_citation_is_not_reported_as_all_verified(monkeypatch):
    monkeypatch.setattr(
        draft_service,
        "get_verification_service",
        lambda: _Verifier(_report(partial=1)),
    )
    data = draft_service.attach_attribution({"full_text": "x"}, _Ctx())

    assert data["unverified_claim_count"] == 1
    assert "not fully verified" in data["verification_overall"].lower()
    assert "all 1" not in data["verification_overall"].lower()


def test_contradicted_draft_claim_is_counted_as_needing_review(monkeypatch):
    monkeypatch.setattr(
        draft_service,
        "get_verification_service",
        lambda: _Verifier(_report(contradicted=1)),
    )
    data = draft_service.attach_attribution({"full_text": "x"}, _Ctx())

    assert data["unverified_claim_count"] == 1
    assert "contradicted" in data["verification_overall"].lower()


def test_only_fully_verified_claims_get_all_verified_summary(monkeypatch):
    monkeypatch.setattr(
        draft_service,
        "get_verification_service",
        lambda: _Verifier(_report(verified=2)),
    )
    data = draft_service.attach_attribution({"full_text": "x"}, _Ctx())

    assert data["unverified_claim_count"] == 0
    assert "all 2" in data["verification_overall"].lower()
    assert "fully verified" in data["verification_overall"].lower()
