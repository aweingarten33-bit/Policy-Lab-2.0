"""Regulations come before guidance, always. Guidance gets a small allowance.

Reported against real output: the OIG compliance-program guidance kept turning
up as a reference and a citation on policies it has nothing to do with.

Measured on a breach-notification policy, retrieval was ordering purely by
similarity and returning:

    0.661  federal_guidance     OIG General Compliance Program Guidance
    0.657  federal_guidance     OIG General Compliance Program Guidance
    0.636  federal_guidance     OIG General Compliance Program Guidance
    0.441  federal_regulation   45 CFR § 164.9001
    0.435  federal_regulation   45 CFR § 164.9003
    0.407  federal_regulation   45 CFR § 164.9002

Guidance took every top slot on a question it does not govern. It is written in
the same register as a policy, so it reads as similar to almost any policy;
a regulation is written in the register of a regulation. Similarity cannot tell
the document that governs from the document that merely sounds like the
question — and the model reads its context top-down.

Two rules now. Tier before score, so law is never displaced by guidance that
scores higher. And a fixed allowance on non-law sources, because three guidance
chunks sitting below three regulations is still a block of general
compliance-program prose in front of a model analysing something else.

Run: python -m pytest tests/evaluation/test_law_outranks_guidance.py -v
"""

import pytest

from app.config import settings
from app.services.retrieval.models import SourceCategory
from app.services.retrieval.retriever import (
    _TIER_AGENCY,
    _TIER_LAW,
    _TIER_REFERENCE,
    _authority_tier,
)

from tests.evaluation.cases import make_result


@pytest.fixture
def retriever(tmp_path, monkeypatch):
    from app.services.retrieval import store as store_module
    from app.services.retrieval.retriever import ComplianceRetriever

    monkeypatch.setattr(
        store_module, "_store", store_module.ChromaStore(persist_dir=str(tmp_path / "kb"))
    )
    return ComplianceRetriever()


def _guidance(score, n=0):
    return make_result(
        f"OIG General Compliance Program Guidance ({n})",
        "An effective compliance program has seven elements.",
        category=SourceCategory.federal_guidance,
        part_citation=None,
        score=score,
    )


def _regulation(citation, score):
    return make_result(
        citation,
        "A covered entity shall notify each affected individual.",
        score=score,
    )


class TestTheReportedCase:
    """The exact scores measured on the breach-notification policy."""

    def _observed(self):
        return [
            _guidance(0.661, 1), _guidance(0.657, 2), _guidance(0.636, 3),
            _regulation("45 CFR § 164.9001", 0.441),
            _regulation("45 CFR § 164.9003", 0.435),
            _regulation("45 CFR § 164.9002", 0.407),
        ]

    def test_score_alone_puts_guidance_first(self):
        """The old behaviour, kept as the baseline this exists to change."""
        ordered = sorted(self._observed(), key=lambda r: r.score, reverse=True)
        assert ordered[0].chunk.metadata.category is SourceCategory.federal_guidance

    def test_tier_first_puts_the_regulation_first(self, retriever):
        ordered = sorted(
            self._observed(),
            key=lambda r: (_authority_tier(r.chunk.metadata.category), -r.score),
        )
        assert ordered[0].chunk.metadata.category is SourceCategory.federal_regulation
        assert ordered[0].chunk.metadata.citation == "45 CFR § 164.9001"

    def test_every_regulation_precedes_every_guidance_document(self, retriever):
        ordered = sorted(
            self._observed(),
            key=lambda r: (_authority_tier(r.chunk.metadata.category), -r.score),
        )
        tiers = [_authority_tier(r.chunk.metadata.category) for r in ordered]
        assert tiers == sorted(tiers), "a guidance document came before a regulation"

    def test_the_guidance_block_is_cut_to_the_allowance(self, retriever, monkeypatch):
        monkeypatch.setattr(settings, "kb_max_guidance_chunks", 2)
        ordered = sorted(
            self._observed(),
            key=lambda r: (_authority_tier(r.chunk.metadata.category), -r.score),
        )
        kept = retriever._cap_supporting_material(ordered)

        guidance = [r for r in kept if _authority_tier(r.chunk.metadata.category) != _TIER_LAW]
        law = [r for r in kept if _authority_tier(r.chunk.metadata.category) == _TIER_LAW]
        assert len(guidance) == 2, "the allowance was not applied"
        assert len(law) == 3, "the allowance must never drop a regulation"


