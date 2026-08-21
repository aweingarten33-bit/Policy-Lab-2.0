"""
The verification pass must run on the path the product actually uses.

Every check in this system -- evidence records, claim support, the obligation
gate, unsupported-figure flagging -- lived only in generate_full_package().
Nothing calls that method. The app runs generate_full_package_stream(), which
did the retrieval and the analysis and then yielded, running none of them.

So the checks existed, had tests, passed those tests, and never executed. A
finding claiming a legal mandate its source did not establish reached users
looking exactly like one that did.

Testing a method in isolation proves the method works. It does not prove
anything calls it. These tests assert the wiring.

Run: python -m pytest tests/test_verification_actually_runs.py -v
"""

import inspect

import pytest

from app.services.orchestrator import PackageOrchestrator

STREAM = "generate_full_package_stream"


def _stream_source():
    return inspect.getsource(getattr(PackageOrchestrator, STREAM))


class TestTheUsedPathVerifies:
    @pytest.mark.parametrize("call", [
        "_build_evidence",
        "_flag_unsupported_specifics",
        "_verify_and_attribute",
    ])
    def test_the_streaming_path_runs_it(self, call):
        assert call in _stream_source(), (
            f"{call} is not called by {STREAM}, which is the only path the "
            f"application uses. A check nothing invokes protects nobody."
        )

    def test_the_obligation_gate_is_reachable_from_it(self):
        """The gate runs inside _build_evidence rather than being called
        directly, so the chain has to be checked end to end."""
        evidence = inspect.getsource(PackageOrchestrator._build_evidence)
        assert "_gate_unproven_mandates" in evidence
        assert "_build_evidence" in _stream_source()


class TestTheRouterUsesTheVerifiedPath:
    def test_the_job_runner_calls_the_streaming_orchestrator(self):
        """If the router ever switches paths, the checks have to come along."""
        import app.routers.action_package as ap
        runner = inspect.getsource(ap._run_action_package_job)
        assert STREAM in runner


class TestResultsArriveBeforeVerification:
    """Correctness is not negotiable; making someone wait for it before seeing
    anything is. Findings are yielded first, then verified, then yielded again
    with the evidence attached."""

    def test_there_is_a_yield_before_the_verification_pass(self):
        source = _stream_source()
        first_yield = source.index("yield package")
        verify_at = source.index("_build_evidence")
        assert first_yield < verify_at, (
            "verification runs before the first yield, so the reader waits on "
            "it with nothing on screen"
        )

    def test_the_package_is_yielded_again_afterwards(self):
        source = _stream_source()
        verify_at = source.index("_build_evidence")
        assert "yield package" in source[verify_at:], (
            "the verified package is never sent, so the evidence and obligation "
            "badges never reach the reader"
        )

    def test_a_failed_check_does_not_drop_findings(self):
        """A verification error must degrade to unverified, never to silence."""
        source = _stream_source()
        verify_at = source.index("_build_evidence")
        tail = source[verify_at:]
        assert "except Exception" in tail
        assert "return" not in tail.split("except Exception")[1][:400], (
            "a verification failure aborts the generator and the user loses "
            "findings that were already produced"
        )
