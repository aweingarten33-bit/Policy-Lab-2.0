"""
The knowledge base must report how old it is.

The corpus is built into the image, so it is exactly as current as the last
rebuild. Nothing surfaced that: a chunk count alone cannot tell a corpus
downloaded this morning from one downloaded two years ago, and both report a
healthy-looking "5,714 chunks stored". Left alone, a deployment drifts out of
date with every check still green.

Run: python -m pytest tests/test_corpus_age.py -v
"""

from datetime import date, timedelta

import pytest

from app.services.retrieval.ingestion import ingest_source_document
from app.services.retrieval.models import Jurisdiction, SourceCategory, SourceType


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.services.retrieval import store as store_module
    fresh = store_module.ChromaStore(persist_dir=str(tmp_path / "kb"))
    monkeypatch.setattr(store_module, "_store", fresh)
    return fresh


def _add(category, effective_date, name):
    ingest_source_document(
        source_name=name,
        text="A covered entity must designate a privacy official. " * 40,
        category=category,
        jurisdiction=Jurisdiction.federal,
        effective_date=effective_date,
        source_type=SourceType.retrieved_source,
    )


class TestCorpusDate:
    def test_empty_store_has_no_date(self, store):
        assert store.get_corpus_date() is None

    def test_reports_the_ecfr_fetch_date(self, store):
        _add(SourceCategory.federal_regulation, "2026-08-20", "45 CFR 164")
        assert store.get_corpus_date() == "2026-08-20"

    def test_guidance_publication_dates_are_ignored(self, store):
        """Guidance carries the date the document was published -- the OIG
        guidance is stamped 2023 and always will be. Counting it reported a
        freshly built corpus as nearly three years old."""
        _add(SourceCategory.federal_guidance, "2023-11-06", "OIG GCPG")
        assert store.get_corpus_date() is None

        _add(SourceCategory.federal_regulation, "2026-08-20", "45 CFR 164")
        assert store.get_corpus_date() == "2026-08-20", (
            "a 2023 guidance document must not drag the reported corpus date backwards"
        )

    def test_takes_the_most_recent_regulation_date(self, store):
        _add(SourceCategory.federal_regulation, "2026-01-05", "42 CFR 482")
        _add(SourceCategory.federal_regulation, "2026-08-20", "45 CFR 164")
        assert store.get_corpus_date() == "2026-08-20"


class TestHealthReporting:
    def _health(self, monkeypatch):
        from fastapi.testclient import TestClient
        import app.main as main
        monkeypatch.setattr(main.settings, "kb_seed_at_runtime", False)
        with TestClient(main.app) as client:
            return client.get("/api/health").json()

    def test_health_exposes_date_and_age(self, store, monkeypatch):
        recent = (date.today() - timedelta(days=3)).isoformat()
        _add(SourceCategory.federal_regulation, recent, "45 CFR 164")

        body = self._health(monkeypatch)
        assert body["kb_corpus_date"] == recent
        assert body["kb_corpus_age_days"] == 3

    def test_health_survives_an_empty_corpus(self, store, monkeypatch):
        """Age reporting must never be able to break the health check."""
        body = self._health(monkeypatch)
        assert body["status"] == "ok"
        assert body["kb_corpus_date"] is None


class TestDiagnoseReporting:
    def _corpus_step(self, monkeypatch):
        from fastapi.testclient import TestClient
        import app.main as main
        monkeypatch.setattr(main.settings, "kb_seed_at_runtime", False)
        with TestClient(main.app) as client:
            body = client.get("/api/kb/diagnose").json()
        return next((s for s in body["steps"] if "Corpus age" in s["step"]), None)

    def test_a_fresh_corpus_passes(self, store, monkeypatch):
        _add(SourceCategory.federal_regulation, date.today().isoformat(), "45 CFR 164")
        step = self._corpus_step(monkeypatch)
        assert step is not None and step["ok"] is True

    def test_a_stale_corpus_is_flagged_with_the_fix(self, store, monkeypatch):
        from app.routers.knowledge_base import CORPUS_STALE_AFTER_DAYS

        old = (date.today() - timedelta(days=CORPUS_STALE_AFTER_DAYS + 200)).isoformat()
        _add(SourceCategory.federal_regulation, old, "45 CFR 164")

        step = self._corpus_step(monkeypatch)
        assert step is not None
        assert step["ok"] is False
        # The warning has to say what to do about it, not just that it is old.
        assert "Clear build cache" in step["detail"]
        assert "months old" in step["detail"]

    def test_the_stale_warning_says_live_research_still_covers_it(self, store, monkeypatch):
        """Staleness is 'worth refreshing', not 'broken' -- live .gov research
        keeps recent developments flowing regardless."""
        from app.routers.knowledge_base import CORPUS_STALE_AFTER_DAYS

        old = (date.today() - timedelta(days=CORPUS_STALE_AFTER_DAYS + 30)).isoformat()
        _add(SourceCategory.federal_regulation, old, "45 CFR 164")

        step = self._corpus_step(monkeypatch)
        assert "Live .gov research" in step["detail"]


class TestCorpusDateAfterTheDatesWereSeparated:
    """The date read must be the one eCFR ingestion now writes.

    eCFR chunks no longer set effective_date: eCFR serves the text in force as
    of a date, which is not the date the provision took effect, and writing the
    fetch date there asserted something the source never said. The date the
    corpus's age is measured by is last_verified_date — when the text was last
    confirmed against the publisher.
    """

    def test_the_last_verified_date_is_what_is_reported(self, store):
        ingest_source_document(
            source_name="45 CFR 164",
            text="A covered entity must designate a privacy official. " * 40,
            category=SourceCategory.federal_regulation,
            jurisdiction=Jurisdiction.federal,
            effective_date=None,
            retrieved_date="2026-08-25",
            last_verified_date="2026-08-25",
            source_type=SourceType.retrieved_source,
        )
        assert store.get_corpus_date() == "2026-08-25"

    def test_an_older_corpus_still_reports_its_age(self, store):
        """Chunks embedded before the split carry only effective_date. Falling
        back to it keeps a pre-existing baked corpus reporting an age instead of
        silently going dateless — which would read as 'no corpus' on a health
        check rather than 'an old one'."""
        ingest_source_document(
            source_name="45 CFR 164 (legacy)",
            text="A covered entity must designate a privacy official. " * 40,
            category=SourceCategory.federal_regulation,
            jurisdiction=Jurisdiction.federal,
            effective_date="2025-01-15",
            source_type=SourceType.retrieved_source,
        )
        assert store.get_corpus_date() == "2025-01-15"
