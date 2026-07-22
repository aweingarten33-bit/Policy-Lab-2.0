"""
LLM Service — Calls LLM for policy gap analysis.
Industry-aware: routes to the correct regulatory persona and framework
based on the selected industry (healthcare, home_health, other).
API key is read from environment variables ONLY. Never hard-coded.
No policy text is stored — stateless processing.
"""

import json
import re
import logging
from typing import Optional

from app.config import settings
from app.services.provider import get_provider
from app.models.schemas import AnalysisResult, GapRow, GapStatus
from app.services.retrieval.models import RetrievalContext
from app.services.industry_config import get_industry

logger = logging.getLogger(__name__)

# ── JSON response schema (shared across all industries) ──

ANALYTICAL_PROTOCOL = """
═══════════════════════════════════════════════════════════════════════════════
STEP 0 — IS THIS ACTUALLY A POLICY? (check before anything else)
═══════════════════════════════════════════════════════════════════════════════

The submitted text must be a genuine attempt at a policy, procedure, or
organizational document. If it clearly isn't — random prose, a story, lyrics,
code, spam, an off-topic question, or anything that was never meant to be a
policy — do NOT run the analysis below or invent findings to fill the schema.
Instead return: gap_table as an empty array, priority_findings as an empty
array, and audit_ready_summary stating plainly that the submitted text does
not appear to be a policy document and no analysis could be performed. Do not
soften this into a normal-looking report — a fabricated gap analysis of
non-policy text is a worse failure than an empty one. Ambiguous or informal
documents (a short internal memo, a rough draft, bullet-point notes) are still
real policy attempts and should be analyzed normally; this check is for
content that was never a policy in the first place.

═══════════════════════════════════════════════════════════════════════════════
ANALYTICAL PROTOCOL — execute internally before drafting any finding
═══════════════════════════════════════════════════════════════════════════════

You are not writing a generic compliance review. You are simulating the precise
mental model of a regulator standing in the organization's lobby with a subpoena.
Before you emit a single finding, walk this protocol in your head:

STEP 1 — REGULATORY CARTOGRAPHY
  Map every regulation that touches this policy area to a specific obligation.
  Federal → State → Local → Industry standard → Internal governance.
  For each, identify: the exact statutory section, the operational duty it
  imposes (what must the org DO, not what must it say), and the documentation
  artifact a regulator would demand to prove compliance.

STEP 2 — FOUR-AXIS POLICY EVALUATION
  For each regulatory obligation, score the policy on four independent axes —
  a policy can SATISFY one axis while FAILING three:
    (a) PRESENCE      — Is the topic addressed at all?
    (b) SPECIFICITY   — Are the operational details concrete (timeframes,
                        owners, thresholds, definitions) or vague?
    (c) OPERABILITY   — Could a new employee execute this on day one without
                        asking a supervisor? Is there a procedure, not just a
                        principle?
    (d) ACCOUNTABILITY — Is a named role assigned, with authority commensurate
                        with the responsibility, and is there an evidence
                        trail (logs, sign-offs, attestations)?
  A finding is "partial" when 1–2 axes pass; "gap" when 0–1 pass; "missing"
  when the topic is absent entirely; "compliant" only when all four pass.

STEP 3 — AUDIT-DAY SIMULATION
  For each finding, answer in your head: "If an auditor opened this policy
  Monday at 9am, what is the first follow-up document they would demand,
  and would it exist? What is the first interview question they would ask,
  and what would the answer reveal?" If the answer exposes the org, the
  finding is real. If you cannot articulate the audit-day exposure, the
  finding is not yet sharp enough — refine it.

STEP 4 — COUNTERFACTUAL LIABILITY TEST
  For each gap, ask: "If a violation occurred TOMORROW under this policy
  as written, what would the org's defense be in front of a regulator
  or in litigation? Would it survive?" Findings that fail this test
  belong in priority_findings.

STEP 5 — CITATION DISCIPLINE
  Every citation is fully specified: title + part + section + subsection
  where applicable, plus the year of the version cited. Generic refs like
  "HIPAA" or "Title IX" are unacceptable. Cite the exact provision being
  applied (e.g., "45 CFR §164.308(a)(1)(ii)(D) — Information System Activity
  Review (HIPAA Security Rule, current through 2026 NPRM)").

STEP 6 — REMEDIATION CONCRETENESS
  Every suggested_language entry must be DROP-IN POLICY TEXT — fully drafted
  sentences with named roles, defined timeframes, measurable thresholds, and
  evidence requirements. Not "consider implementing X" — the actual X.

═══════════════════════════════════════════════════════════════════════════════
BANNED PHRASING — these phrases reveal weak analysis, never use them
═══════════════════════════════════════════════════════════════════════════════
  ✗ "consider implementing"        ✗ "may want to"
  ✗ "appears to"                   ✗ "could benefit from"
  ✗ "it is recommended that"       ✗ "best practices suggest"
  ✗ "should consider"              ✗ "as appropriate"
  ✗ "where applicable"             ✗ "in some cases"
  ✗ "robust", "comprehensive"      ✗ "world-class", "industry-leading"

Use direct verbs: "requires", "must", "designates", "documents within
[X] hours", "the [Role] shall".

═══════════════════════════════════════════════════════════════════════════════
CALIBRATION — what a weak finding looks like vs an audit-grade finding
═══════════════════════════════════════════════════════════════════════════════

WEAK (REJECT):
  finding: "The breach notification section is vague and could be improved."
  suggested_language: "The organization should implement a robust breach
  notification process consistent with HIPAA requirements."
  citation: "HIPAA Breach Notification Rule"

AUDIT-GRADE (TARGET):
  finding: "Section 4.2 references 'timely notification' without defining the
  trigger event, the 60-day clock, the notification recipients, or the content
  required by 45 CFR §164.404(c). A workforce member encountering a suspected
  breach has no procedure to follow. On audit day, OCR would request the most
  recent notification log and the breach risk-assessment template — the
  current policy mandates neither."
  suggested_language: "Within 24 hours of any workforce member identifying a
  suspected impermissible use or disclosure of PHI, the workforce member shall
  notify the Privacy Officer in writing using Form BR-1. The Privacy Officer
  shall convene a Breach Risk Assessment Team within 72 hours and conduct a
  four-factor assessment (45 CFR §164.402) documented on Form BR-2. If
  determined to be a reportable breach, individual notice shall be issued
  no later than 60 calendar days from discovery (45 CFR §164.404(b)),
  containing all elements required by 45 CFR §164.404(c)(1). Breaches
  affecting 500+ individuals additionally trigger HHS Secretary notification
  via the OCR breach portal and prominent media notice within the same 60-day
  window (45 CFR §§164.408(b), 164.406)."
  citation: "45 CFR §§164.402–164.414 (HIPAA Breach Notification Rule,
  current through 2024); HHS OCR breach reporting guidance (2024)."

═══════════════════════════════════════════════════════════════════════════════
"""

