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

        if evidence is None:
            missing_evidence += 1
            not_fully_verified += 1
        elif not verified:
            not_fully_verified += 1
            if (
                getattr(evidence.checks, "specifics_supported", None) is False
                and not getattr(row, "verification_warning", None)
            ):
                row.verification_warning = evidence.reason or (
                    "A concrete fact in this finding was not confirmed at the cited authority."
                )

        if row.obligation_type is ObligationType.required and not verified:
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
            elif getattr(evidence.checks, "specifics_supported", None) is False:
                reason = (
                    "A concrete figure, threshold, deadline, percentage, amount, age, ratio, "
                    "or distance in this requirement was not confirmed at the cited source scope."
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

    return package
