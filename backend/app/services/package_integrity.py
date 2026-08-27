"""Final integrity checks for analysis packages.

This is the last-mile fail-closed guard at the API boundary. Internal pipeline
bugs must not be able to publish a reassuring verification count or a "legally
required" label when the evidence record is missing, partial, contradicted, or
otherwise not fully verified.
"""

from app.models.schemas import (
    ComplianceActionPackage,
    ObligationType,
    PackageStatus,
    VerificationStatus,
)

# Prefixes stamped onto the prose of a finding whose legal claim was not
# verified.
#
# Reported from real output: every finding in a production run was correctly
# labelled UNVERIFIED REQUIREMENT, and every finding's text still read
# "the policy must...", named an exact deadline, and cited a section as though
# the requirement had been confirmed. A reader takes the sentence, not the
# badge -- and the sentence said the law requires this.
#
# So the words change too, not only the label. Deliberately by prefixing rather
# than by rewriting: a rewrite would need a model call, would be another place
# for a claim to be invented, and could mangle a quoted provision. A prefix is
# deterministic, reversible, and leaves the original text intact for a reader
# who wants to judge it.
UNVERIFIED_FINDING_PREFIX = (
    "[NOT VERIFIED — the cited source does not establish this as a legal requirement] "
)
UNVERIFIED_LANGUAGE_PREFIX = (
    "[NOT VERIFIED AS LAW — adopt this only as your organization's own standard "
    "unless you confirm the requirement in the regulation yourself] "
)
GUIDANCE_FINDING_PREFIX = (
    "[AGENCY GUIDANCE, NOT LAW — this reflects what a regulator expects, not a "
    "legal obligation] "
)
GUIDANCE_LANGUAGE_PREFIX = (
    "[BASED ON GUIDANCE, NOT LAW — sound practice, but not a legal requirement] "
)

_ALL_PREFIXES = (
    UNVERIFIED_FINDING_PREFIX,
    UNVERIFIED_LANGUAGE_PREFIX,
    GUIDANCE_FINDING_PREFIX,
    GUIDANCE_LANGUAGE_PREFIX,
)


def _stamp(text, prefix: str) -> str:
    """Prefix `text`, unless it already carries one of these markers.

    Idempotent because reconciliation runs on every response the API emits and
    a finding must not accumulate a stack of identical warnings.
    """
    body = (text or "").strip()
    if not body:
        return body
    if body.startswith(_ALL_PREFIXES):
        return body
    return prefix + body


