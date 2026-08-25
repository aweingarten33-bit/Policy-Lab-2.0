"""There is one way to become `verified`, and one definition of each model.

Two invariants, both checked against the source tree rather than described in a
comment, because both are the kind of property a well-meaning future change
breaks without noticing.

1. apply_claim_support() is the only code that assigns VerificationStatus.verified.
   Any second path would be a way to reach a green badge without the citation,
   excerpt, concrete-fact, modality and standing checks.

2. SourceType, VerificationStatus and SourceAttribution are each declared once.
   They were previously declared twice, in app.models.schemas and in
   app.services.retrieval.models. Two enum classes with identical members are
   still two classes: `status == X` is True across them and `status is X` is
   always False, so an identity check silently evaluated False depending on
   which module the caller happened to import from.

Run: python -m pytest tests/evaluation/test_single_verification_path.py -v
"""

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _python_files():
    return sorted(APP.rglob("*.py"))


def _assignments_of_verified(tree, source):
    """Locations where VerificationStatus.verified is assigned to something."""
    hits = []

    def is_verified(node):
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "verified"
            and isinstance(node.value, ast.Name)
            and node.value.id == "VerificationStatus"
        )

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = [node.value]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.value]
        elif isinstance(node, ast.keyword):
            targets = [node.value]
        for value in targets:
            if is_verified(value):
                hits.append(node.lineno)
    return hits


def _enclosing_function(tree, lineno):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= lineno <= end:
                if best is None or node.lineno > best.lineno:
                    best = node
    return best.name if best else "<module>"


# apply_claim_support is the only place a CLAIM becomes verified.
#
# verify_section is allowed as the single exception because it does something
# different: it rolls per-claim results up into one section-level status, and it
# can only report verified when every underlying claim is already verified --
# each of which reached that state through apply_claim_support. The behavioural
# tests in TestTheRollupCannotBeGreenerThanItsParts hold it to that, so the
# exemption is not taken on trust.
_ALLOWED_VERIFIED_WRITERS = {"apply_claim_support", "verify_section"}


class TestOnlyOnePathAssignsVerified:
    def test_apply_claim_support_is_the_only_writer(self):
        offenders = []
        for path in _python_files():
            source = path.read_text()
            if "VerificationStatus.verified" not in source:
                continue
            tree = ast.parse(source)
            for lineno in _assignments_of_verified(tree, source):
                function = _enclosing_function(tree, lineno)
                if function in _ALLOWED_VERIFIED_WRITERS:
                    continue
                offenders.append(f"{path.relative_to(APP.parent)}:{lineno} in {function}()")

        assert not offenders, (
            "VerificationStatus.verified is assigned outside apply_claim_support(). "
            "Every such path bypasses the citation, excerpt, concrete-fact, "
            "modality and source-standing checks:\n  " + "\n  ".join(offenders)
        )

    def test_no_other_module_assigns_verified_at_all(self):
        """Narrower and blunter: the status may only be written inside the
        verification module. A new writer anywhere else fails here first."""
        offenders = []
        for path in _python_files():
            source = path.read_text()
            if "VerificationStatus.verified" not in source:
                continue
            if _assignments_of_verified(ast.parse(source), source):
                offenders.append(str(path.relative_to(APP.parent)))
        assert offenders == ["app/services/retrieval/verification.py"], offenders


class TestTheRollupCannotBeGreenerThanItsParts:
    """verify_section's exemption, held to its justification."""

    def _report(self, statuses):
        from app.models.schemas import VerificationStatus as VS
        from app.services.retrieval.models import ClaimVerification
        from app.services.retrieval.verification import VerificationService

        service = VerificationService()
        claims = [
            ClaimVerification(claim_text=f"c{i}", verification_status=s)
            for i, s in enumerate(statuses)
        ]
        service.verify_citations = lambda *a, **k: claims
        return service.verify_section("s", "text", None)

    def test_all_verified_rolls_up_to_verified(self):
        from app.models.schemas import VerificationStatus as VS
        assert self._report([VS.verified, VS.verified]).overall_status is VS.verified

    def test_one_unverified_claim_blocks_a_verified_section(self):
        from app.models.schemas import VerificationStatus as VS
        assert self._report([VS.verified, VS.unverified]).overall_status is not VS.verified

    def test_one_cannot_determine_claim_blocks_a_verified_section(self):
        from app.models.schemas import VerificationStatus as VS
        report = self._report([VS.verified, VS.cannot_determine])
        assert report.overall_status is not VS.verified
        assert report.cannot_determine_claims == 1

    def test_one_contradicted_claim_blocks_a_verified_section(self):
        from app.models.schemas import VerificationStatus as VS
        assert self._report([VS.verified, VS.contradicted]).overall_status is VS.contradicted

    def test_no_claims_at_all_is_not_verified(self):
        from app.models.schemas import VerificationStatus as VS
        assert self._report([]).overall_status is VS.unverified

    def test_the_verified_branch_names_every_precondition(self):
        """Read from the source so a removed condition fails here loudly."""
        source = (APP / "services" / "retrieval" / "verification.py").read_text()
        start = source.index("def apply_claim_support")
        end = source.index("def check_unsupported_specifics")
        body = source[start:end]

        verified_branch = body[body.index("VerificationStatus.verified") - 700:
                               body.index("VerificationStatus.verified")]
        for condition in (
            "ClaimSupport.supported",
            "citation_exists",
            "specifics_supported",
            "source.excerpt",
            "source_status_current",
        ):
            assert condition in verified_branch, (
                f"the verified branch no longer requires {condition}"
            )


class TestModelsAreDeclaredOnce:
    @pytest.mark.parametrize("name", [
        "SourceAttribution", "SourceType", "VerificationStatus", "SourceStatus",
    ])
    def test_exactly_one_class_definition(self, name):
        definitions = []
        for path in _python_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == name:
                    definitions.append(f"{path.relative_to(APP.parent)}:{node.lineno}")
        assert len(definitions) == 1, (
            f"{name} is declared {len(definitions)} times: {definitions}. Two "
            f"classes with identical members are still two classes, and `is` "
            f"comparisons between them are always False."
        )

    def test_the_retrieval_package_re_exports_rather_than_redefining(self):
        from app.models import schemas
        from app.services.retrieval import models

        for name in ("SourceAttribution", "SourceType", "VerificationStatus", "SourceStatus"):
            assert getattr(models, name) is getattr(schemas, name), (
                f"{name} differs between the two import paths"
            )

    def test_identity_comparison_holds_across_import_paths(self):
        """The bug the duplication caused, asserted directly."""
        from app.models.schemas import VerificationStatus as FromSchemas
        from app.services.retrieval.models import VerificationStatus as FromRetrieval

        status = FromSchemas.verified
        assert status is FromRetrieval.verified
        assert FromSchemas is FromRetrieval

    def test_a_source_attribution_built_either_way_is_one_type(self):
        from app.models.schemas import SourceAttribution as A
        from app.services.retrieval.models import SourceAttribution as B

        assert isinstance(A(), B)
        assert isinstance(B(), A)
