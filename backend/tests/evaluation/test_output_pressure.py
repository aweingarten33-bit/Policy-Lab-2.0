"""Output pressure — the model must be allowed to return nothing.

A prompt that says "6-10 entries" or "minimum one, often 2-4" is not a format
hint. It is an instruction to keep going after the evidence has run out, and
what comes out of that is a plausible-looking finding attached to a real
citation that does not support it. Padding a compliance report is not a
cosmetic problem: the reader cannot tell the padding from the findings.

These tests check two things. That no minimum survives anywhere in the prompt
surface, and that the parsing and scoring path downstream handles an empty or
tiny result correctly instead of treating it as a failure.

Run: python -m pytest tests/evaluation/test_output_pressure.py -v
"""

import re

import pytest

from app.models.schemas import GapStatus
from app.services.llm_service import (
    ANALYTICAL_PROTOCOL,
    RESPONSE_SCHEMA,
    _build_system_prompt,
    _parse_llm_response,
)


def _all_prompt_surfaces():
    """Every instruction string a model is handed, across all three flows."""
    from app.services.draft_policy_service import _build_draft_system_prompt
    from app.services.industry_config import INDUSTRIES
    from app.services.rewrite_service import REWRITE_TASK_INSTRUCTIONS

    surfaces = {
        "analysis_protocol": ANALYTICAL_PROTOCOL,
        "response_schema": RESPONSE_SCHEMA,
        "rewrite_instructions": REWRITE_TASK_INSTRUCTIONS,
    }
    for slug in INDUSTRIES:
        surfaces[f"analysis_system_prompt[{slug}]"] = _build_system_prompt(slug)
        surfaces[f"draft_system_prompt[{slug}]"] = _build_draft_system_prompt(slug, None)
    return surfaces


# Phrasings that tell a model to produce a certain number of items regardless
# of how many it found. Each is written to avoid matching a ceiling ("at most
# 10", "up to 4"), which is a legitimate budget control.
_QUOTA_PATTERNS = [
    # "6-10 entries", "3–5 sections" — a range presented as the expected output
    (r"\b\d+\s*[-–]\s*\d+\s+(?:entries|items|rows|findings|sections|citations|regulations|policies|recommendations)\b",
     "a numeric range presented as an output target"),
    # Scoped to output counts. "the regulatory minimum" is a legal concept the
    # prompt legitimately discusses, and "there is no minimum" is the rule
    # itself -- neither is a quota.
    (r"\bminimum\s+(?:of\s+)?(?:\d+|one|two|three|four|five|six)\b(?!\s+(?:required|set|fixed)\s+by)",
     "an explicit minimum count"),
    (r"\bat\s+least\s+\d+\b", "an explicit floor"),
    (r"\bno\s+fewer\s+than\s+\d+\b", "an explicit floor"),
    (r"\boften\s+\d+\s*[-–]\s*\d+\b", "a suggested count"),
    (r"\breturn\s+fewer\s+than\s+\d+\b", "a count framed as the norm"),
]


@pytest.mark.parametrize("pattern,description", _QUOTA_PATTERNS)
def test_no_prompt_sets_a_minimum_output_count(pattern, description):
    offenders = []
    for name, text in _all_prompt_surfaces().items():
        for match in re.finditer(pattern, text, re.IGNORECASE):
            start = max(0, match.start() - 70)
            offenders.append(f"{name}: ...{text[start:match.end() + 70]!r}...")
    assert not offenders, (
        f"Found {description} in a prompt. A minimum makes the model invent "
        f"items when the evidence has run out:\n  " + "\n  ".join(offenders)
    )


def test_the_schema_says_an_empty_result_is_valid():
    """The instruction has to be explicit. Absence of a minimum is not the same
    as permission to return nothing — a model asked for a 'gap analysis' will
    supply gaps unless told that none is an acceptable answer."""
    assert "EMPTY gap_table is a valid, complete result" in RESPONSE_SCHEMA
    assert "Never pad output to meet a count" in RESPONSE_SCHEMA


def test_the_schema_states_ceilings_are_not_targets():
    assert "CEILINGS ONLY, NO MINIMUMS" in RESPONSE_SCHEMA
    assert "a ceiling, and ONLY a ceiling" in RESPONSE_SCHEMA


class TestAPolicyWithNoGapsParsesCleanly:
    """A fully compliant policy must survive the whole parsing path.

    If an empty gap_table crashed, or scored as zero-percent compliant, the
    prompt's permission to return nothing would be worthless — the model would
    be right and the product would still be wrong.
    """

    def test_zero_findings_is_not_an_error(self):
        result = _parse_llm_response(
            '{"policy_type": "Test Policy", "gap_table": [], "priority_findings": [], '
            '"audit_ready_summary": "No material gaps were identified."}'
        )
        assert result.gap_table == []
        assert result.priority_findings == []
        assert result.critical_count == 0

    def test_zero_findings_does_not_score_as_zero_percent_compliant(self):
        """An empty table means nothing was measured, not that everything failed.

        Reporting 0% for a clean policy would be the most alarming possible way
        to say 'no problems found'."""
        result = _parse_llm_response(
            '{"policy_type": "T", "gap_table": [], "audit_ready_summary": "Clean."}'
        )
        assert result.compliance_score is None

    def test_two_findings_is_a_complete_result(self):
        """The case the old '6-10 entries' quota pushed against: a narrow policy
        where only two authorities genuinely apply."""
        result = _parse_llm_response("""
        {"policy_type": "Attendance Policy",
         "regulations_applied": ["29 CFR Part 825", "29 CFR Part 1630"],
         "gap_table": [
           {"clause": "Leave requests", "regulations": ["29 CFR Part 825"],
            "axes_passed": 2, "status": "partial", "finding": "f",
            "suggested_language": "s", "citation": "29 CFR Part 825"},
           {"clause": "Accommodation", "regulations": ["29 CFR Part 1630"],
            "axes_passed": 1, "status": "gap", "finding": "f",
            "suggested_language": "s", "citation": "29 CFR Part 1630"}
         ],
         "audit_ready_summary": "Two touchpoints."}
        """)
        assert len(result.gap_table) == 2
        assert len(result.regulations_applied) == 2
        assert result.gap_table[0].status is GapStatus.partial
        assert result.gap_table[1].status is GapStatus.gap
