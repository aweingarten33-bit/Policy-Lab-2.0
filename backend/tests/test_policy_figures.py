"""
Round 3, from the live sample run after round 2 (2026-10-08 00:38):

  finding-1  164.404  "not stated at that citation scope: 24 hour, 5 day, 60 day"
  finding-3  482.13; 164.524                         "...: 30 day"
  finding-4  164.530; 164.308                        "...: 30 day, 6 year"
  finding-5  164.308; 164.310                        "...: 90 day, 24 hour"
  finding-6  164.530                                 "...: 6 year"
  finding-2  164.504  BAA: "does not require the covered entity to execute written..."

Two causes. The figure check read only the ~800-character chunk retrieval
returned, so "60 calendar days" (§ 164.404(b)) and "6 years" (§ 164.530(j)) were
reported absent from sections that state them. And it demanded that every figure
appear in the regulation, so a policy's own stricter deadlines (notify within 24
hours, against a 60-day maximum) were counted as unverified facts. A policy is
allowed to be stricter than the law; a figure it chose is now checked for
CONFLICT. A figure the finding attributes to the law must still be stated there.

Run: python -m pytest tests/test_policy_figures.py -v
"""

import pytest

from app.models.schemas import VerificationStatus
from app.services.retrieval.models import (
    Jurisdiction, RetrievalContext, RetrievalResult, SourceCategory, SourceChunk,
    SourceMetadata, SourceStatus, SourceType,
)
from app.services.retrieval.verification import VerificationService

S164_404 = (
    "(a) Standard—(1) General rule. A covered entity shall, following the discovery of a breach of "
    "unsecured protected health information, notify each individual whose unsecured protected health "
    "information has been, or is reasonably believed by the covered entity to have been, accessed, "
    "acquired, used, or disclosed as a result of such breach. (2) Breaches treated as discovered. For "
    "purposes of paragraph (a)(1) of this section, a breach shall be treated as discovered by a covered "
    "entity as of the first day on which such breach is known to the covered entity. (b) Implementation "
    "specification: Timeliness of notification. Except as provided in § 164.412, a covered entity shall "
    "provide the notification required by paragraph (a) of this section without unreasonable delay and in "
    "no case later than 60 calendar days after discovery of a breach. (c) Implementation specifications: "
    "Content of notification. (d) Implementation specifications: Methods of individual notification. "
    "(2)(ii) In the case in which there are 10 or more individuals for which there is insufficient or "
    "out-of-date contact information, substitute notice shall be in the form of a conspicuous posting for "
    "a period of 90 days on the home page of the Web site of the covered entity."
)
S164_530 = (
    "(a)(1) Standard: Personnel designations. (i) A covered entity must designate a privacy official who "
    "is responsible for the development and implementation of the policies and procedures of the entity. "
    "(b)(1) Standard: Training. A covered entity must train all members of its workforce on the policies "
    "and procedures with respect to protected health information. (j)(1) Standard: Documentation. A covered "
    "entity must: (i) Maintain the policies and procedures provided for in paragraph (i) of this section in "
    "written or electronic form. (2) Implementation specification: Retention period. A covered entity must "
    "retain the documentation required by paragraph (j)(1) of this section for 6 years from the date of its "
    "creation or the date when it last was in effect, whichever is later."
)
S164_524 = (
    "(b)(1) The covered entity must permit an individual to request access to inspect or to obtain a copy "
    "of the protected health information about the individual. (2) Implementation specifications: Timely "
    "action by the covered entity. (i) Except as provided in paragraph (b)(2)(ii) of this section, the "
    "covered entity must act on a request for access no later than 30 days after receipt of the request."
)
S164_308 = (
    "(a) A covered entity or business associate must, in accordance with § 164.306: (1)(i) Standard: "
    "Security management process. Implement policies and procedures to prevent, detect, contain, and "
    "correct security violations. (5)(ii)(D) Password management (Addressable). Procedures for creating, "
    "changing, and safeguarding passwords. (6)(i) Standard: Security incident procedures. Implement "
    "policies and procedures to address security incidents."
)
S482_13 = (
    "(a) Standard: Notice of rights. A hospital must inform each patient, or when appropriate, the "
    "patient's representative, of the patient's rights. (d) Standard: Confidentiality of patient records. "
    "(2) The patient has the right to access information contained in his or her clinical records within "
    "a reasonable time frame."
)
S164_504 = (
    "(a) Definitions. As used in this section: Plan administration functions means administration functions "
    "performed by the plan sponsor of a group health plan on behalf of the group health plan. (e)(1) "
    "Standard: Business associate contracts. (i) The contract or other arrangement required by "
    "§ 164.502(e)(2) must meet the requirements of paragraph (e)(2), (e)(3), or (e)(5) of this section, as "
    "applicable. (ii) A covered entity is not in compliance with the standards in § 164.502(e) and this "
    "paragraph, if the covered entity knew of a pattern of activity or practice of the business associate "
    "that constituted a material breach or violation of the business associate's obligation under the "
    "contract or other arrangement, unless the covered entity took reasonable steps to cure the breach or "
    "end the violation, as applicable. (2) Implementation specifications: Business associate contracts. A "
    "contract between the covered entity and a business associate must: (i) Establish the permitted and "
    "required uses and disclosures of protected health information by the business associate. (ii) Provide "
    "that the business associate will: (A) Not use or further disclose the information other than as "
    "permitted or required by the contract or as required by law; (B) Use appropriate safeguards to prevent "
    "use or disclosure of the information other than as provided for by its contract. (f)(1) Standard: "
    "Requirements for group health plans."
)
S164_502 = (
    "(a) Standard. A covered entity or business associate may not use or disclose protected health "
    "information, except as permitted or required by this subpart. (e)(1) Standard: Disclosures to business "
    "associates. (i) A covered entity may disclose protected health information to a business associate and "
    "may allow a business associate to create, receive, maintain, or transmit protected health information "
    "on its behalf, if the covered entity obtains satisfactory assurance that the business associate will "
    "appropriately safeguard the information. (2) Implementation specification: Documentation. The "
    "satisfactory assurances required by paragraph (e)(1) of this section must be documented through a "
    "written contract or other written agreement or arrangement with the business associate that meets the "
    "applicable requirements of § 164.504(e). (f) Standard: Deceased individuals."
)

