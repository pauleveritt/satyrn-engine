"""Default-tier tests for the engine-owned whole-attempt budget.

No model, no network, no subprocess: the clock is controlled by passing
``seconds_used`` straight into :func:`evaluate`, and the counter reads plain
lines. Every refusal has a sibling success (binding rule 4).
"""

import pytest

from satyrn_engine.budget import (
    Budget,
    BudgetState,
    BudgetUsage,
    TurnCounter,
    evaluate,
)


def test_turn_counter_counts_only_turn_start_events() -> None:
    counter = TurnCounter()
    counter.feed('{"type":"session"}')
    counter.feed('{"type":"turn_start"}')
    counter.feed('{"type":"message_start"}')
    counter.feed('{"type":"turn_start"}')
    assert counter.turns == 2


def test_turn_counter_ignores_prose_and_unparseable_lines() -> None:
    """The sibling: a line must be a JSON object whose ``type`` is
    ``turn_start``. Prose that mentions a turn, or a non-JSON line, is not a
    turn -- otherwise a model could under-report by writing the words."""
    counter = TurnCounter()
    counter.feed("the turn starts now")
    counter.feed('{"adapter_marker":"turn_start","index":0}')
    counter.feed("")
    counter.feed('{"type":"turn_end"}')
    assert counter.turns == 0


def test_turn_counter_accepts_a_final_unterminated_line() -> None:
    """A turn-start line split across the last chunk still counts once the
    stream reaches EOF without a trailing newline."""
    counter = TurnCounter()
    counter.feed('{"type":"turn_start"}')
    assert counter.turns == 1


@pytest.mark.parametrize("limit", [3, 5])
def test_a_turn_limit_trips_on_the_limit_plus_one(limit: int) -> None:
    """A limit of N is WITHIN at N and TURN_EXHAUSTED at N + 1."""
    assert evaluate(Budget(turn_limit=limit), limit, 0.0).state is BudgetState.WITHIN
    assert (
        evaluate(Budget(turn_limit=limit), limit + 1, 0.0).state
        is BudgetState.TURN_EXHAUSTED
    )


def test_a_deadline_trips_only_after_it_is_passed() -> None:
    """The test controls the monotonic clock by passing seconds_used."""
    assert (
        evaluate(Budget(deadline_seconds=2.0), 0, 2.0).state is BudgetState.WITHIN
    )
    assert (
        evaluate(Budget(deadline_seconds=2.0), 0, 2.001).state
        is BudgetState.DEADLINE_EXHAUSTED
    )


def test_an_undeclared_budget_is_not_declared_not_ambiguous_none() -> None:
    """No declared limit is its own state, never a ``None`` standing in for
    "not reached"."""
    usage = evaluate(Budget(), 0, 0.0)
    assert usage.state is BudgetState.NOT_DECLARED
    assert not Budget().declared


def test_a_declared_budget_reports_within_before_any_spend() -> None:
    """The sibling: a declared budget starts WITHIN, and a turn-only or
    deadline-only declaration is still a declaration."""
    assert Budget(turn_limit=3).declared
    assert Budget(deadline_seconds=1.0).declared
    assert evaluate(Budget(turn_limit=3), 0, 0.0) == BudgetUsage(
        BudgetState.WITHIN, 0, 0.0
    )


def test_turn_exhaustion_wins_over_deadline_when_both_are_declared() -> None:
    """The evaluation checks the turn limit first, so a stream that crosses
    both limits on the same observation is named TURN_EXHAUSTED."""
    usage = evaluate(Budget(turn_limit=2, deadline_seconds=0.5), 3, 1.0)
    assert usage.state is BudgetState.TURN_EXHAUSTED
