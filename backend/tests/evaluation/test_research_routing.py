"""Live research runs when it is justified, and not otherwise.

_should_use_live_research() was `return True`. Every generation step performed
a live web search on top of knowledge-base retrieval regardless of whether the
corpus already answered the question — which cost a search per step and, worse,
pulled undated web pages into the evidence pool for questions the codified CFR
text covered completely.

Both directions matter and are tested here. A router that never searches breaks
state-law questions, which the corpus does not cover at all; a router that
always searches is what we had.

Run: python -m pytest tests/evaluation/test_research_routing.py -v
"""

import pytest

from app.models.schemas import SourceStatus
from app.services.retrieval.live_research import (
    MIN_AUTHORITATIVE_SOURCES,
    decide_live_research,
)
from app.services.retrieval.models import SourceCategory, SourceType

from tests.evaluation.cases import make_context, make_result


def _covered_context(n=MIN_AUTHORITATIVE_SOURCES):
    """A knowledge base that fully covers the question: current, on-point, plural."""
    return make_context(*[
        make_result(f"99 CFR § 999.{10 + i}", f"Section {10 + i} text. A covered organization shall act.")
        for i in range(n)
    ])


class TestNoSearchWhenTheCorpusCoversIt:
    def test_sufficient_current_sources_means_no_search(self):
        decision = decide_live_research(_covered_context(), needs_freshness=False)
        assert decision.should_search is False
        assert "covers this request" in decision.reason

    def test_the_decision_carries_its_reason(self):
        """The reason is logged on every request; a bare boolean would make an
        unexpected search or an unexpected skip impossible to explain."""
        decision = decide_live_research(_covered_context(), needs_freshness=False)
        assert decision.reason

    def test_templates_and_examples_do_not_count_as_coverage(self):
        """Retrieval returns clause libraries and example policies too. They are
        useful for drafting and are not authority, so they must not make a
        thinly-covered question look covered."""
        ctx = make_context(*[
            make_result(
                f"Example Policy {i}",
                "Sample clause text.",
                category=SourceCategory.example_policy,
                part_citation=None,
            )
            for i in range(6)
        ])
        assert decide_live_research(ctx).should_search is True


class TestSearchWhenItIsJustified:
    def test_an_explicit_request_for_current_developments_searches(self):
        decision = decide_live_research(_covered_context(), needs_freshness=True)
        assert decision.should_search is True
        assert "explicitly requested" in decision.reason

    def test_a_thin_corpus_searches(self):
        decision = decide_live_research(_covered_context(n=1))
        assert decision.should_search is True
        assert "below the" in decision.reason

    def test_an_empty_corpus_searches(self):
        assert decide_live_research(make_context()).should_search is True

    def test_a_requested_jurisdiction_with_no_state_source_searches(self):
        """State law is not in the corpus at all, so this is the case live
        research exists for."""
        decision = decide_live_research(_covered_context(), jurisdiction="Tennessee")
        assert decision.should_search is True
        assert "Tennessee" in decision.reason

    def test_sources_of_unknown_standing_search(self):
        ctx = make_context(*[
            make_result(
                f"99 CFR § 999.{10 + i}",
                "text",
                status=None,
                source_type=SourceType.live_research,
            )
            for i in range(4)
        ])
        decision = decide_live_research(ctx)
        assert decision.should_search is True
        assert "current status" in decision.reason

    def test_a_proposed_only_corpus_searches(self):
        ctx = make_context(*[
            make_result(f"99 CFR § 999.{10 + i}", "text", status=SourceStatus.proposed)
            for i in range(4)
        ])
        assert decide_live_research(ctx).should_search is True

    def test_conflicting_versions_of_one_provision_search(self):
        """Two copies of the same section confirmed on different dates. Which one
        governs cannot be settled from inside the corpus."""
        ctx = make_context(
            make_result("99 CFR § 999.10", "Old text.", last_verified_date="2025-01-01"),
            make_result("99 CFR § 999.10", "New text.", last_verified_date="2026-08-01"),
            make_result("99 CFR § 999.11", "Other section."),
            make_result("99 CFR § 999.12", "Another section."),
        )
        decision = decide_live_research(ctx)
        assert decision.should_search is True
        assert "conflicting versions" in decision.reason


class TestTheRouterIsPlainCode:
    def test_the_decision_is_deterministic(self):
        """Same input, same answer, every time — the property a planner agent
        would not have given us."""
        ctx = _covered_context()
        answers = {decide_live_research(ctx, False, None).should_search for _ in range(20)}
        assert answers == {False}

    def test_no_model_call_is_involved(self, monkeypatch):
        """A routing decision that costs an LLM call is a routing decision that
        can fail, cost money, and answer differently on a retry."""
        import app.services.provider as provider_module

        def _explode(*a, **k):
            raise AssertionError("routing must not call a model")

        monkeypatch.setattr(provider_module, "get_provider", _explode)
        assert decide_live_research(_covered_context()).should_search is False
