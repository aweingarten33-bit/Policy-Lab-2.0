"""
A draft must not invent facts about the organization.

From a live audit: a home-health draft created roles the agency does not have
("Director of Clinical Services", "Quality Improvement Coordinator"), made up
numeric deadlines, and stated a March 1, 2026 adoption date when no date was
supplied. A reader adopting the draft adopted the invention with it.

These run a whole draft through the real draft_policy() path with the model
stubbed to return exactly that kind of invented output, and assert that what
comes out introduces no date, timeframe or role title the user did not supply
and the sources do not state -- and that each one became a placeholder listed
as a decision.

Run: python -m pytest tests/test_draft_grounding.py -v
"""

import json
from types import SimpleNamespace

import pytest

import app.services.draft_policy_service as draft_service
from app.services.draft_facts import find_unsupplied_facts, ground_draft_facts
from app.services.retrieval.models import (
    Jurisdiction, RetrievalContext, RetrievalResult, SourceCategory, SourceChunk,
    SourceMetadata, SourceStatus, SourceType,
)

DESCRIPTION = "Patient complaints policy for a Medicare-certified home health agency."
REFERENCE = (
    "42 CFR § 484.50(e)(1) The HHA must (i) investigate complaints made by a patient, the patient's "
    "representative, and the patient's caregivers and family; (ii) document both the existence of the "
    "complaint and the resolution of the complaint; and (iii) take action to prevent further potential "
    "violations, including retaliation, while the complaint is being investigated."
)

# What the model produced in the audit, in miniature.
INVENTED_DRAFT = {
    "policy_title": "Patient Complaint and Grievance Policy",
    "effective_date": "March 1, 2026",
    "version": "1.0",
    "regulations_applied": ["42 CFR §484.50"],
    "sections": [
        {"title": "V. Procedures", "content": (
            "1. The Director of Clinical Services logs each complaint within 2 business days.\n"
            "2. The Quality Improvement Coordinator reviews complaint trends every 30 days.\n"
            "3. Written responses are sent within 14 days."
        )},
        {"title": "VI. Roles and Responsibilities", "content": (
            "Director of Clinical Services: owns the complaint log. "
            "Quality Improvement Coordinator: reports trends to the board."
        )},
        {"title": "X. Review and Revision Schedule", "content": "Reviewed by June 30, 2027 and annually thereafter."},
    ],
    "decisions_required": [],
    "drafting_notes": "Prepared for the Chief Compliance Officer.",
}


def _context():
    meta = SourceMetadata(
        source_name="42 CFR Part 484 — Patient rights",
        source_type=SourceType.retrieved_source,
        category=SourceCategory.federal_regulation,
        jurisdiction=Jurisdiction.federal,
        citation="42 CFR § 484.50",
        source_status=SourceStatus.current_verified,
        collection="federal_regulation",
    )
    chunk = SourceChunk(id="c1", text=REFERENCE, metadata=meta)
    return RetrievalContext(
        query="complaints",
        retrieved_chunks=[RetrievalResult(chunk=chunk, score=0.9, query="q")],
        formatted_context=REFERENCE,
        total_sources_found=1,
    )


class _Provider:
    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    async def complete(self, system_prompt, user_message, **kwargs):
        self.prompts.append((system_prompt, user_message))
        return json.dumps(self.payload)


class _LiveResearch:
    async def augment_retrieval_context(self, context, **kwargs):
        return context


class _Verifier:
    def verify_section(self, **kwargs):
        return SimpleNamespace(
            total_claims=0, verified_claims=0, partially_verified_claims=0,
            unverified_claims=0, contradicted_claims=0,
        )


@pytest.fixture
def run_draft(monkeypatch):
    def _run(payload, description=DESCRIPTION, jurisdiction=None):
        provider = _Provider(payload)
        monkeypatch.setattr(draft_service, "get_provider", lambda: provider)
        monkeypatch.setattr(
            draft_service, "get_retriever",
            lambda: SimpleNamespace(retrieve_for_step=lambda **k: _context()),
        )
        monkeypatch.setattr(draft_service, "get_live_research_service", lambda: _LiveResearch())
        monkeypatch.setattr(draft_service, "get_verification_service", lambda: _Verifier())
        import asyncio
        result = asyncio.run(draft_service.draft_policy(description, "home_health", jurisdiction))
        return result, provider
    return _run


def test_a_draft_run_introduces_no_unsupplied_dates_or_roles(run_draft):
    result, _ = run_draft(json.loads(json.dumps(INVENTED_DRAFT)))

    everything = "\n".join([
        result["full_text"], result.get("drafting_notes") or "", result.get("effective_date") or "",
        *result.get("decisions_required", []),
    ])
    assert find_unsupplied_facts(everything, DESCRIPTION, REFERENCE) == []

    for invented in ("Director of Clinical Services", "Quality Improvement Coordinator",
                     "Chief Compliance Officer", "March 1, 2026", "June 30, 2027",
                     "2 business days", "30 days", "14 days"):
        assert invented not in everything, invented


def test_inventions_become_placeholders_listed_as_decisions(run_draft):
    result, _ = run_draft(json.loads(json.dumps(INVENTED_DRAFT)))

    assert result["effective_date"] == "[EFFECTIVE DATE]"
    text = result["full_text"]
    assert "[ACCOUNTABLE ROLE 1]" in text and "[ACCOUNTABLE ROLE 2]" in text
    assert "[TIMEFRAME]" in text

    decisions = "\n".join(result["decisions_required"])
    for placeholder in ("[EFFECTIVE DATE]", "[ACCOUNTABLE ROLE 1]", "[ACCOUNTABLE ROLE 2]", "[TIMEFRAME]", "[DATE]"):
        assert placeholder in decisions, placeholder


def test_the_same_invented_role_maps_to_the_same_placeholder(run_draft):
    """Two sections naming one role must still read as one role."""
    result, _ = run_draft(json.loads(json.dumps(INVENTED_DRAFT)))
    procedures, roles = result["sections"][0]["content"], result["sections"][1]["content"]
    assert "[ACCOUNTABLE ROLE 1] logs each complaint" in procedures
    assert roles.startswith("[ACCOUNTABLE ROLE 1]: owns the complaint log")


def test_supplied_facts_are_kept():
    """A date, role or deadline the user gave, or the regulation states, stays."""
    description = (
        "Complaints policy effective January 5, 2027. The Clinical Manager handles complaints "
        "and responds within 5 business days."
    )
    data = {
        "effective_date": "January 5, 2027",
        "sections": [{"title": "V", "content": (
            "The Clinical Manager responds within 5 business days. "
            "Records are kept per the regulation's 60 calendar days rule."
        )}],
    }
    out = ground_draft_facts(data, description, "notify within 60 calendar days")
    assert out["effective_date"] == "January 5, 2027"
    assert "Clinical Manager" in out["full_text"]
    assert "5 business days" in out["full_text"]
    assert "60 calendar days" in out["full_text"]
    assert out["decisions_required"] == []


def test_the_prompt_asks_for_placeholders_not_inventions(run_draft):
    _, provider = run_draft(json.loads(json.dumps(INVENTED_DRAFT)))
    system_prompt, user_message = provider.prompts[0]
    assert "[ACCOUNTABLE ROLE 1]" in system_prompt
    assert "[EFFECTIVE DATE]" in system_prompt
    assert "decisions_required" in system_prompt
    assert "decide on a reasonable specific value" not in system_prompt
    assert "a date of your choosing" not in system_prompt
    assert "ready to sign and adopt" not in user_message.lower()
