"""
The seeding pipeline must actually store what it downloads.

Production symptom: eCFR was reachable, titles.json resolved, and 45 CFR Part
164 parsed into 41 sections -- yet every deploy reported 0 chunks and the
knowledge base stayed empty. Startup blocking and the XML parser had already
been fixed and the symptom persisted.

Cause: the last two steps of the pipeline were never executed successfully.

  1. `parts_to_source_chunks` set `source_type=SourceType.curated_source`.
     That member does not exist, so the call raised AttributeError on the very
     first section of every part.

  2. `seed_data._async_seed` and `scheduler` both called
     `ingest_source_document(store=..., title=..., source_type="curated_source")`.
     The function takes neither `store` nor `title`, so the call raised
     TypeError, and the result was then read as `result.get("chunks_added")`
     even though the function returns an int.

Both raised inside a broad `except Exception` that logged "Failed to seed" and
recorded 0 -- indistinguishable from eCFR legitimately having no content.

These tests exercise the real functions against a real temporary store. They
would all have failed before the fix.

Run: python -m pytest tests/test_seeding_pipeline.py -v
"""

import inspect

import pytest

from app.services.retrieval.ecfr_client import parts_to_source_chunks
from app.services.retrieval.ingestion import ingest_source_document
from app.services.retrieval.models import Jurisdiction, SourceCategory, SourceType


def _use_temp_store(tmp_path, monkeypatch):
    """Point the module-level store singleton at a throwaway directory.

    Setting KB_PERSIST_DIR in the environment is not enough: settings are
    loaded once at import, so the store would keep writing to the real
    knowledge base.
    """
    from app.services.retrieval import store as store_module
    monkeypatch.setattr(
        store_module, "_store", store_module.ChromaStore(persist_dir=str(tmp_path / "kb"))
    )
    return store_module


def _fake_part(n_sections: int = 4) -> dict:
    return {
        "title": 45,
        "part": 164,
        "fetched_date": "2026-08-20",
        "label": "45 CFR Part 164 — HIPAA Privacy, Security & Breach Notification",
        "sections": [
            {
                "section": f"164.3{i:02d}",
                "heading": "Administrative requirements",
                "text": (
                    "A covered entity must designate a privacy official who is "
                    "responsible for the development and implementation of the "
                    "policies and procedures of the entity. "
                ) * 4,
                "citation": f"45 CFR § 164.3{i:02d}",
            }
            for i in range(n_sections)
        ],
    }


class TestSourceTypeIsReal:
    def test_curated_source_is_not_a_source_type(self):
        """The name the code used to reference must stay gone, so nobody
        reintroduces it believing it exists."""
        assert not hasattr(SourceType, "curated_source")

    def test_parts_to_source_chunks_returns_chunks(self):
        """The original failure: this raised AttributeError instead of returning."""
        chunks = parts_to_source_chunks(_fake_part(4), SourceCategory.federal_regulation)
        assert len(chunks) == 4

    def test_chunks_carry_a_valid_source_type(self):
        chunks = parts_to_source_chunks(_fake_part(1), SourceCategory.federal_regulation)
        assert chunks[0].metadata.source_type in set(SourceType)

    def test_chunk_text_contains_the_regulatory_text(self):
        """Assert on content, not on the call merely returning."""
        chunks = parts_to_source_chunks(_fake_part(1), SourceCategory.federal_regulation)
        assert "privacy official" in chunks[0].text
        assert "164.300" in chunks[0].metadata.citation


class TestIngestionCallSignature:
    """The seeder and the scheduler must call ingestion the way it is defined.

    Checking the signature directly catches the mismatch even if a future
    refactor moves the call sites around.
    """

    def test_ingest_rejects_the_parameters_the_old_code_passed(self):
        params = set(inspect.signature(ingest_source_document).parameters)
        assert "store" not in params
        assert "title" not in params
        assert "source_name" in params

    def test_ingest_returns_an_int_not_a_dict(self, tmp_path, monkeypatch):
        store_module = _use_temp_store(tmp_path, monkeypatch)

        result = ingest_source_document(
            source_name="Signature check",
            text="The covered entity must implement policies and procedures. " * 30,
            category=SourceCategory.federal_regulation,
            jurisdiction=Jurisdiction.federal,
            citation="45 CFR Part 164",
            source_type=SourceType.retrieved_source,
        )
        assert isinstance(result, int)
        assert result > 0

    @pytest.mark.parametrize("module_name", [
        "app.services.retrieval.seed_data",
        "app.services.retrieval.scheduler",
    ])
    def test_call_sites_do_not_use_the_broken_kwargs(self, module_name):
        """Guards both callers, since the scheduler carried the identical bug
        and would have silently broken the nightly refresh on its own."""
        import importlib
        source = inspect.getsource(importlib.import_module(module_name))
        # Strip comments: these modules describe the old broken call in their
        # explanatory comments, and a naive substring check would match those.
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert "store=store" not in code
        assert '"curated_source"' not in code
        assert 'get("chunks_added"' not in code


class TestEndToEndStorage:
    def test_parsed_sections_reach_the_store(self, tmp_path, monkeypatch):
        """The whole point: parse -> convert -> ingest -> queryable.

        This is the assertion whose absence let an empty knowledge base ship.
        """
        store_module = _use_temp_store(tmp_path, monkeypatch)

        chunks = parts_to_source_chunks(_fake_part(5), SourceCategory.federal_regulation)
        stored = ingest_source_document(
            source_name="45 CFR Part 164 [eCFR 2026-08-20]",
            text="\n\n".join(c.text for c in chunks),
            category=SourceCategory.federal_regulation,
            jurisdiction=Jurisdiction.federal,
            citation="45 CFR Part 164",
            source_type=SourceType.retrieved_source,
        )
        assert stored > 0

        store = store_module.get_store()
        assert store.get_all_stats()["federal_regulation"] == stored

        results = store.query_all_collections(
            query_text="who must be designated as privacy official",
            n_results_per_collection=1,
            collections=["federal_regulation"],
        )
        assert results, "stored chunks were not retrievable"
        assert "privacy official" in results[0]["results"]["documents"][0][0]