def reconcile_package_verification(
    package: ComplianceActionPackage,
) -> ComplianceActionPackage:
    """Reconcile package metadata and mandatory labels with finding evidence.

    Interim streaming snapshots are intentionally left alone. Final responses
    fail closed: a finding may remain ``required`` only when its evidence status
    is fully verified.
    """
    if package.status is not PackageStatus.complete:
        return package

    gap_analysis = package.gap_analysis
    rows = list(getattr(gap_analysis, "gap_table", None) or [])
    if not rows:
        return package

    missing_evidence = 0
    not_fully_verified = 0
    downgraded_required = 0

    for row in rows:
        evidence = getattr(row, "evidence", None)
        verified = evidence is not None and evidence.status is VerificationStatus.verified
        checks = getattr(evidence, "checks", None) if evidence is not None else None
        specifics_supported = getattr(checks, "specifics_supported", None)

        if evidence is None:
            missing_evidence += 1
            not_fully_verified += 1
        elif not verified:
            not_fully_verified += 1
            if specifics_supported is False and not getattr(row, "verification_warning", None):
                row.verification_warning = getattr(evidence, "reason", "") or (
                    "A concrete fact in this finding was not confirmed at the cited authority."
                )

        # Older/internal test doubles may not carry obligation_type. In real
        # GapRow objects it is always present; only touch legal labels when the
        # field actually exists.
        if getattr(row, "obligation_type", None) is ObligationType.required and not verified:
            row.obligation_type = ObligationType.unverified_requirement
            if evidence is None:
                reason = (
                    "Verification did not complete for this finding, so Policy Lab cannot "
                    "present it as a confirmed legal requirement."
                )
            elif evidence.status is VerificationStatus.contradicted:
                reason = (
                    "The cited source contradicts this claimed requirement. It cannot be "
                    "presented as legally required without independent confirmation."
                )
            elif specifics_supported is False:
                reason = (
                    "A concrete figure, threshold, deadline, percentage, amount, age, ratio, "
                    "or distance in this requirement was not confirmed at the cited source scope."
                )
            elif evidence.status is VerificationStatus.cannot_determine:
                reason = (
                    "The matching source is a proposal, an older version, archived material, "
                    "or of unestablished standing, so it cannot show what the law requires "
                    "today. Check the current text of the provision before treating this as "
                    "a legal requirement."
                )
            elif evidence.status is VerificationStatus.partially_verified:
                reason = (
                    "The authority was only partially verified or the cited passage did not "
                    "fully establish the claimed duty. Policy Lab therefore cannot label it "
                    "as a confirmed legal requirement."
                )
            else:
                reason = (
                    "The cited authority did not fully verify this claimed duty. Treat it as "
                    "an unverified requirement until independently confirmed."
                )
            row.obligation_note = reason
            downgraded_required += 1

    # The prose has to match the label. A finding carrying a badge that says
    # unverified and a sentence that says "must, per 45 CFR §X, within 60 days"
    # is read as a confirmed legal requirement, because that is what the
    # sentence says.
    for row in rows:
        obligation = getattr(row, "obligation_type", None)
        if obligation is ObligationType.unverified_requirement:
            row.finding = _stamp(row.finding, UNVERIFIED_FINDING_PREFIX)
            row.suggested_language = _stamp(row.suggested_language, UNVERIFIED_LANGUAGE_PREFIX)
        elif obligation is ObligationType.guidance:
            row.finding = _stamp(row.finding, GUIDANCE_FINDING_PREFIX)
            row.suggested_language = _stamp(row.suggested_language, GUIDANCE_LANGUAGE_PREFIX)

    package.unverified_claim_count = not_fully_verified

    if missing_evidence:
        package.verification_overall = (
            f"Verification incomplete: {missing_evidence} finding(s) did not receive an "
            "evidence record. Treat those findings as unverified and independently "
            "confirm them before relying on the analysis."
        )
    elif not_fully_verified:
        package.verification_overall = (
            f"{not_fully_verified} finding(s) were not fully verified against the exact "
            "cited source material and require independent review."
        )
    else:
        package.verification_overall = (
            f"All {len(rows)} finding(s) completed the evidence verification pass. "
            "Findings should still be independently confirmed before implementation."
        )

    if downgraded_required:
        package.verification_overall += (
            f" {downgraded_required} claimed legal requirement(s) were downgraded because "
            "full verification was not established."
        )

    # The executive summary is the part most likely to be read aloud to a board
    # and least likely to be read alongside the badges. It is model prose about
    # the findings, so when the findings turn out not to be established law it
    # describes something that did not happen. The correction is appended rather
    # than the summary rewritten: the observations may still be sound, and a
    # rewrite would need a model call.
    unverified_rows = sum(
        1 for r in rows
        if getattr(r, "obligation_type", None) in (
            ObligationType.unverified_requirement, ObligationType.guidance
        )
    )
    if unverified_rows:
        summary = (getattr(gap_analysis, "audit_ready_summary", "") or "").strip()
        correction = (
            f"IMPORTANT: {unverified_rows} of {len(rows)} finding(s) in this report could "
            f"not be confirmed as legal requirements against the cited source. Wherever "
            f"this summary describes something as required, treat it as unverified and "
            f"check the regulation directly before relying on it."
        )
        if correction not in summary:
            gap_analysis.audit_ready_summary = (summary + "\n\n" + correction).strip()

    return package
