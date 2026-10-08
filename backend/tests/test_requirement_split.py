"""
Round 4, from the live sample after round 3: 5 partially verified, 1 not verified.

Each finding was verified as one claim: finding + suggested language. That claim
mixed (a) what the regulation requires with (b) the model's recommendations and
stricter internal standards -- 24-hour internal reporting, a 72-hour team
assembly, HHS and media specifics. The verifier correctly found (b) not
established by the cited source, so no finding could reach verified.

Findings now carry the two parts separately. `regulatory_requirement` states
only what the cited text requires and is the only part checked against it;
`recommendations` are labelled best practice and never attributed to the
regulation. A finding whose regulatory requirement the cited text fully
supports is VERIFIED.

Run: python -m pytest tests/test_requirement_split.py -v
"""

import asyncio
import json

import pytest

from app.models.schemas import AnalysisResult, GapRow, GapStatus, ObligationType, VerificationStatus
from app.services.llm_service import _parse_llm_response
from app.services.orchestrator import PackageOrchestrator
from app.services.retrieval.models import SourceStatus
from tests.evaluation.cases import make_context, make_result

CITATION = "45 CFR § 164.404"
S164_404 = (
    "(a) Standard—(1) General rule. A covered entity shall, following the discovery of a breach of "
    "unsecured protected health information, notify each individual whose unsecured protected health "
    "information has been, or is reasonably believed by the covered entity to have been, accessed, "
    "acquired, used, or disclosed as a result of such breach. (b) Implementation specification: "
    "Timeliness of notification. Except as provided in § 164.412, a covered entity shall provide the "
    "notification required by paragraph (a) of this section without unreasonable delay and in no case "
    "later than 60 calendar days after discovery of a breach."
)
REQUIREMENT = (
    "A covered entity must notify each individual whose unsecured protected health information was "
    "breached without unreasonable delay and no later than 60 calendar days after discovery of the breach."
)
MIXED_FINDING = (
    "The policy says only 'as soon as possible' and names no deadline. Workforce members should report "
    "within 24 hours and the response team should assemble within 72 hours."
)
SUGGESTED = (
    "The Privacy Officer notifies each affected individual within 30 calendar days of discovery, and "
    "workforce members report suspected breaches within 24 hours."
)


class _Classifier:
    def __init__(self, label="SUPPORTED"):
        self.label = label
        self.claims = []

    async def __call__(self, pending):
        self.claims.extend(p["claim"] for p in pending)
        return {p["id"]: {"label": self.label, "note": "checked"} for p in pending}


@pytest.fixture
def orch(tmp_path, monkeypatch):
    from app.services.retrieval import section_store as section_module
    from app.services.retrieval import obligation_memory as memory_module
    import app.services.claim_support as cs

    monkeypatch.setattr(section_module, "_section_store", section_module.SectionStore(persist_dir=str(tmp_path / "kb")))
    monkeypatch.setattr(memory_module, "_memory", memory_module.ObligationMemory(persist_dir=str(tmp_path / "kb")))
    classifier = _Classifier()
    monkeypatch.setattr(cs, "classify_claim_support", classifier)
    return PackageOrchestrator(), classifier


def _run(orch, **row_fields):
    row = GapRow(
        clause="Breach notification", regulations=[CITATION], status=GapStatus.gap,
        finding=MIXED_FINDING, suggested_language=SUGGESTED, citation=CITATION,
        obligation_type=ObligationType.required, **row_fields,
    )
    result = AnalysisResult(policy_type="Breach policy", audit_ready_summary="S.", gap_table=[row])
    ctx = make_context(make_result(CITATION, S164_404, status=SourceStatus.current_verified))
    asyncio.run(orch._build_evidence(result, ctx, policy_text="Report as soon as possible."))
    return result.gap_table[0]


def test_a_supported_regulatory_requirement_is_verified(orch):
    o, classifier = orch
    row = _run(o, regulatory_requirement=REQUIREMENT,
               recommendations=["Report suspected breaches to the Privacy Officer within 24 hours."])
    assert row.evidence.status is VerificationStatus.verified, row.evidence.reason
    # Only the regulatory requirement went to the support check.
    assert classifier.claims == [REQUIREMENT]


def test_recommendations_are_not_verified_against_the_regulation(orch):
    o, _ = orch
    row = _run(o, regulatory_requirement=REQUIREMENT,
               recommendations=["Assemble the incident response team within 72 hours."])
    assert "72" not in (row.evidence.claim_text or "")


def test_a_stricter_figure_inside_the_regulatory_requirement_still_fails(orch):
    """The split is not a loophole: the requirement must say only what the text says."""
    o, _ = orch
    row = _run(o, regulatory_requirement=(
        "A covered entity must notify each affected individual within 24 hours of discovery of a breach."
    ))
    assert row.evidence.status is not VerificationStatus.verified
    assert row.evidence.checks.specifics_supported is False


def test_without_the_split_the_old_combined_claim_is_checked(orch):
    o, _ = orch
    row = _run(o)
    assert MIXED_FINDING in row.evidence.claim_text
    assert row.evidence.status is not VerificationStatus.verified  # 72 hours etc. not in the text


def test_the_parser_reads_both_parts():
    raw = json.dumps({
        "policy_type": "Breach policy",
        "gap_table": [{
            "clause": "Breach notification", "regulations": [CITATION], "axes_passed": 1, "status": "gap",
            "risk_level": "high", "current_state": "Policy is silent.", "finding": "No deadline.",
            "regulatory_requirement": REQUIREMENT,
            "recommendations": "Report within 24 hours. Assemble the team within 72 hours.",
            "suggested_language": "The Privacy Officer notifies individuals.", "citation": CITATION,
            "remediation_priority": "Immediate",
        }],
        "audit_ready_summary": "Summary.",
    })
    row = _parse_llm_response(raw).gap_table[0]
    assert row.regulatory_requirement == REQUIREMENT
    assert row.recommendations == ["Report within 24 hours.", "Assemble the team within 72 hours."]
