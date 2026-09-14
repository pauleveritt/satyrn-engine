"""Default-tier tests for the budget accounting on delivery receipts.

No model, no network, no subprocess: validation is stubbed exactly as in
``test_delivery.py``, and the receipts are built through the same
``_context_receipt`` helper the product path uses.
"""

import io
import json
from pathlib import Path

import pytest

import satyrn_engine.delivery as delivery
from satyrn_engine.budget import Budget, BudgetState, BudgetUsage
from satyrn_engine.delivery import DeliveryCode, DeliveryOutcome, ValidationOutcome


def _context(tmp_path: Path, budget: Budget) -> delivery._DeliveryContext:
    return delivery._DeliveryContext(
        repository=str(tmp_path),
        root=tmp_path,
        environment={},
        contract_id="budget",
        base_commit="a" * 40,
        candidate_ref="refs/satyrn/candidates/budget/head",
        test_command=("pytest",),
        budget=budget,
    )


def _state(tmp_path: Path) -> delivery._AttemptState:
    return delivery._AttemptState(tmp_path, tmp_path / "worktree", parent_exists=False)


def _pending(context: delivery._DeliveryContext) -> delivery.DeliveryReceipt:
    return delivery._context_receipt(
        context,
        DeliveryCode.BUDGET_EXHAUSTED,
        "candidate created; whole-attempt turn limit exhausted after 4 turns",
        candidate_commit="c" * 40,
        changed_paths=("app.py",),
        budget=context.budget,
        budget_usage=BudgetUsage(BudgetState.TURN_EXHAUSTED, 4, 2.0),
    )


def test_an_undeclared_budget_payload_is_not_declared() -> None:
    receipt = delivery._receipt("/repo", DeliveryCode.REPO_DIRTY, "dirty")
    budget = receipt.payload()["budget"]
    assert budget == {
        "state": "not_declared",
        "turns_used": 0,
        "seconds_used": 0.0,
        "turn_limit": None,
        "deadline_seconds": None,
    }


def test_a_declared_budget_receipt_reports_within_and_the_declaration() -> None:
    receipt = delivery._receipt(
        "/repo",
        DeliveryCode.OK,
        "candidate created",
        budget=Budget(turn_limit=3, deadline_seconds=5.0),
        budget_usage=BudgetUsage(BudgetState.WITHIN, 2, 1.5),
    )
    assert receipt.payload()["budget"] == {
        "state": "within",
        "turns_used": 2,
        "seconds_used": 1.5,
        "turn_limit": 3,
        "deadline_seconds": 5.0,
    }


def test_a_declared_budget_with_no_run_is_not_enforced() -> None:
    """A declared budget with no explicit usage means the attempt never ran,
    so the state is NOT_ENFORCED -- not WITHIN, which would claim a spend of
    0/0 was measured."""
    receipt = delivery._receipt(
        "/repo", DeliveryCode.REPO_DIRTY, "dirty", budget=Budget(turn_limit=3)
    )
    assert receipt.budget_usage.state is BudgetState.NOT_ENFORCED
    assert receipt.payload()["budget"]["state"] == "not_enforced"
    assert receipt.payload()["budget"]["turn_limit"] == 3


def test_a_declared_budget_receipt_with_explicit_usage_stays_within() -> None:
    """The sibling: once the attempt ran and usage is given, the state is
    WITHIN -- NOT_ENFORCED is only for a budget whose attempt never ran."""
    receipt = delivery._receipt(
        "/repo",
        DeliveryCode.OK,
        "candidate created",
        budget=Budget(turn_limit=3),
        budget_usage=BudgetUsage(BudgetState.WITHIN, 1, 0.5),
    )
    assert receipt.budget_usage.state is BudgetState.WITHIN
    assert receipt.payload()["budget"]["state"] == "within"


