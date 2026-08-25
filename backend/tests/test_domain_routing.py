"""
An analysis must be grounded in its own regulatory domain.

Reported symptom: an OSHA industrial noise policy came back grounded in 45 CFR
Part 164 (HIPAA), 45 CFR Part 92 and OIG nursing-facility guidance, and the
exported policy closed by advising the reader to "consult qualified healthcare
compliance counsel" alongside a statement about protected health information.

Cause: every industry's regulations share one `federal_regulation` collection,
and retrieval only ever filtered by jurisdiction. The selected industry shaped
the wording of the semantic query and nothing else, so nothing prevented a
search from returning another sector's law -- "employee training", "records
retention" and "notification requirements" read as similar text whichever
statute they sit in. Semantic similarity cannot separate regulatory domains;
only a filter can.

The product has since been narrowed to healthcare. That makes the scoping
below more important, not less: the remaining verticals sit close together,
so a pharmacy analysis leaking hospital Conditions of Participation is a
subtler and more plausible failure than the original one.

Run: python -m pytest tests/test_domain_routing.py -v
"""

import pytest

from app.services.retrieval.retriever import ComplianceRetriever

HEALTHCARE_VERTICALS = ["healthcare", "home_health", "pharmacy"]


@pytest.fixture
def retriever():
    return ComplianceRetriever.__new__(ComplianceRetriever)


class TestScopeIsHealthcare:
    """The product is positioned for healthcare compliance officers. Anything
    it offers has to be backed by regulation actually loaded for it."""

    def test_only_healthcare_verticals_are_offered(self):
        from app.services.industry_config import INDUSTRIES
        assert set(INDUSTRIES) == set(HEALTHCARE_VERTICALS)

    def test_the_catch_all_is_gone(self):
        """"Other / General" invited exactly the cross-domain failure above."""
        from app.services.industry_config import INDUSTRIES
        assert "other" not in INDUSTRIES

    def test_every_vertical_has_a_policy_menu(self):
        from app.services.industry_config import INDUSTRIES, get_policy_types
        for slug in INDUSTRIES:
            assert get_policy_types(slug), f"{slug} has no policy types"


class TestIndustryScoping:
    def test_pharmacy_cannot_reach_hospital_conditions_of_participation(self, retriever):
        allowed = retriever._allowed_citations("pharmacy")
        assert "42 CFR Part 482" not in allowed

    def test_hospitals_cannot_reach_dea_registration_rules(self, retriever):
        allowed = retriever._allowed_citations("healthcare")
        assert "21 CFR Part 1301" not in allowed

    def test_each_vertical_reaches_its_own_law(self, retriever):
        expected = {
            "healthcare": "42 CFR Part 482",
            "home_health": "42 CFR Part 484",
            "pharmacy": "21 CFR Part 1306",
        }
        for slug, citation in expected.items():
            assert citation in retriever._allowed_citations(slug)

    def test_hipaa_reaches_every_healthcare_vertical(self, retriever):
        for slug in HEALTHCARE_VERTICALS:
            assert "45 CFR Part 164" in retriever._allowed_citations(slug), slug

    def test_an_unknown_industry_is_not_scoped(self, retriever):
        """Absent a known industry, fall back to unfiltered rather than
        silently returning nothing."""
        assert retriever._allowed_citations(None) is None
        assert retriever._allowed_citations("manufacturing") is None

    def test_the_filter_combines_industry_and_jurisdiction(self, retriever):
        combined = retriever._build_metadata_filter("Tennessee", "healthcare")
        assert "$and" in combined
        assert len(combined["$and"]) == 2

    def test_the_filter_is_flat_with_one_clause(self, retriever):
        only_industry = retriever._build_metadata_filter(None, "healthcare")
        assert "$and" not in only_industry
        assert "$or" in only_industry

    def test_scoping_filters_on_the_part_not_the_section(self, retriever):
        """eCFR content is stored per section, so a filter comparing `citation`
        against part-level strings would match nothing at all."""
        clause = retriever._build_metadata_filter(None, "healthcare")
        assert any("part_citation" in c for c in clause["$or"])
        assert not any("citation" in c and "part_citation" not in c for c in clause["$or"])

    def test_guidance_without_a_part_is_not_filtered_out(self, retriever):
        """OIG/HCCA compliance-program guidance has no CFR part, so it must be
        admitted by the empty-part arm.

        Under the old `citation` filter it was excluded from every retrieval
        where an industry was named -- which is every real request -- so the
        material defining the seven elements of an effective compliance program
        never reached a healthcare analysis."""
        clause = retriever._build_metadata_filter(None, "healthcare")
        assert {"part_citation": ""} in clause["$or"]


