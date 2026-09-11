"""The TESTS_FAILED result code and its distinct exit.

The payload truth is ``validation == FAILED``; ``TESTS_FAILED`` only changes
the coarse status so no caller can read ``OK`` and infer passing tests. The
candidate is still created and retained.
"""

import pytest

import satyrn_engine.delivery as delivery
from satyrn_engine.delivery import (
    DeliveryCode,
    DeliveryOutcome,
    DeliveryReceipt,
    ValidationOutcome,
)
from satyrn_engine.exits import ExitCode

CANDIDATE_CODES = {DeliveryCode.OK, DeliveryCode.TESTS_FAILED}


def _receipt(code: DeliveryCode) -> DeliveryReceipt:
    produced = code in CANDIDATE_CODES
    return DeliveryReceipt(
        code=code,
        message="candidate created" if produced else "",
        contract_id="c",
        repository="/repo",
        base_commit="a" * 40,
        candidate_ref="refs/satyrn/candidates/c/head" if produced else None,
        candidate_commit="c" * 40 if produced else None,
        changed_paths=("app.py",) if produced else None,
        command_exit=0,
        worktree_path=None,
        validation=(
            ValidationOutcome.NOT_REQUESTED
            if produced
            else ValidationOutcome.NOT_APPLICABLE
        ),
    )


def test_tests_failed_is_the_next_unused_exit_integer() -> None:
    """The exit-code table is a stable contract; TESTS_FAILED takes 13, the
    first integer after TEST_COMMAND_NOT_ALLOWED (12)."""
    assert int(ExitCode.TESTS_FAILED) == 13
    assert [int(code) for code in ExitCode].count(13) == 1


def test_tests_failed_maps_to_candidate_created_and_a_distinct_exit() -> None:
    assert (
        delivery._CODE_TO_OUTCOME[DeliveryCode.TESTS_FAILED]
        is DeliveryOutcome.CANDIDATE_CREATED
    )
    assert delivery._CODE_TO_EXIT[DeliveryCode.TESTS_FAILED] is ExitCode.TESTS_FAILED


def test_tests_failed_is_not_refused_or_discarded() -> None:
    """The candidate is still created and retained; only the coarse status
    differs from a clean pass."""
    failed = _receipt(DeliveryCode.TESTS_FAILED)
    assert failed.outcome is DeliveryOutcome.CANDIDATE_CREATED
    assert failed.exit_code is ExitCode.TESTS_FAILED
    assert failed.candidate_commit == "c" * 40
    assert failed.changed_paths == ("app.py",)


def test_ok_does_not_imply_passing_tests() -> None:
    """The sibling success test: OK is the coarse status only; the validation
    field is the truth, so a caller that reads just OK cannot infer passing
    tests."""
    ok = _receipt(DeliveryCode.OK)
    assert ok.outcome is DeliveryOutcome.CANDIDATE_CREATED
    assert ok.exit_code is ExitCode.OK
    assert ok.validation is ValidationOutcome.NOT_REQUESTED


def test_command_exit_semantics_are_independent_of_validation() -> None:
    """A failed implementer process keeps COMMAND_FAILED even though validation
    was never run, and its own exit is preserved verbatim."""
    failed_command = DeliveryReceipt(
        code=DeliveryCode.COMMAND_FAILED,
        message="command exited with status 9",
        contract_id="c",
        repository="/repo",
        base_commit="a" * 40,
        candidate_ref=None,
        candidate_commit=None,
        changed_paths=None,
        command_exit=9,
        worktree_path=None,
    )
    assert failed_command.command_exit == 9
    assert failed_command.exit_code is ExitCode.NO_CANDIDATE
    assert failed_command.validation is ValidationOutcome.NOT_APPLICABLE


def test_receipt_rejects_an_unknown_code_through_the_public_constructor() -> None:
    """The closed vocabulary still refuses a typo, sibling of the mapped tests
    that all use real members."""
    with pytest.raises(TypeError, match="DeliveryCode"):
        DeliveryReceipt(
            code="TYPO",  # type: ignore[arg-type]
            message="",
            contract_id=None,
            repository="/repo",
            base_commit=None,
            candidate_ref=None,
            candidate_commit=None,
            changed_paths=None,
            command_exit=None,
            worktree_path=None,
        )
