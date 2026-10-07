"""
Home-health complaint flows must carry the obligations of 42 CFR 484.50(e)(1),
not just cite the section.

From a live audit: a home-health complaints draft limited investigation to
"formal grievances". The regulation requires the agency to (i) investigate
complaints from the patient, representative, caregivers and family, (ii)
document the complaint and its resolution, and (iii) act to prevent further
violations, including retaliation, while investigating. A draft can cite
§484.50 and miss all three, so these tests assert the obligations, not the
citation.

The default tests run without a model. Set RUN_LIVE_LLM_TESTS=1 (with provider
keys configured) to also run the end-to-end acceptance tests against a real
model.

Run: python -m pytest tests/test_home_health_obligations.py -v
"""

import asyncio
import json
import os
from types import SimpleNamespace

import pytest

import app.services.draft_policy_service as draft_service
from app.services.industry_config import get_policy_types
from app.services.llm_service import _build_user_prompt
from app.services.obligation_checklists import (
    HOME_HEALTH_COMPLAINTS, checklist_for, missing_obligations,
)
from app.services.retrieval.models import RetrievalContext

GRIEVANCE_ONLY = (
    "Patients may submit a formal grievance in writing. The agency will review formal grievances "
    "and send a written response."
)
COMPLETE = (
    "The agency investigates every complaint made by a patient, the patient's representative, or the "
    "patient's caregivers and family, including complaints about care furnished or not furnished. Staff "
    "document each complaint and its resolution in the complaint log. While a complaint is being "
    "investigated, the agency takes interim action to protect the patient and prevent further potential "
    "violations, including retaliation against the person who complained."
)
OBLIGATION_KEYS = {o.key for o in HOME_HEALTH_COMPLAINTS}


class TestTheChecklist:
    def test_a_grievance_only_policy_misses_every_obligation(self):
        missing = {o.key for o in missing_obligations(GRIEVANCE_ONLY, HOME_HEALTH_COMPLAINTS)}
        assert missing == OBLIGATION_KEYS

    def test_a_policy_meeting_the_regulation_misses_none(self):
        assert missing_obligations(COMPLETE, HOME_HEALTH_COMPLAINTS) == []

    def test_investigating_patients_only_is_not_enough(self):
        """Complaints from representatives, caregivers and family must be investigated too."""
        patient_only = COMPLETE.replace(
            "a patient, the patient's representative, or the patient's caregivers and family", "a patient"
        )
        keys = {o.key for o in missing_obligations(patient_only, HOME_HEALTH_COMPLAINTS)}
        assert keys == {"investigate_complaints"}

    def test_protection_must_be_during_the_investigation(self):
        no_interim = COMPLETE.replace("While a complaint is being investigated, ", "Afterwards, ")
        keys = {o.key for o in missing_obligations(no_interim, HOME_HEALTH_COMPLAINTS)}
        assert "prevent_further_violations" in keys

    def test_applies_to_home_health_complaint_topics_only(self):
        assert checklist_for("home_health", "Patient rights policy per 42 CFR 484.50")
        assert checklist_for("home_health", "grievance and complaint handling")
        assert not checklist_for("home_health", "aide supervision policy")
        assert not checklist_for("healthcare", "complaints policy")


class TestTheFlowsAreToldTheObligations:
    def test_the_menu_no_longer_describes_a_grievance_process(self):
        patient_rights = next(p for p in get_policy_types("home_health") if p["slug"] == "patient_rights")
        assert "grievance" not in patient_rights["description"].lower()
        assert "complaint" in patient_rights["description"].lower()

    def test_the_analysis_prompt_names_each_obligation(self):
        prompt = _build_user_prompt(GRIEVANCE_ONLY + " Patient rights.", "home_health")
        for o in HOME_HEALTH_COMPLAINTS:
            assert o.citation in prompt
        assert "formal grievances" in prompt  # the explicit instruction not to narrow to them

    def test_the_draft_prompt_names_each_obligation(self, monkeypatch):
        captured = {}

        async def complete(system_prompt, user_message, **kwargs):
            captured["user"] = user_message
            return json.dumps({"policy_title": "P", "sections": [{"title": "I", "content": COMPLETE}]})

        _stub_draft_dependencies(monkeypatch, complete)
        asyncio.run(draft_service.draft_policy("Patient complaints policy", "home_health"))
        for o in HOME_HEALTH_COMPLAINTS:
            assert o.citation in captured["user"]


class TestTheDraftFlowSurfacesWhatIsMissing:
    def test_a_grievance_only_draft_is_flagged(self, monkeypatch):
        async def complete(system_prompt, user_message, **kwargs):
            return json.dumps({"policy_title": "P", "sections": [{"title": "V", "content": GRIEVANCE_ONLY}]})

        _stub_draft_dependencies(monkeypatch, complete)
        result = asyncio.run(draft_service.draft_policy("Patient complaints policy", "home_health"))
        flagged = "\n".join(result["missing_obligations"])
        for o in HOME_HEALTH_COMPLAINTS:
            assert o.citation in flagged

    def test_a_complete_draft_is_not_flagged(self, monkeypatch):
        async def complete(system_prompt, user_message, **kwargs):
            return json.dumps({"policy_title": "P", "sections": [{"title": "V", "content": COMPLETE}]})

        _stub_draft_dependencies(monkeypatch, complete)
        result = asyncio.run(draft_service.draft_policy("Patient complaints policy", "home_health"))
        assert result["missing_obligations"] == []


def _stub_draft_dependencies(monkeypatch, complete):
    monkeypatch.setattr(draft_service, "get_provider", lambda: SimpleNamespace(complete=complete))
    monkeypatch.setattr(
        draft_service, "get_retriever",
        lambda: SimpleNamespace(retrieve_for_step=lambda **k: RetrievalContext(query="q")),
    )

    class _Live:
        async def augment_retrieval_context(self, context, **kwargs):
            return context

    monkeypatch.setattr(draft_service, "get_live_research_service", lambda: _Live())


# ── End-to-end acceptance against a real model (opt-in) ──

live = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_LLM_TESTS") != "1",
    reason="set RUN_LIVE_LLM_TESTS=1 with provider keys to run against a real model",
)


@live
def test_live_home_health_complaints_draft_covers_every_obligation():
    result = asyncio.run(draft_service.draft_policy(
        "Patient complaints and grievance policy for a Medicare-certified home health agency.",
        "home_health",
    ))
    assert result["missing_obligations"] == [], result["missing_obligations"]


@live
def test_live_analysis_of_a_grievance_only_policy_raises_every_obligation():
    from app.services.llm_service import analyze_policy

    policy = "PATIENT RIGHTS POLICY\n1. COMPLAINTS\n" + GRIEVANCE_ONLY + "\n2. REVIEW\nReviewed annually."
    result = asyncio.run(analyze_policy(policy, industry="home_health"))
    findings = "\n".join(
        f"{r.finding}\n{r.suggested_language}\n{r.implementation_question or ''}" for r in result.gap_table
    )
    assert missing_obligations(findings, HOME_HEALTH_COMPLAINTS) == []
