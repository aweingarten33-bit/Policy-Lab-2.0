"""
Claim-support classification.

The last check in the verification chain: given a claim, its citation, and the
exact authoritative excerpt, does the excerpt actually support the claim?

Design constraints that matter:

  * The excerpt is the source of truth. The model is only classifying the
    relationship between two pieces of text it is shown — it is never asked
    what the regulation says, so it cannot answer from memory.
  * It sees no policy text, no findings, no prior context. Just the triple.
  * The output is one of four labels. There is no free-form path by which a
    claim can talk its way into being "verified".
  * All claims go in ONE batched call. Per-finding calls would multiply cost
    and latency by the number of findings for no accuracy gain.

If the call fails, every claim stays NOT_CHECKED and keeps its
partially-verified status. Failure must never silently upgrade anything.
"""

import json
import logging
import re
from typing import Dict, List

from app.config import settings
from app.models.schemas import ClaimSupport
from app.services.provider import get_provider

logger = logging.getLogger(__name__)

_VALID = {c.value for c in ClaimSupport}

SYSTEM_PROMPT = """You classify whether a quoted regulatory excerpt supports a claim.

You are given numbered items. Each has:
  CLAIM — an assertion made about what a regulation requires
  CITATION — the authority the claim cites
  EXCERPT — the actual text of that authority

For each item, decide how the EXCERPT relates to the CLAIM. Judge ONLY from the
EXCERPT shown. Do not use outside knowledge of the regulation, and do not assume
the claim is true because it sounds plausible or because the citation looks real.

Labels:
  SUPPORTED — the excerpt states or directly entails the claim.
  PARTIALLY_SUPPORTED — the excerpt is on topic and consistent with the claim,
      but does not establish all of it (e.g. it requires the policy exist but
      says nothing about the specific deadline or role the claim asserts).
  NOT_SUPPORTED — the excerpt does not address what the claim asserts.
  CONTRADICTED — the excerpt states something incompatible with the claim
      (e.g. the claim says ten years, the excerpt says six).

Be strict. If the excerpt does not actually say what the claim says, it is not
SUPPORTED. Defaulting to SUPPORTED when unsure defeats the purpose of this check.

Return ONLY a JSON array, no prose, no markdown fences:
[{"id": "<item id>", "label": "<one of the four labels>", "note": "<one short sentence>"}]
"""


def _build_user_prompt(items: List[Dict[str, str]]) -> str:
    blocks = []
    for item in items:
        blocks.append(
            f"ITEM {item['id']}\n"
            f"CLAIM: {item['claim']}\n"
            f"CITATION: {item['citation']}\n"
            f"EXCERPT: {item['excerpt']}\n"
        )
    return (
        "Classify each item.\n\n"
        + "\n---\n".join(blocks)
        + f"\n\nReturn a JSON array with exactly {len(items)} objects."
    )


def _parse_response(raw: str) -> Dict[str, Dict[str, str]]:
    """Parse the model's JSON array into {id: {label, note}}."""
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    match = re.search(r"\[[\s\S]*\]", cleaned)
    if not match:
        raise ValueError("No JSON array in claim-support response")

    out: Dict[str, Dict[str, str]] = {}
    for entry in json.loads(match.group(0)):
        item_id = str(entry.get("id", "")).strip()
        label = str(entry.get("label", "")).strip().upper()
        if not item_id:
            continue
        if label not in _VALID:
            # An unrecognized label must not become a pass.
            logger.warning(f"Claim-support returned unknown label {label!r} for {item_id}")
            label = ClaimSupport.not_checked.value
        out[item_id] = {"label": label, "note": str(entry.get("note", "")).strip()}
    return out


async def classify_claim_support(items: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """Classify a batch of (claim, citation, excerpt) triples.

    Returns {item_id: {"label": ..., "note": ...}}. Items missing from the
    result — or all of them, if the call fails — simply stay unclassified,
    which leaves their evidence at partially-verified.
    """
    if not items:
        return {}

    try:
        provider = get_provider()
        raw = await provider.complete(
            system_prompt=SYSTEM_PROMPT,
            user_message=_build_user_prompt(items),
            max_tokens=min(400 * len(items) + 500, settings.llm_max_tokens),
            temperature=0.0,  # classification, not generation
        )
        results = _parse_response(raw)
        logger.info(f"Claim-support: classified {len(results)}/{len(items)} claims")
        return results
    except Exception as e:
        # Never upgrade anything on failure.
        logger.warning(f"Claim-support classification unavailable: {e}")
        return {}
