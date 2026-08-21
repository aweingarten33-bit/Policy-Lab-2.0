"""Final integrity checks for analysis packages.

The orchestrator streams an early, intentionally unverified snapshot and then a
final snapshot after evidence verification. This module is a last-mile guard at
the API boundary: a final package must not report a reassuring verification
count when evidence is missing or not fully verified.

It does not replace the verification pipeline. It makes failures in that
pipeline visible instead of allowing an internally inconsistent package to be
served as though verification succeeded.
"""

from app.models.schemas import ComplianceActionPackage, PackageStatus, VerificationStatus


def reconcile_package_verification(
    package: ComplianceActionPackage,
) -> ComplianceActionPackage:
    """Reconcile package-level verification metadata with finding evidence.

    Only final packages are changed. Streaming interim snapshots intentionally
    arrive before evidence is attached and must remain untouched.
    """
    if package.status is not PackageStatus.complete:
        return package

    gap_analysis = package.gap_analysis
    rows = list(getattr(gap_analysis, "gap_table", None) or [])
    if not rows:
        return package

    missing_evidence = 0
    not_fully_verified = 0

    for row in rows:
        evidence = getattr(row, "evidence", None)
        if evidence is None:
            missing_evidence += 1
            not_fully_verified += 1
            continue
        if evidence.status is not VerificationStatus.verified:
            not_fully_verified += 1

    package.unverified_claim_count = not_fully_verified

    if missing_evidence:
        package.verification_overall = (
            f"Verification incomplete: {missing_evidence} finding(s) did not receive an "
            "evidence record. Treat those findings as unverified and independently "
            "confirm them before relying on the analysis."
        )
    elif not_fully_verified:
        package.verification_overall = (
            f"{not_fully_verified} finding(s) were not fully verified against the loaded "
            "source material and require independent review."
        )
    else:
        package.verification_overall = (
            f"All {len(rows)} finding(s) completed the evidence verification pass. "
            "Findings should still be independently confirmed before implementation."
        )

    return package
