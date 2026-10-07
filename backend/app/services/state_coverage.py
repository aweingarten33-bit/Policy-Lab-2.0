"""
What state-law material an output was actually built on.

Selecting a state used to make the interface say that state's law had been
"checked" and "verified". The knowledge base holds no codified state text, and
live research only runs when the corpus falls short -- so a New York run could
consult nothing from New York at all and still be presented as covering it.

This reports the truth from the retrieval context: which state sources were in
front of the model, where they came from, and a plain statement when there
were none. It is derived, never asserted by the model.
"""

from __future__ import annotations

from typing import List, Optional

from app.models.schemas import StateCoverage, StateSourceConsulted
from app.services.retrieval.models import RetrievalContext, SourceCategory, SourceType


def _is_state_source(result, state: str) -> bool:
    meta = result.chunk.metadata
    if meta.jurisdiction.value.upper() == state:
        return True
    # Live state-government search results are tagged federal by the search
    # layer; their category is what marks them as state material.
    return meta.category == SourceCategory.state_law


def state_coverage(ctx: Optional[RetrievalContext], jurisdiction: Optional[str]) -> Optional[StateCoverage]:
    """State coverage for a run, or None when no state was selected."""
    if not jurisdiction:
        return None
    state = str(jurisdiction).strip().upper()[:2]

    consulted: List[StateSourceConsulted] = []
    seen = set()
    for result in (ctx.get_all_sources() if ctx else []):
        if not _is_state_source(result, state):
            continue
        meta = result.chunk.metadata
        key = (meta.url or meta.source_name or "").strip().lower()
        if key in seen:
            continue
        seen.add(key)
        consulted.append(StateSourceConsulted(
            name=meta.source_name,
            url=meta.url,
            kind="live_search" if meta.source_type == SourceType.live_research else "knowledge_base",
        ))

    has_statute_text = any(s.kind == "knowledge_base" for s in consulted)
    live_only = bool(consulted) and not has_statute_text

    if not consulted:
        summary = (
            f"No {state} state law text was available for this run. Nothing here reflects {state} "
            f"requirements; only federal sources were consulted. Check {state} law separately."
        )
    elif live_only:
        summary = (
            f"{len(consulted)} {state} government web page(s) from a live search were consulted. "
            f"These are search results, not the codified {state} statute or regulation text, and "
            f"they have not been verified. Check {state} law separately before relying on it."
        )
    else:
        summary = (
            f"{len(consulted)} {state} source(s) were consulted. Coverage of {state} law is limited to "
            f"these sources; requirements not in them were not checked."
        )

    return StateCoverage(
        jurisdiction=state,
        state_text_available=has_statute_text,
        sources_consulted=consulted,
        summary=summary,
    )


__all__ = ["state_coverage"]
