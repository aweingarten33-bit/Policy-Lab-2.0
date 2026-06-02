"""
Retrieval Models — Data structures for source-grounded compliance intelligence.

Every chunk of source material carries rich metadata so the system can:
  - Retrieve only the most relevant material before each generation step
  - Attribute every claim to its source (model knowledge, retrieved source, or live research)
  - Verify claims against actual source text after generation
  - Clearly distinguish verified from unverified content in UI and exports
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from enum import Enum
from datetime import datetime


# ── Enums ──

class SourceType(str, Enum):
    """Origin of a claim or piece of information."""
    model_knowledge = "model_knowledge"        # LLM training data — NOT verified
    retrieved_source = "retrieved_source"      # From curated internal knowledge base
    live_research = "live_research"            # From controlled web research
    verified_source = "verified_source"        # Cross-checked against source material


class VerificationStatus(str, Enum):
    """Verification result for a claim."""
    verified = "verified"                      # Confirmed against source material
    partially_verified = "partially_verified"  # Some support found, not exact match
    unverified = "unverified"                  # No supporting source found
    contradicted = "contradicted"              # Source material contradicts the claim


class SourceCategory(str, Enum):
    """Category of source material for collection management."""
    federal_regulation = "federal_regulation"       # Federal regulatory text (45 CFR, 42 CFR, etc.)
    ocr_guidance = "ocr_guidance"                   # OCR guidance and enforcement actions
    state_law = "state_law"                         # State-specific regulations and statutes
    policy_clause_library = "policy_clause_library" # Approved policy clause templates
    policy_template = "policy_template"             # Complete policy templates
    example_policy = "example_policy"               # Example policies from peer organizations
    enforcement_action = "enforcement_action"       # OCR enforcement actions and resolutions
    requirement_pack = "requirement_pack"           # Bundled requirement sets for specific frameworks


class Jurisdiction(str, Enum):
    """Jurisdiction scope of source material."""
    federal = "federal"
    AL = "AL"; AK = "AK"; AZ = "AZ"; AR = "AR"; CA = "CA"; CO = "CO"
    CT = "CT"; DE = "DE"; FL = "FL"; GA = "GA"; HI = "HI"; ID = "ID"
    IL = "IL"; IN = "IN"; IA = "IA"; KS = "KS"; KY = "KY"; LA = "LA"
    ME = "ME"; MD = "MD"; MA = "MA"; MI = "MI"; MN = "MN"; MS = "MS"
    MO = "MO"; MT = "MT"; NE = "NE"; NV = "NV"; NH = "NH"; NJ = "NJ"
    NM = "NM"; NY = "NY"; NC = "NC"; ND = "ND"; OH = "OH"; OK = "OK"
    OR = "OR"; PA = "PA"; RI = "RI"; SC = "SC"; SD = "SD"; TN = "TN"
    TX = "TX"; UT = "UT"; VT = "VT"; VA = "VA"; WA = "WA"; WV = "WV"
    WI = "WI"; WY = "WY"; DC = "DC"


# ── Source Metadata ──

class SourceMetadata(BaseModel):
    """Rich metadata attached to every chunk of source material."""
    source_name: str = Field(..., description="Human-readable name of the source document")
    source_type: SourceType = Field(SourceType.retrieved_source, description="Where this came from")
    category: SourceCategory = Field(..., description="Source category for collection routing")
    jurisdiction: Jurisdiction = Field(Jurisdiction.federal, description="Federal or state-specific")
    effective_date: Optional[str] = Field(None, description="When this source became effective")
    citation: Optional[str] = Field(None, description="Formal citation (e.g., '45 CFR §164.530(b)')")
    url: Optional[str] = Field(None, description="Source URL if available")
    section: Optional[str] = Field(None, description="Section within the source document")
    authority: Optional[str] = Field(None, description="Issuing authority (e.g., 'HHS OCR', 'CMS')")
    is_current: bool = Field(True, description="Whether this is the current version")
    chunk_index: int = Field(0, description="Index of this chunk within the source document")
    total_chunks: int = Field(1, description="Total chunks from this source document")
    collection: str = Field(..., description="Which collection this chunk belongs to")


# ── Source Chunk ──

class SourceChunk(BaseModel):
    """A chunk of source material with its metadata, ready for embedding and retrieval."""
    id: str = Field(..., description="Unique chunk identifier")
    text: str = Field(..., description="The actual source text content")
    metadata: SourceMetadata = Field(..., description="Rich metadata about this source")


# ── Retrieval Result ──

class RetrievalResult(BaseModel):
    """A retrieved chunk with its relevance score."""
    chunk: SourceChunk = Field(..., description="The retrieved source chunk")
    score: float = Field(..., description="Relevance score (0-1, higher = more relevant)")
    query: str = Field(..., description="The query that retrieved this chunk")


# ── Source Attribution ──

class SourceAttribution(BaseModel):
    """
    Attribution for a single claim or finding.
    Every major output in the compliance action package carries one of these.
    """
    source_type: SourceType = Field(
        SourceType.model_knowledge,
        description="Where this information came from"
    )
    verification_status: VerificationStatus = Field(
        VerificationStatus.unverified,
        description="Whether this claim has been verified against source material"
    )
    source_name: Optional[str] = Field(
        None,
        description="Name of the source document (if retrieved/verified)"
    )
    source_citation: Optional[str] = Field(
        None,
        description="Formal citation (e.g., '45 CFR §164.530(b)')"
    )
    source_url: Optional[str] = Field(
        None,
        description="URL to the source material"
    )
    source_date: Optional[str] = Field(
        None,
        description="Date of the source material"
    )
    retrieved_text: Optional[str] = Field(
        None,
        description="The actual retrieved text that supports this claim (for verification)"
    )
    confidence: float = Field(
        0.5,
        description="Confidence in this attribution (0-1)"
    )
    warning: Optional[str] = Field(
        None,
        description="Warning message if the claim cannot be verified"
    )


# ── Retrieval Context ──

class RetrievalContext(BaseModel):
    """
    The complete retrieval context passed to a generation step.
    Contains all retrieved source material plus live research results.
    """
    query: str = Field(..., description="The retrieval query used")
    retrieved_chunks: List[RetrievalResult] = Field(
        default_factory=list,
        description="Chunks retrieved from the curated knowledge base"
    )
    live_research_results: List[RetrievalResult] = Field(
        default_factory=list,
        description="Results from controlled live research (if used)"
    )
    live_research_used: bool = Field(
        False,
        description="Whether live research was used for this step"
    )
    total_sources_found: int = Field(0, description="Total number of source chunks found")
    formatted_context: str = Field(
        "",
        description="Pre-formatted context string ready to inject into prompts"
    )

    def get_all_sources(self) -> List[RetrievalResult]:
        """Return all source results (curated + live)."""
        return self.retrieved_chunks + self.live_research_results

    def get_source_names(self) -> List[str]:
        """Return unique source names from all results."""
        names = []
        for r in self.get_all_sources():
            name = r.chunk.metadata.source_name
            if name not in names:
                names.append(name)
        return names

    def get_source_url_map(self) -> dict:
        """Return mapping of source_name -> URL for all results that have one."""
        url_map: dict = {}
        for r in self.get_all_sources():
            name = r.chunk.metadata.source_name
            url = r.chunk.metadata.url
            if name and url and name not in url_map:
                url_map[name] = url
        return url_map

    def get_source_citations(self) -> List[str]:
        """Return unique citations from all results."""
        citations = []
        for r in self.get_all_sources():
            cite = r.chunk.metadata.citation
            if cite and cite not in citations:
                citations.append(cite)
        return citations


# ── Verification Result ──

class ClaimVerification(BaseModel):
    """Verification result for a single claim made in the output."""
    claim_text: str = Field(..., description="The claim being verified")
    claimed_citation: Optional[str] = Field(None, description="Citation the claim references")
    verification_status: VerificationStatus = Field(
        VerificationStatus.unverified,
        description="Whether the claim is verified"
    )
    supporting_evidence: Optional[str] = Field(
        None,
        description="The source text that supports (or contradicts) this claim"
    )
    evidence_source: Optional[str] = Field(
        None,
        description="Name of the source where evidence was found"
    )
    warning: Optional[str] = Field(None, description="Warning for unverified claims")


class VerificationReport(BaseModel):
    """Complete verification report for an output section."""
    section_name: str = Field(..., description="Which output section was verified")
    total_claims: int = Field(0, description="Total claims checked")
    verified_claims: int = Field(0, description="Claims with supporting evidence")
    partially_verified_claims: int = Field(0, description="Claims with partial support")
    unverified_claims: int = Field(0, description="Claims with no supporting evidence")
    contradicted_claims: int = Field(0, description="Claims contradicted by source material")
    claim_details: List[ClaimVerification] = Field(default_factory=list)
    overall_status: VerificationStatus = Field(
        VerificationStatus.unverified,
        description="Overall verification status for this section"
    )


# ── Knowledge Base Stats ──

class CollectionStats(BaseModel):
    """Statistics about a source collection."""
    name: str
    category: SourceCategory
    chunk_count: int
    source_count: int
    jurisdictions: List[str]
    last_updated: Optional[str] = None


class KnowledgeBaseStats(BaseModel):
    """Overall knowledge base statistics."""
    total_chunks: int
    total_sources: int
    total_collections: int
    collections: List[CollectionStats]
    embedding_model: str