class TestEmploymentBaselineSurvivedTheNarrowing:
    """The employment baseline used to live inside "Other / General", so every
    other vertical inherited it from there. Removing that category would have
    silently stripped ADA, FMLA and OSHA out of healthcare -- and a hospital
    asking for an absenteeism policy would have had nothing real to cite."""

    @pytest.mark.parametrize("slug", HEALTHCARE_VERTICALS)
    @pytest.mark.parametrize("citation,name", [
        ("29 CFR Part 825", "FMLA"),
        ("29 CFR Part 1630", "ADA"),
        ("29 CFR Part 1601", "Title VII"),
        ("29 CFR Part 1910", "OSHA"),
        ("29 CFR Part 541", "FLSA"),
    ])
    def test_employment_law_reaches_every_vertical(self, retriever, slug, citation, name):
        assert citation in retriever._allowed_citations(slug), f"{name} missing for {slug}"

    def test_the_baseline_belongs_to_no_industry(self):
        from app.services.industry_config import BASELINE_EMPLOYMENT_TARGETS, INDUSTRIES
        assert len(BASELINE_EMPLOYMENT_TARGETS) == 7
        for cfg in INDUSTRIES.values():
            parts = {(t, p) for t, p, _, _ in cfg.get("ecfr_targets", [])}
            for title, part, _, _ in BASELINE_EMPLOYMENT_TARGETS:
                assert (title, part) not in parts, "baseline duplicated into an industry"

    def test_the_baseline_is_still_downloaded(self):
        """Nothing references these parts through an industry any more, so if
        the seeder did not list them separately they would simply stop being
        fetched."""
        from app.services.industry_config import BASELINE_EMPLOYMENT_TARGETS
        from app.services.retrieval.ecfr_client import ECFR_TARGETS

        seeded = {(t, p) for t, p, _, _ in ECFR_TARGETS}
        for title, part, label, _ in BASELINE_EMPLOYMENT_TARGETS:
            assert (title, part) in seeded, f"{label} is no longer seeded"


class TestGuidanceScoping:
    """OIG/HCCA compliance-program guidance governs federal health care program
    participants."""

    @pytest.mark.parametrize("industry", HEALTHCARE_VERTICALS)
    def test_guidance_reaches_healthcare_sectors(self, retriever, industry):
        cols = retriever._get_relevant_collections("gap_analysis", None, industry)
        assert "federal_guidance" in cols

    def test_collections_are_not_mutated_between_calls(self, retriever):
        """The step map was previously returned by reference and appended to,
        so a state-law lookup leaked into later, unrelated requests."""
        first = retriever._get_relevant_collections("gap_analysis", "Tennessee", "healthcare")
        second = retriever._get_relevant_collections("gap_analysis", None, "healthcare")
        assert "state_law" in first
        assert "state_law" not in second


class TestExportBoilerplate:
    def test_counsel_matches_the_sector(self):
        from app.services.export_service import _counsel_phrase
        assert "healthcare" in _counsel_phrase("healthcare")
        assert "pharmacy" in _counsel_phrase("pharmacy")

    def test_unknown_industry_gets_neutral_wording(self):
        from app.services.export_service import _counsel_phrase
        assert _counsel_phrase(None) == "qualified compliance counsel"

    def test_phi_language_matches_the_sector(self):
        from app.services.export_service import _handles_phi
        for slug in HEALTHCARE_VERTICALS:
            assert _handles_phi(slug) is True
        assert _handles_phi(None) is False

    def test_models_carry_the_industry_through(self):
        """The exporter cannot pick sector wording if nothing tells it the
        sector."""
        from app.models.schemas import ComplianceActionPackage, RewrittenPolicy
        assert "industry" in ComplianceActionPackage.model_fields
        assert "industry" in RewrittenPolicy.model_fields

    def test_a_policy_does_not_call_itself_a_report(self):
        import inspect
        from app.services.export_service import _add_disclaimer_box
        source = inspect.getsource(_add_disclaimer_box)
        assert 'document_kind' in source
        assert '"This policy"' in source