SECTIONS = {
    ("45", "164.404"): S164_404, ("45", "164.530"): S164_530, ("45", "164.524"): S164_524,
    ("45", "164.308"): S164_308, ("42", "482.13"): S482_13, ("45", "164.504"): S164_504,
    ("45", "164.502"): S164_502,
}

# The policy under analysis (the built-in hospital sample's figures).
POLICY = (
    "Workforce members must report a suspected breach to the Privacy Officer within 24 hours. The Privacy "
    "Officer completes a risk assessment within 5 business days. Affected individuals are notified without "
    "unreasonable delay and no later than 60 calendar days after discovery. Requests for access are acted on "
    "within 30 days. Documentation is retained for 6 years. User access is reviewed every 90 days and "
    "terminated within 24 hours of separation."
)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    from app.services.retrieval import section_store as section_module

    store = section_module.SectionStore(persist_dir=str(tmp_path / "kb"))
    monkeypatch.setattr(section_module, "_section_store", store)
    for (title, section), text in SECTIONS.items():
        part = section.split(".")[0]
        store.put_many([{
            "citation": f"{title} CFR § {section}", "part_citation": f"{title} CFR Part {part}",
            "source_name": f"{title} CFR Part {part} — § {section}", "full_text": text,
            "source_status": SourceStatus.current_verified.value,
        }])
    return VerificationService()


def _ctx():
    """Retrieval returned only the first ~300 characters of each section, as in production."""
    results = []
    for (title, section), text in SECTIONS.items():
        meta = SourceMetadata(
            source_name=f"{title} CFR — § {section}", source_type=SourceType.retrieved_source,
            category=SourceCategory.federal_regulation, jurisdiction=Jurisdiction.federal,
            citation=f"{title} CFR § {section}", part_citation=f"{title} CFR Part {section.split('.')[0]}",
            source_status=SourceStatus.current_verified, collection="federal_regulation",
        )
        chunk = SourceChunk(id=f"{title}-{section}", text=text[:300], metadata=meta)
        results.append(RetrievalResult(chunk=chunk, score=0.8, query="q"))
    return RetrievalContext(query="q", retrieved_chunks=results)


def _check(svc, claim, citation, policy=POLICY):
    return svc.build_claim_evidence("f", claim, citation, _ctx(), policy_text=policy)


class TestTheLiveSampleFindings:
    def test_breach_notification_figures_stricter_than_60_days_pass(self, svc):
        ev = _check(
            svc,
            "The policy requires reporting within 24 hours and a risk assessment within 5 business days, "
            "and notifies individuals no later than 60 calendar days after discovery.",
            "45 CFR § 164.404 — Notification to individuals; 45 CFR § 164.410 — Notification by a business associate",
        )
        assert ev.checks.specifics_supported is True, ev.reason
        assert ev.status is VerificationStatus.partially_verified  # awaiting the entailment check
        assert "60 day: stated in the cited text" in ev.checks.figure_check
        assert "24 hour: policy figure, within the 60 day maximum" in ev.checks.figure_check
        # The classifier sees the 60-day limit it is being compared with.
        assert "60 calendar days" in ev.source.excerpt

    def test_access_deadline_found_in_the_second_cited_section(self, svc):
        ev = _check(
            svc, "The policy should act on access requests within 30 days.",
            "42 CFR § 482.13 — Patient's rights; 45 CFR § 164.524 — Access of individuals",
        )
        assert ev.checks.specifics_supported is True, ev.reason

    def test_six_year_retention_is_found_in_the_full_section(self, svc):
        ev = _check(svc, "Documentation must be retained for 6 years.", "45 CFR § 164.530 — Administrative requirements")
        assert ev.checks.specifics_supported is True, ev.reason
        assert "6 year: stated" in ev.checks.figure_check

    def test_figures_the_regulation_does_not_quantify_are_organizational_choices(self, svc):
        ev = _check(
            svc, "User access is reviewed every 90 days and terminated within 24 hours of separation.",
            "45 CFR § 164.308 — Administrative safeguards; 45 CFR § 164.310 — Physical safeguards",
        )
        assert ev.checks.specifics_supported is True, ev.reason


