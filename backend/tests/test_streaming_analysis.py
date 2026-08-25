"""Findings appear as they are written, and never before they are finished.

The gap analysis is one JSON object from one model call. Waiting for it to close
meant the reader watched a spinner for the whole generation while completed
findings sat in a buffer -- the first one is typically done seconds in.

The risk in fixing that is showing something half-written: a finding cut off
mid-sentence, or a citation missing its subsection, is worse than a spinner,
because a compliance officer may act on what they can see. So the rule is that
a row is emitted only once its closing brace has arrived and it parses on its
own.

Run: python -m pytest tests/test_streaming_analysis.py -v
"""

import json

import pytest

from app.services.streaming_json import complete_rows, scalar_field

ROW_A = {
    "clause": "Breach notification content",
    "regulations": ["45 CFR § 164.404"],
    "axes_passed": 1,
    "status": "gap",
    "finding": "The policy does not state what a notification must contain.",
    "suggested_language": "Each notification shall include a description of the event.",
    "citation": "45 CFR § 164.404(c)",
}
ROW_B = {
    "clause": "Workforce training",
    "regulations": ["45 CFR § 164.530"],
    "axes_passed": 4,
    "status": "compliant",
    "finding": "All four axes pass.",
    "suggested_language": "No change required.",
    "citation": "45 CFR § 164.530(b)",
}


def _document(rows, closed=True):
    doc = {
        "policy_type": "HIPAA Breach Notification Policy",
        "gap_table": rows,
        "audit_ready_summary": "Summary.",
    }
    text = json.dumps(doc, indent=1)
    return text if closed else text[: text.rindex("]")]


class TestRowsAppearAsTheyComplete:
    def test_nothing_is_returned_before_the_first_row_closes(self):
        partial = _document([ROW_A])
        cut = partial[: partial.index('"citation"')]
        assert complete_rows(cut) == []

    def test_a_finished_row_is_returned_while_the_next_is_still_writing(self):
        full = _document([ROW_A, ROW_B])
        # Cut midway through the second row.
        cut = full[: full.index('"Workforce training"') + 10]
        rows = complete_rows(cut)
        assert len(rows) == 1
        assert rows[0]["clause"] == ROW_A["clause"]

    def test_all_rows_are_returned_once_the_array_closes(self):
        rows = complete_rows(_document([ROW_A, ROW_B]))
        assert [r["clause"] for r in rows] == [ROW_A["clause"], ROW_B["clause"]]

    def test_rows_arrive_in_order_as_the_buffer_grows(self):
        full = _document([ROW_A, ROW_B])
        seen = []
        for i in range(1, len(full) + 1):
            rows = complete_rows(full[:i])
            if len(rows) > len(seen):
                seen = rows
        assert [r["clause"] for r in seen] == [ROW_A["clause"], ROW_B["clause"]]

    def test_a_row_count_never_decreases_as_more_text_arrives(self):
        """The consumer de-duplicates by count, so the prefix has to be stable."""
        full = _document([ROW_A, ROW_B])
        highest = 0
        for i in range(1, len(full) + 1):
            n = len(complete_rows(full[:i]))
            assert n >= highest, f"row count went backwards at character {i}"
            highest = n


class TestItNeverShowsHalfWrittenContent:
    def test_a_truncated_finding_is_not_emitted(self):
        text = _document([ROW_A])
        cut = text[: text.index("The policy does not state") + 12]
        assert complete_rows(cut) == []

    def test_braces_inside_policy_text_do_not_close_a_row_early(self):
        """A finding quoting policy text containing a brace must not be cut
        short at that brace -- it would emit a truncated, wrong finding."""
        row = dict(ROW_A, finding='Section 4.2 uses the placeholder "{ROLE}" instead of a named officer.')
        rows = complete_rows(_document([row]))
        assert len(rows) == 1
        assert rows[0]["finding"] == row["finding"]

    def test_escaped_quotes_inside_a_finding_are_handled(self):
        row = dict(ROW_A, finding='The policy says \\"timely\\" without defining it.')
        rows = complete_rows(_document([row]))
        assert len(rows) == 1

    def test_a_malformed_row_is_skipped_not_guessed_at(self):
        broken = '{"policy_type": "x", "gap_table": [{"clause": "a", ]'
        assert complete_rows(broken) == []

    def test_an_unopened_array_returns_nothing(self):
        assert complete_rows('{"policy_type": "Still writing') == []
        assert complete_rows("") == []


