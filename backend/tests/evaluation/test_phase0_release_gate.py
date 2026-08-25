"""Phase 0 release gate.

The release condition for this phase, stated once and checked mechanically:

    Unsupported legal claims presented as VERIFIED = 0 across the suite.

Every case in cases.py is run through the real verification path — the same
build_claim_evidence / apply_claim_support the production analysis uses — with
the entailment classifier's most generous possible answer fed in. A case that
must not verify has to survive a classifier saying "supported", because that is
the failure mode being guarded against: a model calling something supported and
nothing downstream disagreeing.

Run: python -m pytest tests/evaluation -v
"""

import pytest

from app.models.schemas import ClaimSupport, SourceStatus, VerificationStatus
from app.services.retrieval.verification import VerificationService

from tests.evaluation.cases import EVAL_CASES, cases_tagged, make_context


@pytest.fixture
def verifier(tmp_path, monkeypatch):
    """A verifier with an empty store and an empty section store.

    Both are repointed at a temp directory so a case can never be rescued by
    material the developer's machine happens to have baked in.
    """
    from app.services.retrieval import store as store_module
    from app.services.retrieval import section_store as section_module

    monkeypatch.setattr(
        store_module, "_store", store_module.ChromaStore(persist_dir=str(tmp_path / "kb"))
    )
    monkeypatch.setattr(
        section_module, "_section_store", section_module.SectionStore(persist_dir=str(tmp_path / "kb"))
    )
    return VerificationService()


def run_case(verifier, case):
    """Verify one case exactly as production does, and return the evidence."""
    evidence = verifier.build_claim_evidence(
        claim_id=case.name,
        claim_text=case.claim,
        citation=case.citation,
        retrieval_context=make_context(*case.results),
    )
    if case.apply_support is not None:
        verifier.apply_claim_support(evidence, ClaimSupport(case.apply_support))
    return evidence


# ── The gate ──


def test_no_unsupported_claim_is_ever_presented_as_verified(verifier):
    """The Phase 0 release condition.

    Reported as one list rather than one assertion per case so a regression
    shows every case it broke, not just the first.
    """
    wrongly_verified = []
    for case in EVAL_CASES:
        evidence = run_case(verifier, case)
        if evidence.status in case.forbidden_statuses:
            wrongly_verified.append(
                f"{case.name}: got {evidence.status.value} "
                f"(forbidden: {[s.value for s in case.forbidden_statuses]}) — {evidence.reason}"
            )

    assert not wrongly_verified, (
        "Phase 0 release gate FAILED — claims reached a forbidden verification "
        "status:\n  " + "\n  ".join(wrongly_verified)
    )


@pytest.mark.parametrize("case", EVAL_CASES, ids=lambda c: c.name)
def test_each_case_reaches_its_known_answer(verifier, case):
    evidence = run_case(verifier, case)

    if case.expect_status is not None:
        assert evidence.status is case.expect_status, (
            f"{case.name}: expected {case.expect_status.value}, got "
            f"{evidence.status.value} — {evidence.reason}"
        )
    if case.expect_citation_exists is not None:
        assert evidence.checks.citation_exists is case.expect_citation_exists, case.name
    if case.expect_source_status is not None:
        assert evidence.checks.source_status is case.expect_source_status, case.name


# ── The specific guarantees, called out so a failure names the property ──


@pytest.mark.parametrize("case", cases_tagged("source_status"), ids=lambda c: c.name)
def test_only_a_current_source_can_confirm_a_present_duty(verifier, case):
    evidence = run_case(verifier, case)
    is_current = case.expect_source_status is SourceStatus.current_verified
    assert evidence.checks.source_status_current is is_current, case.name
    if not is_current:
        assert evidence.status is not VerificationStatus.verified, (
            f"{case.name}: a {case.expect_source_status.value} source produced a "
            f"verified present-duty claim"
        )


def test_the_system_can_say_cannot_determine(verifier):
    """CANNOT_DETERMINE has to be reachable, not merely defined.

    An enum member nothing ever produces is not a safety property. This asserts
    that insufficient standing actually lands there rather than being folded
    into unverified, so a reader is told the difference between "checked and
    unsupported" and "cannot be checked against present law at all".
    """
    outcomes = {
        case.name: run_case(verifier, case).status
        for case in cases_tagged("source_status")
    }
    assert VerificationStatus.cannot_determine in outcomes.values(), outcomes


def test_the_truncation_regression_case_still_verifies(verifier):
    """The controlling subsection sits past character 3,000 and must be reachable.

    This is the case the old eCFR cap broke: the paragraph that decides the
    claim was discarded during ingestion, so the claim came back unverifiable
    and the regulation looked silent on something it states plainly.
    """
    (case,) = [c for c in EVAL_CASES if c.name == "subsection_past_the_old_truncation_point"]
    evidence = run_case(verifier, case)

    assert evidence.checks.citation_exists is True, evidence.reason
    assert evidence.status is VerificationStatus.verified, evidence.reason
    assert "seven years" in (evidence.source.excerpt or ""), (
        "the excerpt must contain the operative text, not just prove the marker exists"
    )


@pytest.mark.parametrize("case", cases_tagged("modality"), ids=lambda c: c.name)
def test_permissive_language_cannot_become_a_mandate(verifier, case):
    evidence = run_case(verifier, case)
    assert evidence.status is not VerificationStatus.verified, (
        f"{case.name}: a mandate was verified against non-mandatory text — {evidence.reason}"
    )


@pytest.mark.parametrize("case", cases_tagged("citation"), ids=lambda c: c.name)
def test_citation_integrity(verifier, case):
    evidence = run_case(verifier, case)
    if case.expect_citation_exists is False:
        assert evidence.checks.citation_exists is False, case.name
        assert evidence.status is not VerificationStatus.verified, case.name