class TestScoreStillOrdersWithinATier:
    def test_the_most_relevant_regulation_comes_first(self):
        results = [
            _regulation("45 CFR § 164.9003", 0.30),
            _regulation("45 CFR § 164.9001", 0.90),
            _regulation("45 CFR § 164.9002", 0.60),
        ]
        ordered = sorted(
            results, key=lambda r: (_authority_tier(r.chunk.metadata.category), -r.score)
        )
        assert [r.chunk.metadata.citation for r in ordered] == [
            "45 CFR § 164.9001", "45 CFR § 164.9002", "45 CFR § 164.9003",
        ]

    def test_the_most_relevant_guidance_survives_the_cut(self, retriever, monkeypatch):
        """The allowance keeps the best guidance, not an arbitrary one."""
        monkeypatch.setattr(settings, "kb_max_guidance_chunks", 1)
        ordered = sorted(
            [_guidance(0.20, 1), _guidance(0.80, 2), _guidance(0.50, 3)],
            key=lambda r: (_authority_tier(r.chunk.metadata.category), -r.score),
        )
        kept = retriever._cap_supporting_material(ordered)
        assert len(kept) == 1
        assert kept[0].score == 0.80


class TestGuidanceIsBuriedNotRemoved:
    """It stays usable where it genuinely governs."""

    def test_guidance_still_reaches_retrieval(self, retriever, monkeypatch):
        monkeypatch.setattr(settings, "kb_max_guidance_chunks", 2)
        kept = retriever._cap_supporting_material([_regulation("45 CFR § 1", 0.9), _guidance(0.5)])
        assert any(
            r.chunk.metadata.category is SourceCategory.federal_guidance for r in kept
        ), "guidance was removed entirely; it is meant to be ranked below law, not deleted"

    def test_guidance_alone_is_still_returned(self, retriever, monkeypatch):
        """On a compliance-program policy the corpus may offer little else."""
        monkeypatch.setattr(settings, "kb_max_guidance_chunks", 2)
        kept = retriever._cap_supporting_material([_guidance(0.7, 1), _guidance(0.6, 2)])
        assert len(kept) == 2

    def test_an_allowance_of_zero_would_remove_it(self, retriever, monkeypatch):
        """Documents the escape hatch: the allowance is the single knob, and 0
        is 'law only' without touching the corpus."""
        monkeypatch.setattr(settings, "kb_max_guidance_chunks", 0)
        kept = retriever._cap_supporting_material([_regulation("45 CFR § 1", 0.9), _guidance(0.99)])
        assert len(kept) == 1
        assert kept[0].chunk.metadata.category is SourceCategory.federal_regulation


class TestTheTierMap:
    @pytest.mark.parametrize("category", [
        SourceCategory.federal_regulation,
        SourceCategory.state_law,
    ])
    def test_codified_law_is_the_top_tier(self, category):
        assert _authority_tier(category) == _TIER_LAW

    @pytest.mark.parametrize("category", [
        SourceCategory.federal_guidance,
        SourceCategory.ocr_guidance,
        SourceCategory.enforcement_action,
    ])
    def test_agency_material_sits_below_law(self, category):
        assert _authority_tier(category) == _TIER_AGENCY
        assert _TIER_AGENCY > _TIER_LAW

    @pytest.mark.parametrize("category", [
        SourceCategory.policy_template,
        SourceCategory.example_policy,
        SourceCategory.policy_clause_library,
    ])
    def test_drafting_material_sits_last(self, category):
        assert _authority_tier(category) == _TIER_REFERENCE

    def test_the_tiers_agree_with_what_verification_will_accept(self):
        """Retrieval order and verification's binding-law rule must not
        disagree: showing the model something first that it is then forbidden
        to treat as binding is how guidance got cited as law."""
        from app.services.retrieval.verification import _LEGALLY_BINDING_CATEGORIES

        for category in SourceCategory:
            if category in _LEGALLY_BINDING_CATEGORIES:
                assert _authority_tier(category) == _TIER_LAW, category
            else:
                assert _authority_tier(category) > _TIER_LAW, category


class TestThePromptSaysSo:
    """Ordering alone is not enough; the model is told the rule as well."""

    def _context(self, retriever):
        return retriever._format_context_for_prompt(
            [_regulation("45 CFR § 164.9001", 0.44), _guidance(0.66)]
        )

    def test_the_ordering_rule_is_stated(self, retriever):
        text = self._context(retriever)
        assert "order of authority, not relevance" in text

    def test_guidance_is_not_offered_as_the_authority_for_a_requirement(self, retriever):
        assert "never as the authority for a legal requirement" in self._context(retriever)

    def test_the_model_is_told_retrieval_is_a_wide_net(self, retriever):
        """The complaint was citation of irrelevant sources, and 'it was in my
        context' is why models do that."""
        assert "Being retrieved is not a reason to cite something" in self._context(retriever)

    def test_the_regulation_appears_before_the_guidance_in_the_prompt(self, retriever):
        text = self._context(retriever)
        assert text.index("164.9001") < text.index("Compliance Program Guidance")