class TestEarlyScalars:
    def test_the_policy_type_is_available_before_the_rows(self):
        text = _document([ROW_A])
        cut = text[: text.index("gap_table")]
        assert scalar_field(cut, "policy_type") == "HIPAA Breach Notification Policy"

    def test_a_half_written_title_is_not_shown(self):
        assert scalar_field('{"policy_type": "HIPAA Brea', "policy_type") is None

    def test_a_missing_field_is_none(self):
        assert scalar_field(_document([ROW_A]), "nonexistent") is None


class TestMarkdownFences:
    def test_a_leading_json_fence_is_ignored(self):
        text = "```json\n" + _document([ROW_A])
        assert len(complete_rows(text)) == 1


class TestTheStreamProducesTheSameResultAsTheBlockingCall:
    """Streaming must be a delivery change, not an analysis change."""

    @pytest.mark.asyncio
    async def test_the_final_result_matches_a_single_parse(self, monkeypatch):
        from app.services import llm_service

        document = _document([ROW_A, ROW_B])

        class _Stub:
            async def complete_stream(self, **kwargs):
                for i in range(0, len(document), 40):
                    yield document[i:i + 40]

        monkeypatch.setattr(llm_service, "get_provider", lambda: _Stub())

        partials, final = [], None
        async for result, done in llm_service.analyze_policy_stream("policy text"):
            if done:
                final = result
            else:
                partials.append(result)

        expected = llm_service._parse_llm_response(document)
        assert final is not None
        assert [r.clause for r in final.gap_table] == [r.clause for r in expected.gap_table]
        assert final.compliance_score == expected.compliance_score
        assert partials, "findings should have been emitted before the document closed"
        assert len(partials[-1].gap_table) <= len(final.gap_table)

    @pytest.mark.asyncio
    async def test_a_provider_that_cannot_stream_falls_back(self, monkeypatch):
        """A provider failing to stream must cost the streaming benefit, not
        the analysis."""
        from app.models.schemas import AnalysisResult
        from app.services import llm_service

        class _Broken:
            async def complete_stream(self, **kwargs):
                raise RuntimeError("streaming unsupported")
                yield  # pragma: no cover

        async def _fallback(*a, **k):
            return AnalysisResult(policy_type="Fallback", audit_ready_summary="s")

        monkeypatch.setattr(llm_service, "get_provider", lambda: _Broken())
        monkeypatch.setattr(llm_service, "analyze_policy", _fallback)

        results = [r async for r in llm_service.analyze_policy_stream("policy text")]
        assert results[-1][1] is True
        assert results[-1][0].policy_type == "Fallback"


class TestTheOutputWasTrimmed:
    """The other half of the speed change: ask for less."""

    def test_the_gap_table_ceiling_came_down(self):
        from app.services.llm_service import RESPONSE_SCHEMA
        assert "at most 6 rows" in RESPONSE_SCHEMA
        assert "at most 10 rows" not in RESPONSE_SCHEMA

    def test_the_token_ceiling_matches_the_smaller_schema(self):
        from app.config import Settings
        assert Settings.model_fields["llm_max_tokens"].default == 6000

    def test_drafting_keeps_its_larger_budget(self):
        """A drafted policy is a full document and is legitimately longer."""
        from app.config import Settings
        assert Settings.model_fields["llm_max_tokens_long"].default == 12000

    def test_an_empty_result_is_still_valid_after_trimming(self):
        """Trimming reduces the ceiling. It must not reintroduce a floor."""
        from app.services.llm_service import RESPONSE_SCHEMA
        assert "EMPTY gap_table is a valid" in RESPONSE_SCHEMA
        assert "Never pad output to meet a count" in RESPONSE_SCHEMA
