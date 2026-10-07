"""
Retrieval must reach the regulation each part of a policy is about.

Production, 2026-10-07: for the built-in hospital HIPAA sample, retrieval ran
one query (a template plus the first 500 characters) and kept the top three
regulation chunks of ~7,000. They were 42 CFR 482 and 42 CFR Part 2 -- not one
45 CFR Part 164 section reached the model, which then cited whole subparts from
memory and labelled its own citations unverified. Each section of the policy is
now also queried on its own text.

Uses a real local vector store (same embedding model as production) with
short stand-ins for the regulation text.

Run: python -m pytest tests/test_section_retrieval.py -v
"""

import re
from pathlib import Path

import pytest

from app.services.retrieval.ingestion import ingest_source_document
from app.services.retrieval.models import Jurisdiction, SourceCategory, SourceStatus, SourceType
from app.services.retrieval.retriever import ComplianceRetriever, _policy_sections

SAMPLE = re.search(
    r"healthcare: `(.*?)`,",
    (Path(__file__).resolve().parents[2] / "frontend/src/pages/Index.tsx").read_text(),
    re.S,
).group(1)

CORPUS = [
    ("45 CFR § 164.404", "45 CFR Part 164", "A covered entity shall, following the discovery of a breach of unsecured protected health information, notify each individual whose information has been accessed, acquired, used, or disclosed as a result of such breach, without unreasonable delay."),
    ("45 CFR § 164.312", "45 CFR Part 164", "Access control. Implement technical policies and procedures for electronic information systems that maintain electronic protected health information to allow access only to authorized persons. Unique user identification: assign a unique name or number for identifying and tracking user identity. Password management."),
    ("45 CFR § 164.308", "45 CFR Part 164", "Security awareness and training. Implement a security awareness and training program for all members of the workforce, including management. Business associate contracts and other arrangements."),
    ("45 CFR § 164.524", "45 CFR Part 164", "Access of individuals to protected health information. An individual has a right of access to inspect and obtain a copy of protected health information about the individual in a designated record set. The covered entity must act on a request for access no later than 30 days after receipt."),
    ("42 CFR § 482.13", "42 CFR Part 482", "Condition of participation: Patient's rights. A hospital must protect and promote each patient's rights, including the right to personal privacy and confidentiality of clinical records."),
    ("42 CFR § 2.13", "42 CFR Part 2", "Confidentiality restrictions and safeguards. Records of substance use disorder treatment are confidential and may be disclosed only as permitted."),
]


@pytest.fixture
def retriever(tmp_path, monkeypatch):
    from app.services.retrieval import store as store_module

    monkeypatch.setattr(store_module, "_store", store_module.ChromaStore(persist_dir=str(tmp_path / "kb")))
    # Filler so the right sections compete with many others, as in production.
    for i in range(40):
        ingest_source_document(
            source_name=f"42 CFR § 482.{20 + i}", text=f"Hospital condition of participation {i}: governing body, medical staff, nursing services, food and dietetic services, radiology services and pharmaceutical services requirements.",
            category=SourceCategory.federal_regulation, jurisdiction=Jurisdiction.federal,
            citation=f"42 CFR § 482.{20 + i}", part_citation="42 CFR Part 482",
            source_status=SourceStatus.current_verified, source_type=SourceType.retrieved_source,
        )
    for citation, part, text in CORPUS:
        ingest_source_document(
            source_name=citation, text=text, category=SourceCategory.federal_regulation,
            jurisdiction=Jurisdiction.federal, citation=citation, part_citation=part,
            source_status=SourceStatus.current_verified, source_type=SourceType.retrieved_source,
        )
    return ComplianceRetriever()


def test_the_sample_splits_into_its_sections():
    sections = _policy_sections(SAMPLE)
    assert any(s.startswith("6. BREACH RESPONSE") for s in sections)
    assert any(s.startswith("4. ACCESS CONTROLS") for s in sections)
    assert any(s.startswith("10. POLICY REVIEW") for s in sections)


def test_the_sample_reaches_the_hipaa_sections_it_is_about(retriever):
    ctx = retriever.retrieve_for_step(step_name="gap_analysis", policy_text=SAMPLE, industry="healthcare")
    cited = {r.chunk.metadata.citation for r in ctx.retrieved_chunks}
    for expected in ("45 CFR § 164.404", "45 CFR § 164.312", "45 CFR § 164.308", "45 CFR § 164.524"):
        assert expected in cited, (expected, sorted(cited))
    assert "45 CFR § 164.404" in ctx.formatted_context
