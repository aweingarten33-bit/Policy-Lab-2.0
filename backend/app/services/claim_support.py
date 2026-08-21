"""Strict claim-to-authority entailment classification.

The model used here is a constrained classifier, not a source of legal facts.
It sees only the claim, its citation, and the exact authoritative excerpt. The
excerpt is the source of truth. A failure or malformed response never upgrades
verification status.
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

SYSTEM_PROMPT = """You are a strict regulatory entailment classifier.

You are given numbered items. Each has:
  CLAIM — an assertion about what an authority requires, permits, recommends, prohibits, or establishes
  CITATION — the specific authority/subsection the claim cites
  EXCERPT — the actual source text selected from that cited authority

Judge ONLY from the EXCERPT shown. Do not use memory, outside legal knowledge,
common practice, or assumptions about what the regulation probably says. A real
citation is not proof that the claim is true.

Labels:
  SUPPORTED — the excerpt directly states or necessarily entails the whole claim.
  PARTIALLY_SUPPORTED — the excerpt supports only part of the claim, or the claim
      omits a material condition, exception, actor, scope limitation, or trigger.
  NOT_SUPPORTED — the excerpt is merely related/on-topic or does not establish the claim.
  CONTRADICTED — the excerpt states something incompatible with the claim.

Mandatory-versus-optional language is critical:
- A claim saying MUST / SHALL / REQUIRED / PROHIBITED is SUPPORTED only if the
  excerpt actually imposes that duty or prohibition.
- MAY / SHOULD / RECOMMENDS / ENCOURAGES / OPTIONAL language does NOT support a
  claim that something is legally required.
- Agency guidance or a best-practice recommendation does not become binding law
  merely because the claim uses words such as "requires" or "must."

Citation scope is critical:
- Judge the cited subsection, not a nearby paragraph from the same regulation.
- If the excerpt does not establish the proposition at the cited scope, do not
  infer support from the regulation's general topic.

Concrete facts are critical:
- Numbers, deadlines, retention periods, percentages, dollar amounts, ages,
  ratios, thresholds, frequencies, and distances must match the excerpt.
- If a claim says 30 days and the excerpt says 60 days, use CONTRADICTED.
- If the claim adds a number the excerpt does not contain, use PARTIALLY_SUPPORTED
  or NOT_SUPPORTED depending on whether the remainder of the claim is established.

Conditions and exceptions are critical:
- If the authority applies only when a condition is met and the claim states the
  rule unconditionally, the claim is not fully supported.
- If the excerpt gives discretion but the claim removes that discretion, the
  claim is not supported as a mandate.

Be conservative. When uncertain between SUPPORTED and another label, do not use
SUPPORTED. Verification is allowed to fail closed.

Return ONLY a JSON array, no prose or markdown:
[{"id":"<item id>","label":"SUPPORTED|PARTIALLY_SUPPORTED|NOT_SUPPORTED|CONTRADICTED","note":"<one concise reason tied to the excerpt>"}]
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
        "Classify every item independently using only its excerpt.\n\n"
        + "\n---\n".join(blocks)
        + f"\n\nReturn exactly {len(items)} JSON objects, one for each listed id."
    )


def _parse_response(raw: str) -> Dict[str, Dict[str, str]]:
    """Parse classifier JSON; unknown labels degrade to NOT_CHECKED."""
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    match = re.search(r"\[[\s\S]*\]", cleaned)
    if not match:
        raise ValueError("No JSON array in claim-support response")

    decoded = json.loads(match.group(0))
    if not isinstance(decoded, list):
        raise ValueError("Claim-support response was not a list")

    out: Dict[str, Dict[str, str]] = {}
    for entry in decoded:
        if not isinstance(entry, dict):
            continue
        item_id = str(entry.get("id", "")).strip()
        label = str(entry.get("label", "")).strip().upper()
        if not item_id:
            continue
        if label not in _VALID:
            logger.warning("Claim-support returned unknown label %r for %s", label, item_id)
            label = ClaimSupport.not_checked.value
        out[item_id] = {
            "label": label,
            "note": str(entry.get("note", "")).strip()[:500],
        }
    return out


async def classify_claim_support(items: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """Classify a batch of claim/citation/excerpt triples, failing closed."""
    if not items:
        return {}

    expected_ids = {str(item["id"]) for item in items}
    try:
        provider = get_provider()
        raw = await provider.complete(
            system_prompt=SYSTEM_PROMPT,
            user_message=_build_user_prompt(items),
            max_tokens=min(400 * len(items) + 500, settings.llm_max_tokens),
            temperature=0.0,
        )
        parsed = _parse_response(raw)
        # Ignore invented ids from the classifier. Missing ids simply remain
        # NOT_CHECKED in the caller and therefore cannot be verified.
        results = {item_id: value for item_id, value in parsed.items() if item_id in expected_ids}
        logger.info("Claim-support: classified %s/%s claims", len(results), len(items))
        return results
    except Exception as e:
        logger.warning("Claim-support classification unavailable: %s", e)
        return {}
