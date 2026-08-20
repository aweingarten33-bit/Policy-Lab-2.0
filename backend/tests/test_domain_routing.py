"""
An analysis must be grounded in its own regulatory domain.

Reported symptom: an OSHA industrial noise policy for a manufacturing plant
came back grounded in 45 CFR Part 164 (HIPAA), 45 CFR Part 92 and OIG
nursing-facility guidance, and the exported policy closed by advising the
reader to "consult qualified healthcare compliance counsel" alongside a
statement about protected health information.

Cause: every industry's regulations share one `federal_regulation` collection,
and retrieval only ever filtered by jurisdiction. The selected industry shaped
the wording of the semantic query and nothing else, so nothing prevented a
search from returning another sector's law -- and "employee training",
"records retention" and "notification requirements" read as similar text
whichever statute they sit in. Semantic similarity cannot separate regulatory
domains; only a filter can.

The export boilerplate was hardcoded healthcare regardless of sector.

Run: python -m pytest tests/test_domain_routing.py -v
"""

import pytest

from app.services.retrieval.retriever import ComplianceRetriever


@pytest.fixture
def retriever():
    return ComplianceRetriever.__new__(ComplianceRetriever)


class TestIndustryScoping:
    def test_healthcare_law_is_unreachable_from_a_factory(self, retriever):
        """The exact leak that was reported."""
        allowed = retriever._allowed_citations("manufacturing")
        for healthcare_part in ("45 CFR Part 164", "45 CFR Part 160",
                                "42 CFR Part 2", "45 CFR Part 92"):
            assert healthcare_part not in allowed, (
                f"{healthcare_part} is still reachable from a manufacturing analysis"
            )

    def test_osha_is_reachable_from_a_factory(self, retriever):
        allowed = retriever._allowed_citations("manufacturing")
        assert "29 CFR Part 1910" in allowed
        assert "29 CFR Part 1904" in allowed, "hearing-loss recordability lives here"

    def test_healthcare_still_reaches_its_own_law(self, retriever):
        allowed = retriever._allowed_citations("healthcare")
        assert "45 CFR Part 164" in allowed
        assert "42 CFR Part 411" in allowed

    def test_employment_law_reaches_every_industry(self, retriever):
        """FMLA and the ADA bind a factory and a hospital alike."""
        for industry in ("healthcare", "manufacturing", "pharmacy", "child_family_services"):
            allowed = retriever._allowed_citations(industry)
            assert "29 CFR Part 825" in allowed, f"FMLA missing for {industry}"
            assert "29 CFR Part 1630" in allowed, f"ADA missing for {industry}"

    def test_an_unknown_industry_is_not_scoped(self, retriever):
        """Absent a known industry, fall back to the old unfiltered behaviour
        rather than silently returning nothing."""
        assert retriever._allowed_citations(None) is None
        assert retriever._allowed_citations("not_a_real_industry") is None

    def test_the_filter_combines_industry_and_jurisdiction(self, retriever):
        combined = retriever._build_metadata_filter("New York", "manufacturing")
        assert "$and" in combined
        assert len(combined["$and"]) == 2

    def test_the_filter_is_flat_with_one_clause(self, retriever):
        only_industry = retriever._build_metadata_filter(None, "manufacturing")
        assert "$and" not in only_industry
        assert "citation" in only_industry


class TestGuidanceScoping:
    """OIG/HCCA compliance-program guidance governs federal health care program
    participants, and has nothing to say about a factory's noise policy."""

    @pytest.mark.parametrize("industry", ["healthcare", "home_health", "pharmacy"])
    def test_guidance_reaches_healthcare_sectors(self, retriever, industry):
        cols = retriever._get_relevant_collections("gap_analysis", None, industry)
        assert "federal_guidance" in cols

    @pytest.mark.parametrize("industry", ["manufacturing", "other", "child_family_services"])
    def test_guidance_is_excluded_elsewhere(self, retriever, industry):
        cols = retriever._get_relevant_collections("gap_analysis", None, industry)
        assert "federal_guidance" not in cols

    def test_collections_are_not_mutated_between_calls(self, retriever):
        """The step map was previously returned by reference and appended to,
        so a state-law lookup leaked into later, unrelated requests."""
        first = retriever._get_relevant_collections("gap_analysis", "New York", "healthcare")
        second = retriever._get_relevant_collections("gap_analysis", None, "healthcare")
        assert "state_law" in first
        assert "state_law" not in second