def test_budget_exhausted_receipt_maps_to_candidate_created() -> None:
    context = _context(Path("/tmp"), Budget(turn_limit=3))
    pending = _pending(context)
    assert pending.outcome is DeliveryOutcome.CANDIDATE_CREATED
    assert pending.payload()["budget"]["state"] == "turn_exhausted"
    assert pending.payload()["budget"]["turns_used"] == 4
    assert pending.payload()["budget"]["turn_limit"] == 3


def _stub_validation_run(
    monkeypatch: pytest.MonkeyPatch, result: delivery._TestRunResult
) -> None:
    monkeypatch.setattr(delivery, "_checkout_candidate", lambda *args: None)
    monkeypatch.setattr(delivery, "_run_test_command", lambda *args, **kwargs: result)


def test_passing_validation_does_not_turn_exhausted_into_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V4 independence: a passing self-test on a partial candidate still does
    not complete the attempt -- BUDGET_EXHAUSTED stays the authoritative code."""
    context = _context(tmp_path, Budget(turn_limit=3))
    _stub_validation_run(
        monkeypatch, delivery._TestRunResult(returncode=0, output=b"2 passed\n")
    )

    receipt = delivery._validate_candidate(
        context, _state(tmp_path), _pending(context), 30.0
    )

    assert receipt.code is DeliveryCode.BUDGET_EXHAUSTED
    assert receipt.validation is ValidationOutcome.PASSED
    assert receipt.candidate_commit == "c" * 40


def test_failed_validation_does_not_overwrite_budget_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing self-test on a partial candidate records FAILED but keeps
    BUDGET_EXHAUSTED -- the budget state is not overwritten by V4's verdict."""
    context = _context(tmp_path, Budget(turn_limit=3))
    _stub_validation_run(
        monkeypatch, delivery._TestRunResult(returncode=1, output=b"1 failed\n")
    )

    receipt = delivery._validate_candidate(
        context, _state(tmp_path), _pending(context), 30.0
    )

    assert receipt.code is DeliveryCode.BUDGET_EXHAUSTED
    assert receipt.validation is ValidationOutcome.FAILED
    assert receipt.validation_exit == 1
    assert receipt.candidate_commit == "c" * 40


def _assistant_message_end(output: int) -> bytes:
    return (
        json.dumps(
            {
                "type": "message_end",
                "message": {"role": "assistant", "usage": {"input": 0, "output": output}},
            }
        )
        + "\n"
    ).encode("utf-8")


def test_token_exhaustion_keeps_the_candidate_and_says_so(tmp_path: Path) -> None:
    """The sibling of the turn-exhaustion receipt: a token trip also keeps
    the candidate and names the tokens spent. ``tokens_used`` is 600, not
    900, because the real stream tee (``_consume_chunk``) stops counting as
    soon as the second of three 300-token assistant lines crosses a 500
    token_budget -- the third line is never reached."""
    context = _context(tmp_path, Budget(token_limit=500))
    counter = delivery.TurnCounter()
    pending_bytes = b""
    exhausted = None
    for line in (
        _assistant_message_end(300),
        _assistant_message_end(300),
        _assistant_message_end(300),
    ):
        pending_bytes, exhausted = delivery._consume_chunk(
            line, io.BytesIO(), pending_bytes, counter, context.budget
        )
        if exhausted is not None:
            break
    assert exhausted is BudgetState.TOKEN_EXHAUSTED

    usage = BudgetUsage(exhausted, 0, 0.0, tokens_used=counter.tokens_out)
    receipt = delivery._context_receipt(
        context,
        DeliveryCode.BUDGET_EXHAUSTED,
        f"candidate created; whole-attempt {delivery._budget_exhaustion_detail(exhausted, usage)}",
        candidate_commit="c" * 40,
        changed_paths=("app.py",),
        budget=context.budget,
        budget_usage=usage,
    )

    assert receipt.code is DeliveryCode.BUDGET_EXHAUSTED
    assert receipt.budget_usage.state is BudgetState.TOKEN_EXHAUSTED
    assert receipt.budget.token_limit == 500 and receipt.budget_usage.tokens_used == 600
    assert receipt.message == "candidate created; whole-attempt token budget exhausted after 600 output tokens"
