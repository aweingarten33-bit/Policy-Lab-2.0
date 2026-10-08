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

# Prefixes stamped onto the prose of a finding backed only by agency guidance.
# They classify the source (guidance is not law); they are not a verification
# verdict.
#
# A finding whose regulatory requirement could not be confirmed used to be
# stamped "[NOT VERIFIED — ...]" in its own text, and the executive summary
# gained a correction paragraph. The report now carries that verdict on the
# finding: its badge ("Citation not confirmed, review before use"), which opens
# the cited passage beside the claim, plus the short prefix below on a red
# finding's own text. Nothing is global. The obligation type is still
# downgraded, so nothing unconfirmed is labelled "required by law".
GUIDANCE_FINDING_PREFIX = (
    "[AGENCY GUIDANCE, NOT LAW — this reflects what a regulator expects, not a "
    "legal obligation] "
)
GUIDANCE_LANGUAGE_PREFIX = (
    "[BASED ON GUIDANCE, NOT LAW — sound practice, but not a legal requirement] "
)

# The one exception: a finding whose badge is red also carries the badge's own
# words at the start of its text and its suggested language. A reader acts on
# the sentence they copy, and suggested language is pasted into real policies.
# Same wording as the badge, so it reads as one verdict, not a second one.
NOT_CONFIRMED_PREFIX = "[Citation not confirmed — review before use] "

_ALL_PREFIXES = (
    NOT_CONFIRMED_PREFIX,
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
                    "The cited regulation was found, but the passage did not "
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

    # Guidance is not law: say so in the finding's own words. (An unconfirmed
    # requirement is shown by the finding's badge instead; see above.)
    from app.services.verification_badge import RED, verification_badge

    for row in rows:
        obligation = getattr(row, "obligation_type", None)
        badge = verification_badge(row)
        if badge and badge[0] == RED:
            row.finding = _stamp(row.finding, NOT_CONFIRMED_PREFIX)
            row.suggested_language = _stamp(row.suggested_language, NOT_CONFIRMED_PREFIX)
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
        from app.services.limitations import verification_breakdown
        from app.services.retrieval.cfr_citation import is_uncited

        cited = [r for r in rows if not is_uncited(getattr(r, "citation", "") or "")]
        uncited = len(rows) - len(cited)
        package.verification_overall = (
            (f"Verification: {verification_breakdown(cited)}" if cited else "")
            + (f" {uncited} finding(s) cite no regulation and are organizational recommendations." if uncited else "")
        ).strip()
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

    return package