class TestManufacturingVertical:
    def test_the_vertical_exists(self):
        from app.services.industry_config import INDUSTRIES
        assert "manufacturing" in INDUSTRIES

    def test_it_offers_a_noise_policy(self):
        from app.services.industry_config import get_policy_types
        slugs = {p["slug"] for p in get_policy_types("manufacturing")}
        assert "hearing_conservation" in slugs

    def test_its_live_sources_are_osha_not_hhs(self):
        from app.services.industry_config import INDUSTRIES
        from app.services.retrieval.live_research import CURATED_SOURCES

        sources = INDUSTRIES["manufacturing"]["live_research_sources"]
        assert "osha_standards" in sources
        for healthcare_source in ("hhs_regulations", "ocr_enforcement", "cms_guidance", "oig_advisory"):
            assert healthcare_source not in sources
        for key in sources:
            assert key in CURATED_SOURCES, f"{key} is not a configured source"

    def test_the_persona_requires_checking_state_plans(self):
        """Tennessee runs TOSHA; an analysis that names only federal OSHA for a
        Tennessee plant has the enforcing authority wrong."""
        from app.services.industry_config import INDUSTRIES
        persona = INDUSTRIES["manufacturing"]["persona"].lower()
        assert "state plan" in persona
        assert "tosha" in persona

    def test_the_persona_rejects_certifications_as_requirements(self):
        """LEED acoustic 'requirements' were invented for a plant whose
        certification does not carry them."""
        persona = __import__(
            "app.services.industry_config", fromlist=["INDUSTRIES"]
        ).INDUSTRIES["manufacturing"]["persona"].lower()
        assert "leed" in persona
        assert "not regulatory requirements" in persona


class TestExportBoilerplate:
    def test_counsel_matches_the_sector(self):
        from app.services.export_service import _counsel_phrase
        assert "healthcare" in _counsel_phrase("healthcare")
        assert "healthcare" not in _counsel_phrase("manufacturing")
        assert "occupational safety" in _counsel_phrase("manufacturing")

    def test_unknown_industry_gets_neutral_wording(self):
        from app.services.export_service import _counsel_phrase
        assert _counsel_phrase(None) == "qualified compliance counsel"

    def test_phi_language_only_where_phi_exists(self):
        from app.services.export_service import _handles_phi
        assert _handles_phi("healthcare") is True
        assert _handles_phi("manufacturing") is False
        assert _handles_phi(None) is False

    def test_models_carry_the_industry_through(self):
        """The exporter cannot pick sector wording if nothing tells it the
        sector."""
        from app.models.schemas import ComplianceActionPackage, RewrittenPolicy
        assert "industry" in ComplianceActionPackage.model_fields
        assert "industry" in RewrittenPolicy.model_fields


class TestPromptDiscipline:
    def _protocol(self):
        from app.services.llm_service import ANALYTICAL_PROTOCOL
        return ANALYTICAL_PROTOCOL

    def test_no_hardcoded_year_for_the_model_to_copy(self):
        """Reports generated in 2026 labelled regulations "current through
        2024" because the instructions used that as an example."""
        assert "current through 2024" not in self._protocol()

    def test_applicability_is_checked_before_a_gap_is_raised(self):
        """A statute listed in the references is not evidence it applies;
        FMLA and ADEA workflows were invented inside a noise policy."""
        protocol = self._protocol().lower()
        assert "applicability before gap" in protocol
        assert "remove the irrelevant" in protocol

    def test_optional_provisions_are_not_deficiencies(self):
        """OSHA says an age-correction allowance "may be made"; it was reported
        as a Must Fix."""
        protocol = self._protocol().lower()
        assert "required vs optional" in protocol
        assert '"may"' in protocol

    def test_stricter_company_rules_are_not_attributed_to_regulators(self):
        """A 30-year retention period was presented as an OSHA requirement
        when the standard says two years."""
        protocol = self._protocol().lower()
        assert "exceeds the regulatory minimum" in protocol
