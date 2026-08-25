"""Prompts teach the method. Retrieval supplies the law.

Real regulatory facts were written into generic model instructions: a specific
NPRM and its expected final-rule year, a DOJ takedown's dollar figure and
defendant count, which OIG guidance was published in which month, an
initial-assessment window, an annual in-service training figure, a supervisory
visit cadence, and a worked calibration example built entirely from real HIPAA
breach-notification provisions and deadlines.

Every one of those is a fact that ages, that the model then states with
confidence, and that no source in the pipeline was ever asked to confirm — while
the product tells the reader every statement is grounded. A prompt-embedded
regulatory fact is a hallucination with a long fuse.

Run: python -m pytest tests/evaluation/test_prompts_carry_no_current_law.py -v
"""

import re

import pytest

from app.services.llm_service import ANALYTICAL_PROTOCOL, RESPONSE_SCHEMA, _build_system_prompt


def _instruction_surfaces():
    """Prompt text only.

    The `regulations` and `ecfr_targets` lists are deliberately excluded: those
    scope WHICH authorities to retrieve and check, which is configuration, not
    a claim about what any of them says.
    """
    from app.services.draft_policy_service import _build_draft_system_prompt
    from app.services.industry_config import INDUSTRIES
    from app.services.rewrite_service import REWRITE_TASK_INSTRUCTIONS

    surfaces = {
        "analysis_protocol": ANALYTICAL_PROTOCOL,
        "response_schema": RESPONSE_SCHEMA,
        "rewrite_instructions": REWRITE_TASK_INSTRUCTIONS,
    }
    for slug in INDUSTRIES:
        surfaces[f"persona[{slug}]"] = INDUSTRIES[slug]["persona"]
        surfaces[f"draft_prompt[{slug}]"] = _build_draft_system_prompt(slug, None)
    return surfaces


def _report(pattern, description, *, flags=re.IGNORECASE):
    offenders = []
    for name, text in _instruction_surfaces().items():
        for match in re.finditer(pattern, text, flags):
            start = max(0, match.start() - 80)
            offenders.append(f"{name}: ...{text[start:match.end() + 80]!r}...")
    assert not offenders, (
        f"{description}\n  " + "\n  ".join(offenders)
    )


class TestNoDatedLegalFacts:
    def test_no_specific_years_are_asserted(self):
        """A year in an instruction is either a rule's status ("the 2025 NPRM"),
        a guidance publication date, or an enforcement period — all three are
        current-law facts that belong to a retrieved source."""
        _report(
            r"\b(?:19|20)\d{2}\b",
            "A prompt asserts a specific year. Dates must come from the retrieved source.",
        )

    def test_no_year_ranges(self):
        _report(
            r"\b(?:19|20)\d{2}\s*[-–]\s*(?:19|20)?\d{2}\b",
            "A prompt asserts a year range (an enforcement or update window).",
        )

    def test_no_month_and_year_stamps(self):
        _report(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(?:19|20)\d{2}\b",
            "A prompt stamps a document with a publication month.",
        )


class TestNoEnforcementStatistics:
    def test_no_dollar_figures(self):
        _report(
            r"\$\s?[\d.,]+\s*(?:B|M|billion|million|thousand)?\b",
            "A prompt states an enforcement dollar figure.",
        )

    def test_no_case_or_defendant_counts(self):
        _report(
            r"\b\d[\d,]*\s+(?:defendants?|settlements?|cases?|enforcement actions?)\b",
            "A prompt states an enforcement count.",
        )


class TestNoMemorisedDeadlines:
    """Regulatory intervals taught before the model reads the source.

    Once the model knows "48 hours" from the instruction, it will state it with
    a citation whether or not the retrieved text says so — and a wrong deadline
    presented as law is the most harmful output this product can produce.
    """

    def test_no_hour_or_day_deadlines_in_instructions(self):
        _report(
            r"\bwithin\s+\d+\s*(?:-|\s)?(?:hours?|days?|calendar days?|business days?)\b",
            "A prompt states a specific regulatory deadline.",
        )

    def test_no_training_hour_quotas(self):
        _report(
            r"\b\d+\s*-?\s*hours?\s+(?:annual|in-service|of\s+training)\b",
            "A prompt states a specific training-hours requirement.",
        )

    def test_no_visit_or_review_cadences(self):
        _report(
            r"\b\d+\s*-\s*day\s+(?:for|supervisory|cadence|cycle)\b",
            "A prompt states a specific visit or review cadence.",
        )


class TestTheCalibrationExampleIsFictional:
    def test_the_worked_example_uses_a_nonexistent_part(self):
        assert "999.45" in ANALYTICAL_PROTOCOL
        assert "FICTIONAL" in ANALYTICAL_PROTOCOL

    def test_the_example_says_not_to_reuse_its_citations(self):
        collapsed = " ".join(ANALYTICAL_PROTOCOL.split())
        assert "Do not carry any citation, section number, deadline, threshold or count out of" in collapsed

    def test_the_old_real_hipaa_example_is_gone(self):
        for real in ("164.404", "164.402", "164.408", "164.406", "164.308"):
            assert real not in ANALYTICAL_PROTOCOL, f"real provision {real} still in the protocol"


class TestStatusMustComeFromTheSource:
    def test_the_protocol_forbids_asserting_a_rule_status_from_memory(self):
        collapsed = " ".join(ANALYTICAL_PROTOCOL.split())
        assert "Do not supply the status of a rule from memory" in collapsed

    def test_the_protocol_routes_status_through_the_source_status_field(self):
        assert "CURRENT_VERIFIED" in ANALYTICAL_PROTOCOL
        assert "STATUS_UNKNOWN" in ANALYTICAL_PROTOCOL

    @pytest.mark.parametrize("slug", ["healthcare", "home_health", "pharmacy"])
    def test_each_persona_defers_enforcement_facts_to_retrieval(self, slug):
        from app.services.industry_config import INDUSTRIES
        persona = INDUSTRIES[slug]["persona"].lower()
        assert "from memory" in persona or "reference material" in persona or "retrieved" in persona, (
            f"{slug}: the persona gives no instruction to source its facts"
        )