class TestConflictsAreStillFlagged:
    def test_a_policy_deadline_longer_than_the_maximum_conflicts(self, svc):
        policy = "Affected individuals are notified within 90 days of discovery."
        ev = _check(svc, "The policy notifies individuals within 90 days of discovery.", "45 CFR § 164.404", policy)
        assert ev.checks.specifics_supported is False
        assert "conflict" in ev.reason and "longer than the 60 day maximum" in ev.reason

    def test_a_retention_period_shorter_than_the_minimum_conflicts(self, svc):
        policy = "Documentation is retained for 3 years."
        ev = _check(svc, "The policy retains documentation for 3 years.", "45 CFR § 164.530", policy)
        assert ev.checks.specifics_supported is False
        assert "shorter than the 6 year minimum" in ev.reason

    def test_a_longer_retention_period_is_compliant(self, svc):
        policy = "Documentation is retained for 7 years."
        ev = _check(svc, "The policy retains documentation for 7 years.", "45 CFR § 164.530", policy)
        assert ev.checks.specifics_supported is True, ev.reason

    def test_a_finding_that_names_the_gap_is_not_itself_a_conflict(self, svc):
        policy = "Affected individuals are notified within 90 days of discovery."
        ev = _check(
            svc, "The policy allows notification within 90 days; the maximum at 164.404 is 60 calendar days.",
            "45 CFR § 164.404", policy,
        )
        assert ev.checks.specifics_supported is True, ev.reason

    def test_a_figure_attributed_to_the_law_must_be_stated_there(self, svc):
        """Stricter is fine for a policy, never for a claim about what the law says."""
        policy = "Documentation is retained for 10 years."
        ev = _check(svc, "Retain documentation for 10 years as required by 45 CFR 164.530.", "45 CFR § 164.530", policy)
        assert ev.checks.specifics_supported is False
        assert "attributed to the regulation" in ev.reason

    def test_a_figure_not_from_the_policy_must_be_stated(self, svc):
        ev = _check(svc, "Breach notices must go out within 30 days.", "45 CFR § 164.404", policy="")
        assert ev.checks.specifics_supported is False


class TestBusinessAssociateExcerpt:
    CLAIM = (
        "The policy does not require a written business associate agreement before disclosing protected "
        "health information to a business associate. Suggested: The Hospital will execute a written business "
        "associate contract with each business associate that establishes the permitted uses and disclosures."
    )

    def test_the_excerpt_shows_the_standard_and_the_written_contract_it_incorporates(self, svc):
        ev = _check(svc, self.CLAIM, "45 CFR § 164.504 — Organizational requirements; 45 CFR § 160.103 — Definitions")
        assert ev.checks.citation_exists is True
        excerpt = ev.source.excerpt
        assert "Standard: Business associate contracts" in excerpt
        assert "required by § 164.502(e)(2)" in excerpt
        assert "Incorporated by reference — 45 CFR § 164.502(e)(2)" in excerpt
        assert "written contract" in excerpt


def test_a_regulation_said_to_require_a_figure_it_does_not_state_fails_even_if_the_policy_uses_it(svc):
    policy = "Affected individuals are notified within 30 calendar days."
    ev = _check(svc, "45 CFR 164.404 requires notification within 30 calendar days.", "45 CFR § 164.404", policy)
    assert ev.checks.specifics_supported is False
    assert ev.status is not VerificationStatus.verified


class TestBanner:
    """'6 of 6 not fully verified' hid which findings were partial and which failed."""

    def _rows(self, *statuses):
        from types import SimpleNamespace as NS
        return [NS(citation="45 CFR § 164.404", evidence=NS(status=s)) for s in statuses]

    def test_each_status_is_counted_separately(self):
        from app.services.limitations import verification_breakdown
        V = VerificationStatus
        line = verification_breakdown(self._rows(V.verified, V.verified, V.verified, V.verified,
                                                 V.partially_verified, V.unverified))
        assert line.startswith("6 cited finding(s): 4 verified, 1 partially verified, 1 not verified.")
        assert "Partially verified: the regulation was found" in line

    def test_all_verified(self):
        from app.services.limitations import verification_breakdown
        line = verification_breakdown(self._rows(VerificationStatus.verified))
        assert line == "1 cited finding(s): 1 verified. They still need review by counsel."
