"""The four-axis bands partition the scores exactly once each.

The prompt used to say: partial when 1-2 axes pass, gap when 0-1 pass, missing
when the topic is absent, compliant when all four pass. One passing axis
satisfies both the partial and the gap definition, and zero satisfies both gap
and missing — so for two of the five possible scores the label was whichever
the model happened to pick that run.

That is not a cosmetic inconsistency. Status drives risk_level, drives
remediation_priority, and drives the compliance score, so the same policy could
be reported as moderate/90-day or high/30-day on two runs with identical
findings and no way for the reader to tell which was meant.

Run: python -m pytest tests/evaluation/test_axis_classification.py -v
"""

import pytest

from app.models.schemas import AXIS_COUNT, GapStatus, classify_from_axes
from app.services.llm_service import ANALYTICAL_PROTOCOL, _coerce_axes, _parse_llm_response


class TestTheBandingIsATotalFunction:
    @pytest.mark.parametrize("axes,expected", [
        (4, GapStatus.compliant),
        (3, GapStatus.partial),
        (2, GapStatus.partial),
        (1, GapStatus.gap),
        (0, GapStatus.missing),
    ])
    def test_each_score_maps_to_one_status(self, axes, expected):
        assert classify_from_axes(axes) is expected

    def test_every_possible_score_is_covered(self):
        """Total: no score falls through to a default."""
        assert {classify_from_axes(n) for n in range(AXIS_COUNT + 1)} == {
            GapStatus.compliant, GapStatus.partial, GapStatus.gap, GapStatus.missing
        }

    def test_no_score_produces_two_answers(self):
        """The property the old definition violated. Calling twice must agree,
        and — more to the point — there is exactly one branch per score."""
        for n in range(AXIS_COUNT + 1):
            assert classify_from_axes(n) is classify_from_axes(n)

    def test_the_boundaries_are_where_they_are_documented(self):
        """1 and 2 are the pair the overlapping definition confused."""
        assert classify_from_axes(1) is GapStatus.gap
        assert classify_from_axes(2) is GapStatus.partial
        assert classify_from_axes(1) is not classify_from_axes(2)

    def test_an_absent_topic_is_missing(self):
        assert classify_from_axes(0, topic_absent=True) is GapStatus.missing
        assert classify_from_axes(3, topic_absent=True) is GapStatus.missing

    @pytest.mark.parametrize("bad", [-1, 5, 99])
    def test_an_impossible_score_is_rejected_not_guessed(self, bad):
        with pytest.raises(ValueError):
            classify_from_axes(bad)


class TestThePromptStatesOneRule:
    def test_the_overlapping_definition_is_gone(self):
        collapsed = " ".join(ANALYTICAL_PROTOCOL.split())
        assert '"partial" when 1–2 axes pass' not in collapsed
        assert '"gap" when 0–1 pass' not in collapsed

    def test_each_band_is_stated_exactly_once(self):
        """Written as a table, so the bands cannot be read as overlapping ranges."""
        collapsed = " ".join(ANALYTICAL_PROTOCOL.split())
        for band in (
            "4 axes pass → compliant",
            "3 axes pass → partial",
            "2 axes pass → partial",
            "1 axis passes → gap",
            "0 axes pass → missing",
        ):
            assert collapsed.count(band) == 1, band


class TestStatusIsRecomputedInCode:
    """The prompt states the rule; the parser enforces it.

    A prompt instruction is a request. Deriving status from axes_passed after
    parsing makes the banding identical on every request regardless of what the
    model wrote in the status field.
    """

    def _one_row(self, axes, status):
        return _parse_llm_response(
            '{"policy_type": "T", "audit_ready_summary": "s", "gap_table": [{'
            f'"clause": "c", "regulations": ["99 CFR Part 999"], "axes_passed": {axes}, '
            f'"status": "{status}", "finding": "f", "suggested_language": "s", '
            '"citation": "99 CFR Part 999"}]}'
        ).gap_table[0]

    @pytest.mark.parametrize("axes,expected", [
        (4, GapStatus.compliant), (3, GapStatus.partial),
        (2, GapStatus.partial), (1, GapStatus.gap), (0, GapStatus.missing),
    ])
    def test_axes_decide_the_status(self, axes, expected):
        assert self._one_row(axes, "gap").status is expected

    def test_a_model_disagreeing_with_its_own_axes_is_overruled(self):
        """One passing axis is a gap even when the model labelled it compliant —
        the exact contradiction the overlapping definition used to permit."""
        row = self._one_row(1, "compliant")
        assert row.status is GapStatus.gap
        assert row.axes_passed == 1

    def test_the_score_follows_the_recomputed_status(self):
        """Status feeds the compliance score, so a corrected band has to correct
        the number too rather than leaving the two disagreeing."""
        result = _parse_llm_response(
            '{"policy_type": "T", "audit_ready_summary": "s", "gap_table": ['
            '{"clause": "a", "regulations": ["r"], "axes_passed": 4, "status": "gap",'
            ' "finding": "f", "suggested_language": "s", "citation": "c"},'
            '{"clause": "b", "regulations": ["r"], "axes_passed": 0, "status": "compliant",'
            ' "finding": "f", "suggested_language": "s", "citation": "c"}]}'
        )
        # One genuinely compliant (1.0) and one genuinely missing (0.0) of two.
        assert result.compliance_score == 50.0

    def test_a_row_without_axes_keeps_the_model_status(self):
        """Backwards compatible: axes_passed is new, and an older or truncated
        response that omits it must still parse rather than being forced to a
        band nothing computed."""
        row = _parse_llm_response(
            '{"policy_type": "T", "audit_ready_summary": "s", "gap_table": [{'
            '"clause": "c", "regulations": ["r"], "status": "partial", "finding": "f",'
            ' "suggested_language": "s", "citation": "c"}]}'
        ).gap_table[0]
        assert row.status is GapStatus.partial
        assert row.axes_passed is None


class TestAxesCoercion:
    @pytest.mark.parametrize("raw,expected", [
        (3, 3), ("3", 3), ("3 of 4", 3), (0, 0), (4, 4), (3.0, 3),
    ])
    def test_usable_values_are_read(self, raw, expected):
        assert _coerce_axes(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "many", 5, -1, True, False, "high", {}])
    def test_unusable_values_are_discarded_not_guessed(self, raw):
        """A wrong guess here silently rewrites a finding's severity, so an
        unreadable value must fall back to the model's own label."""
        assert _coerce_axes(raw) is None