RESPONSE_SCHEMA = """
═══════════════════════════════════════════════════════════════════════════════
JSON OUTPUT CONTRACT
═══════════════════════════════════════════════════════════════════════════════

Return ONLY valid JSON — no markdown fences, no preamble, nothing outside the
JSON object. Every field requirement below is enforced; shallow output is a
failed analysis.

{
  "policy_type": "Specific policy type — not a category. e.g., 'HIPAA Breach Notification & Risk-Assessment Policy' not just 'Privacy Policy'.",

  "scope": "2–3 sentences naming what was examined (the policy artifact and its stated coverage), the regulatory frameworks evaluated against it (federal, state, industry standard), and any explicit limits of this analysis (e.g., 'Did not evaluate operational implementation, only the written policy text').",

  "regulations_applied": ["Every regulation/statute/guidance evaluated — fully cited with title + part + year (e.g., '45 CFR Part 164 Subpart D — HIPAA Breach Notification (current through 2024)'). 6-10 entries."],

  "last_updated_note": "Identify any 2024–2026 regulatory developments materially affecting this policy area: new rule, enforcement trend, settlement pattern, guidance update, or NPRM. Cite the source. Omit only if genuinely no recent activity applies.",

  "priority_findings": [
    "One sentence, hard cap: the gap, its citation, and the exposure, packed into one sentence. Example: 'Policy lacks the four-factor breach risk assessment required by 45 CFR §164.402(2), so every undocumented incident becomes a presumptive violation on OCR audit.' Up to 4 entries, ordered by enforcement risk — fewer is fine.",
    "..."
  ],

  "gap_table": [
    // UP TO 10 objects in this array -- a ceiling, not a target. Select the
    // highest-risk distinct obligations, most material first. FEWER than 10
    // is correct and expected when the topic genuinely doesn't carry that
    // many distinct regulatory obligations (e.g. an internal attendance/
    // lateness policy has real but narrow regulatory surface -- FLSA pay-
    // docking rules, ADA accommodation if lateness relates to a disability
    // -- and padding by inventing tenuous regulatory hooks or relabeling
    // organizational-design choices as regulatory requirements is worse than
    // returning 2-3 genuine findings. Never pad to hit the ceiling.
    {
      "clause": "Specific policy section, topic, or operational obligation. NOT a regulation name. e.g., 'Workforce Sanctions for HIPAA Violations' or 'Annual Risk Analysis Documentation' — not 'HIPAA Security Rule'.",

      "regulations": [
        "Fully-specified citations only: title + part + section + subsection where applicable + year. Multiple citations when multiple authorities apply (e.g., a state law layered on top of federal). Minimum one, often 2–4. If this is genuinely an organizational-design matter with no specific regulatory mandate (see REGULATORY VS. ORGANIZATIONAL FINDINGS below), write exactly: 'No specific regulatory citation applies — organizational best practice.'"
      ],

      "status": "compliant | partial | gap | missing — apply the four-axis test from the protocol. For organizational-only findings with no regulatory citation, use 'gap' only if it's a real operational risk, not merely a style preference.",

      "risk_level": "critical | high | moderate | low | compliant — the regulatory consequence of the gap, not your subjective sense of importance. Organizational-only findings (no regulatory citation) cannot be 'critical' — cap at 'moderate', since there is no regulator enforcing them.",

      "current_state": "1–2 sentences, hard cap: direct quote OR close paraphrase of the EXACT policy language on this topic. If the policy is silent, write: 'Policy is silent — no provision addresses [specific obligation].' This field proves you read the actual document; it is a citation record, not analysis.",

      "finding": "2–3 sentences, hard cap. Name which axes pass/fail and the single sharpest deficiency. If regulatory: state the audit-day exposure — what document a regulator would demand and whether it would exist. If organizational-only: say so explicitly and state the operational risk instead of inventing regulatory exposure. Do not restate current_state, do not hedge, do not pad.",

      "suggested_language": "DROP-IN POLICY TEXT, 2–3 sentences, hard cap. MUST include: named role/title, specific timeframe, measurable threshold or trigger, and inline regulatory citation IF one genuinely applies — otherwise omit the citation rather than fabricate one. NEVER write 'the organization should consider.' This is clause text, not a sub-procedure — deeper reasoning belongs in finding, not here.",

      "citation": "Full statutory/regulatory authority for the obligation: title + part + section + subsection + year (and source publication where guidance, e.g., 'HHS OCR FAQ on Right of Access, 2023'). Multiple citations joined with semicolons when needed. Generic refs are rejected. If organizational-only, write exactly: 'Organizational best practice — no regulatory citation applies.' Do not fabricate a citation to avoid writing this.",

      "remediation_priority": "Immediate | 30-day | 90-day | Next-review — based on enforcement risk and operational feasibility.",

      "oig_element": "Healthcare & Home Health ONLY — the OIG GCPG element this finding maps to, formatted exactly as: '3 — Training & Education'. Use the canonical 7-element list. Omit for non-healthcare industries."
    }
  ],

  "audit_ready_summary": "4 sentences, hard cap: overall posture, severity distribution, the single highest-exposure gap, and the standing recommendation for independent legal review. Written for a compliance officer to read verbatim to their board. Flowing prose, no bullet points."
}

═══════════════════════════════════════════════════════════════════════════════
REGULATORY VS. ORGANIZATIONAL FINDINGS
═══════════════════════════════════════════════════════════════════════════════

Not every policy topic is regulation-driven. Some policy areas (e.g. an
internal lateness/attendance policy, a dress code, an internal communications
style guide) are primarily organizational-design choices with only narrow,
specific regulatory touchpoints (e.g. FLSA rules on docking exempt-employee
pay, ADA accommodation if lateness relates to a disability) rather than a
comprehensive regulatory framework the way HIPAA governs a privacy policy.

When you encounter this: identify the genuine regulatory touchpoints (there
are usually a few, even for "unregulated-feeling" topics — find them, don't
skip the analysis), mark any remaining findings explicitly as organizational
best practice (never fabricate a citation to make a design preference look
like a legal requirement), and return fewer than 6 rows rather than padding.
A user relying on this tool to know what's actually legally required is
actively harmed by a fabricated citation dressed up as regulatory law. Being
honest that "this area has limited regulatory framework — these findings are
professional best-practice recommendations, not legal requirements" is a
correct, complete analysis, not a shallow one.

═══════════════════════════════════════════════════════════════════════════════
RISK / PRIORITY / ELEMENT MAPPING
═══════════════════════════════════════════════════════════════════════════════

Status → Risk Level → Remediation Priority (default mapping; override with
specific reasoning when enforcement context warrants):
  missing    → critical → Immediate
  gap        → high     → 30-day
  partial    → moderate → 90-day
  compliant  → compliant → N/A

OIG GCPG 7 Elements (Healthcare & Home Health industries only — exact format for oig_element field):
  1 — Written Policies & Procedures
  2 — Compliance Leadership & Oversight
  3 — Training & Education
  4 — Effective Lines of Communication & Disclosure
  5 — Enforcing Standards: Consequences & Incentives
  6 — Risk Assessment, Auditing & Monitoring
  7 — Responding to Detected Offenses & Corrective Action

═══════════════════════════════════════════════════════════════════════════════
SCALE & DEPTH REQUIREMENTS
═══════════════════════════════════════════════════════════════════════════════

gap_table: UP TO 10 rows — a ceiling, not a target. Select the highest-risk
distinct obligations across the whole policy, most material first, and stop
at 10 even if more apply. Return fewer than 10 when the topic genuinely doesn't
carry that many distinct findings (see REGULATORY VS. ORGANIZATIONAL FINDINGS
above) — never pad to reach 10.

Every row MUST populate: clause, regulations (≥1), status, risk_level,
current_state, finding, suggested_language, citation, remediation_priority.
oig_element is required for Healthcare & Home Health industries and omitted otherwise.

priority_findings: UP TO 4 entries, one sentence each — fewer is fine if
there aren't 4 genuinely high-priority items.

audit_ready_summary: 4 sentences of board-ready prose.

═══════════════════════════════════════════════════════════════════════════════
HARD OUTPUT BUDGET — READ BEFORE WRITING
═══════════════════════════════════════════════════════════════════════════════

Every field above has a hard sentence cap, and gap_table has a fixed count
instead of a range, for the same reason: your entire response must fit inside
a strict token limit, and a shorter COMPLETE, valid JSON response is always
correct where a longer one that gets cut off mid-document is always a total
failure — none of it is usable if the JSON never closes, no matter how good
the content was up to that point.

Write every field at its hard cap, not up to it — a 2-sentence finding beats
a 3-sentence one if it says the same thing. These caps are maximums, not
targets to fill. A complete response using less of the budget always beats
a longer one that doesn't finish."""


