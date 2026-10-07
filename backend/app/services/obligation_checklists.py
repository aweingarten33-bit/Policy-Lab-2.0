"""
Obligation-level checklists for policy topics where a narrow reading is a
known failure.

A live audit found a home-health complaints draft that limited investigation
to "formal grievances". 42 CFR 484.50(e)(1) is broader: the agency must
investigate complaints (by the patient, the representative, caregivers and
family), document both the complaint and its resolution, and act to prevent
further potential violations -- including retaliation -- while the complaint
is being investigated. A draft can cite §484.50 and still miss all three, so
checking that a citation exists proves nothing. These checklists name the
obligations themselves.

They are used three ways:
  * injected into the draft and analysis prompts for a matching topic, so the
    model is told the obligations rather than left to recall them;
  * checked against a finished draft, so a missing obligation is surfaced to
    the reader instead of shipped silently;
  * asserted in acceptance tests.

The obligation wording below paraphrases the regulation. It is a scope
reminder, not a substitute for the regulation text: the prompt still requires
every citation and every number to come from the retrieved source material.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Pattern, Tuple


@dataclass(frozen=True)
class Obligation:
    key: str
    citation: str
    requirement: str
    # Every group must match somewhere in the text for the obligation to count
    # as addressed. Groups are alternatives of equivalent wording.
    evidence: Tuple[Pattern, ...]

    def is_addressed(self, text: str) -> bool:
        return all(p.search(text or "") for p in self.evidence)


def _rx(pattern: str) -> Pattern:
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


HOME_HEALTH_COMPLAINTS: Tuple[Obligation, ...] = (
    Obligation(
        key="investigate_complaints",
        citation="42 CFR §484.50(e)(1)(i)",
        requirement=(
            "Investigate complaints made by a patient, the patient's representative, or the patient's "
            "caregivers and family -- not only formal or written grievances -- including complaints about "
            "care furnished or not furnished, failure to respect patient rights, and mistreatment, neglect, "
            "abuse, injuries of unknown source, or misappropriation of patient property."
        ),
        evidence=(
            _rx(r"\binvestigat\w*"),
            _rx(r"\bcomplain\w*"),
            # Who may complain: more than the patient alone.
            _rx(r"\b(representative|caregiver|family)\b"),
        ),
    ),
    Obligation(
        key="document_complaint_and_resolution",
        citation="42 CFR §484.50(e)(1)(ii)",
        requirement="Document both the existence of the complaint and its resolution.",
        evidence=(
            _rx(r"\bdocument\w*|\brecord\w*|\blog\w*"),
            _rx(r"\bresolution\b|\bresolved?\b|\boutcome\b"),
        ),
    ),
    Obligation(
        key="prevent_further_violations",
        citation="42 CFR §484.50(e)(1)(iii)",
        requirement=(
            "Take action to prevent further potential violations, including retaliation, while the "
            "complaint is being investigated."
        ),
        evidence=(
            _rx(r"\bprevent\w*|\bprotect\w*|\binterim\b|\bsafeguard\w*"),
            _rx(r"\bwhile\b.{0,80}\binvestigat\w*|\bduring\b.{0,80}\binvestigat\w*|\bpending\b.{0,80}\binvestigat\w*"),
            _rx(r"\bretaliat\w*"),
        ),
    ),
)

_COMPLAINT_TOPIC = _rx(r"\bcomplain\w*|\bgrievanc\w*|\bpatient\s+rights\b|\b484\.50\b")


def checklist_for(industry: Optional[str], topic_text: str) -> Tuple[Obligation, ...]:
    """The obligations that apply to this industry and topic, if any."""
    if (industry or "") == "home_health" and _COMPLAINT_TOPIC.search(topic_text or ""):
        return HOME_HEALTH_COMPLAINTS
    return ()


def missing_obligations(text: str, obligations: Tuple[Obligation, ...]) -> List[Obligation]:
    """The obligations ``text`` does not appear to address (keyword check, not a legal judgment)."""
    return [o for o in obligations if not o.is_addressed(text)]


def prompt_block(obligations: Tuple[Obligation, ...]) -> str:
    """Instructions naming each obligation, for a draft or analysis prompt."""
    if not obligations:
        return ""
    lines = "\n".join(f"  - {o.citation}: {o.requirement}" for o in obligations)
    return (
        "OBLIGATIONS THIS TOPIC MUST COVER\n"
        "The regulation imposes each of these duties. Do not narrow them -- in particular, do not limit "
        "investigation to 'formal grievances' or written complaints. Confirm each against the retrieved "
        "reference material before citing it:\n" + lines
    )


__all__ = [
    "HOME_HEALTH_COMPLAINTS", "Obligation", "checklist_for", "missing_obligations", "prompt_block",
]