def _build_system_prompt(industry_slug: Optional[str] = None, jurisdiction: Optional[str] = None) -> str:
    """Build the industry-aware system prompt, optionally with state-specific additions.

    Composition order matters: domain persona (industry expertise) → analytical
    protocol (universal reasoning scaffold) → JSON output contract (structural
    requirements) → state addendum (jurisdictional layer). Each layer constrains
    the next, so a model that wants to skip the analytical protocol cannot
    satisfy the output contract."""
    cfg = get_industry(industry_slug or "healthcare")

    prompt = cfg["persona"]
    prompt += "\n\n" + ANALYTICAL_PROTOCOL
    prompt += "\n\n" + RESPONSE_SCHEMA

    if jurisdiction:
        state_addendum = cfg.get("state_addendum", "")
        if state_addendum:
            prompt += "\n\n" + state_addendum.format(jurisdiction=jurisdiction)

    return prompt


def _build_user_prompt(
    text: str,
    industry_slug: Optional[str] = None,
    jurisdiction: Optional[str] = None,
    retrieval_context: Optional[RetrievalContext] = None,
) -> str:
    """Build the user message sent to the LLM, with optional retrieval context."""
    cfg = get_industry(industry_slug or "healthcare")
    audit_authority = cfg.get("audit_authority", "regulatory audit")

    base = (
        f"Analyze this {cfg['name']} compliance policy against every applicable US regulation:\n\n{text}"
    )

    if jurisdiction:
        base += f"\n\nJurisdiction specified: {jurisdiction}. Include all applicable {jurisdiction} state regulations."

    if retrieval_context and retrieval_context.formatted_context:
        base += f"\n\n{retrieval_context.formatted_context}"
    else:
        base += (
            "\n\n⚠️ No retrieved source material is available for this analysis. You MUST clearly mark any regulatory "
            "citations you provide as [MODEL INFERENCE — NOT VERIFIED FROM LOADED SOURCES] since they come from your "
            "training data, not from verified source documents."
        )

    base += (
        f"\n\nKey regulations to check for {cfg['name']} (this list is not exhaustive — identify all others that apply):\n"
        + "\n".join(f"  • {r}" for r in cfg.get("regulations", []))
    )

    base += (
        f"\n\nExecute the ANALYTICAL PROTOCOL on this policy. Walk the four-axis "
        f"evaluation for every regulatory obligation that touches this document. "
        f"For every finding, simulate audit day in front of a {audit_authority}: "
        f"what document does the regulator demand, what interview question do they "
        f"ask, what does the answer reveal. If you cannot articulate the audit-day "
        f"exposure, the finding is not sharp enough — refine it before emitting. "
        f"Treat hedge language as failure. Treat shallow citations as failure. "
        f"Return only the JSON contract, fully populated, depth proportional to "
        f"the regulatory complexity of the policy area."
    )

    return base


def _parse_llm_response(raw_text: str) -> AnalysisResult:
    """
    Robustly parse the LLM response into an AnalysisResult.
    Handles: markdown fences, preamble text, multiple JSON blocks.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_text)
    cleaned = re.sub(r"```\s*", "", cleaned)

    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        logger.error("No JSON object found in LLM response")
        raise ValueError("No JSON object found in model response")

    json_str = match.group(0)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}. Response length: {len(json_str)} chars. Tail: {json_str[-300:]!r}")
        raise ValueError(f"Invalid JSON in model response: {e}")

    gap_table = []
    for row_data in data.get("gap_table", []):
        status_str = row_data.get("status", "gap").lower()
        try:
            status = GapStatus(status_str)
        except ValueError:
            status = GapStatus.gap

        risk_level = row_data.get("risk_level")
        if not risk_level:
            risk_map = {
                "missing": "critical",
                "gap": "high",
                "partial": "moderate",
                "compliant": "compliant",
            }
            risk_level = risk_map.get(status_str, "moderate")

        remediation_priority = row_data.get("remediation_priority")
        if not remediation_priority:
            priority_map = {
                "missing": "Immediate",
                "gap": "30-day",
                "partial": "90-day",
                "compliant": "N/A",
            }
            remediation_priority = priority_map.get(status_str, "90-day")

        gap_table.append(GapRow(
            clause=row_data.get("clause", ""),
            regulations=row_data.get("regulations", []),
            status=status,
            risk_level=risk_level,
            current_state=row_data.get("current_state"),
            finding=row_data.get("finding", ""),
            suggested_language=row_data.get("suggested_language", ""),
            citation=row_data.get("citation", ""),
            remediation_priority=remediation_priority,
            oig_element=row_data.get("oig_element"),
        ))

    # ── Counts and score: always computed from the parsed rows, never trusted
    # from the model. These are mechanically derivable from gap_table, and
    # asking the model to also self-report them wasted tokens and risked the
    # exact contradiction the old prompt warned about ("a score above 80 with
    # critical findings is a contradiction the user will notice") -- a
    # contradiction that's now structurally impossible instead of just
    # discouraged. ──
    critical_count = sum(1 for r in gap_table if r.risk_level == "critical")
    gap_count = sum(1 for r in gap_table if r.risk_level == "high")
    partial_count = sum(1 for r in gap_table if r.risk_level == "moderate")
    compliant_count = sum(1 for r in gap_table if r.risk_level in ("compliant", "low"))

    if gap_table:
        total = len(gap_table)
        compliant_pts = sum(
            1.0 if r.status.value == "compliant" else (0.5 if r.status.value == "partial" else 0.0)
            for r in gap_table
        )
        compliance_score = round(compliant_pts / total * 100, 1)
    else:
        compliance_score = None

    return AnalysisResult(
        policy_type=data.get("policy_type", "Unknown"),
        scope=data.get("scope", "Analysis of uploaded policy against applicable regulations"),
        methodology="AI-assisted regulatory gap analysis applying a four-axis evaluation (presence, specificity, "
                     "operability, accountability) against applicable regulatory citations. Findings require "
                     "independent confirmation by qualified compliance counsel before any formal compliance "
                     "determination, board reporting, or regulatory submission.",
        regulations_applied=data.get("regulations_applied", []),
        last_updated_note=data.get("last_updated_note"),
        critical_count=critical_count,
        gap_count=gap_count,
        partial_count=partial_count,
        compliant_count=compliant_count,
        compliance_score=compliance_score,
        priority_findings=data.get("priority_findings", []),
        gap_table=gap_table,
        audit_ready_summary=data.get("audit_ready_summary", ""),
    )


_STATUS_PRIORITY = {"missing": 4, "gap": 3, "partial": 2, "compliant": 1}
_RISK_PRIORITY = {"critical": 4, "high": 3, "moderate": 2, "low": 1, "compliant": 0}


def _normalize_clause(clause: str) -> str:
    """Lowercase + strip punctuation for deduplication matching."""
    import re as _re
    return _re.sub(r"[^a-z0-9 ]", "", clause.lower()).strip()


def _merge_results(results: list[AnalysisResult]) -> AnalysisResult:
    """
    Merge multiple AnalysisResult objects into one comprehensive result.
    - Clauses found by multiple models are deduplicated; the worse status wins.
    - Clauses found by only one model are kept as-is.
    - Regulations and priority findings are unioned.
    - Compliance score takes the more conservative (lower) value.
    - Narrative fields (policy_type, scope, summary) come from the first (primary) result.
    """
    if len(results) == 1:
        return results[0]

    primary = results[0]

    # ── Merge gap tables ──
    seen: dict[str, GapRow] = {}  # normalized_clause → GapRow
    for result in results:
        for row in result.gap_table:
            key = _normalize_clause(row.clause)
            if key not in seen:
                seen[key] = row
            else:
                existing = seen[key]
                existing_pri = _STATUS_PRIORITY.get(existing.status.value, 0)
                new_pri = _STATUS_PRIORITY.get(row.status.value, 0)
                if new_pri > existing_pri:
                    # New model found a worse problem — use its status/finding/risk but
                    # keep the better suggested_language (longer = more detail)
                    merged = GapRow(
                        clause=existing.clause,
                        regulations=list(dict.fromkeys(existing.regulations + row.regulations)),
                        status=row.status,
                        risk_level=row.risk_level,
                        current_state=existing.current_state or row.current_state,
                        finding=row.finding,
                        suggested_language=(
                            row.suggested_language
                            if len(row.suggested_language or "") >= len(existing.suggested_language or "")
                            else existing.suggested_language
                        ),
                        citation=row.citation or existing.citation,
                        remediation_priority=row.remediation_priority,
                        oig_element=existing.oig_element or row.oig_element,
                    )
                    seen[key] = merged
                else:
                    # Existing is same or worse — just union the regulations
                    seen[key] = GapRow(
                        clause=existing.clause,
                        regulations=list(dict.fromkeys(existing.regulations + row.regulations)),
                        status=existing.status,
                        risk_level=existing.risk_level,
                        current_state=existing.current_state or row.current_state,
                        finding=existing.finding,
                        suggested_language=existing.suggested_language,
                        citation=existing.citation,
                        remediation_priority=existing.remediation_priority,
                        oig_element=existing.oig_element or row.oig_element,
                    )

    merged_table = list(seen.values())

    # Sort: critical → high → moderate → low → compliant
    merged_table.sort(key=lambda r: _RISK_PRIORITY.get(r.risk_level, 0), reverse=True)

    # ── Recount stats from merged table ──
    critical = sum(1 for r in merged_table if r.risk_level == "critical")
    gaps = sum(1 for r in merged_table if r.status.value == "gap")
    partials = sum(1 for r in merged_table if r.status.value == "partial")
    compliant = sum(1 for r in merged_table if r.status.value == "compliant")
    total = len(merged_table)
    score = round(
        (compliant * 1.0 + partials * 0.5) / total * 100, 1
    ) if total else 0.0

    # ── Union regulations and priority findings ──
    all_regs = []
    seen_regs: set[str] = set()
    for result in results:
        for reg in result.regulations_applied:
            key = reg.strip().lower()
            if key not in seen_regs:
                seen_regs.add(key)
                all_regs.append(reg)

    all_findings = []
    seen_findings: set[str] = set()
    for result in results:
        for f in result.priority_findings:
            key = f.strip().lower()[:60]
            if key not in seen_findings:
                seen_findings.add(key)
                all_findings.append(f)

    return AnalysisResult(
        policy_type=primary.policy_type,
        scope=primary.scope,
        methodology=primary.methodology,
        regulations_applied=all_regs,
        last_updated_note=primary.last_updated_note,
        critical_count=critical,
        gap_count=gaps,
        partial_count=partials,
        compliant_count=compliant,
        compliance_score=min(r.compliance_score for r in results if r.compliance_score is not None),
        priority_findings=all_findings[:8],
        gap_table=merged_table,
        audit_ready_summary=primary.audit_ready_summary,
    )


# ── Ensemble models: run these simultaneously for gap analysis ──
_ENSEMBLE_MODELS = ["gpt-4o-mini"]


async def analyze_policy(
    text: str,
    file_name: Optional[str] = None,
    industry: Optional[str] = None,
    jurisdiction: Optional[str] = None,
    retrieval_context: Optional[RetrievalContext] = None,
) -> AnalysisResult:
    """
    Send policy text to both Claude Haiku and Groq simultaneously.
    Both models analyze the same policy in parallel; their gap tables are merged
    so findings missed by one are caught by the other.
    Falls back to whichever model(s) succeed if one fails (e.g. Groq 'too large').
    """
    provider = get_provider()

    system_prompt = _build_system_prompt(industry, jurisdiction)
    user_message = _build_user_prompt(text, industry, jurisdiction, retrieval_context)

    logger.info(
        f"Ensemble analysis — industry: {industry or 'healthcare'}, "
        f"text length: {len(text)} chars, models: {_ENSEMBLE_MODELS}"
    )

    pairs = await provider.complete_ensemble(
        system_prompt=system_prompt,
        user_message=user_message,
        models=_ENSEMBLE_MODELS,
        max_tokens=settings.llm_max_tokens,
        temperature=0.3,
    )

    if not pairs:
        # All ensemble models failed — fall back to cascade
        logger.warning("Ensemble: all models failed, falling back to cascade")
        raw_text = await provider.complete(
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=settings.llm_max_tokens,
            temperature=0.3,
        )
        result = _parse_llm_response(raw_text)
        logger.info(f"Fallback analysis complete — {len(result.gap_table)} findings")
        return result

    parsed = []
    for model, raw_text in pairs:
        try:
            parsed.append(_parse_llm_response(raw_text))
            logger.info(f"Ensemble parsed {model}: {len(parsed[-1].gap_table)} findings")
        except Exception as e:
            logger.warning(f"Ensemble: failed to parse {model} response — {e}")

    if not parsed:
        raise ValueError("Ensemble: all model responses failed to parse")

    result = _merge_results(parsed)
    logger.info(
        f"Ensemble merged — {len(result.gap_table)} total findings "
        f"({result.critical_count} critical) from {len(parsed)} model(s)"
    )
    return result
