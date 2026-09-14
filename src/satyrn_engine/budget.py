"""Engine-owned whole-attempt turn and wall-clock budget.

A budget is a declaration (:class:`Budget`) plus an accounting of what was
spent (:class:`BudgetUsage`). The counter reads the implementer's own stream,
never the model's prose: a turn is a ``type == "turn_start"`` line in Pi's
JSON-lines transcript, and nothing the model writes inside a message counts.
"""

import json
from dataclasses import dataclass
from enum import StrEnum

_TURN_START_TYPE = "turn_start"


class BudgetState(StrEnum):
    """Closed vocabulary for where an attempt stands against its budget.

    ``WITHIN``, ``TURN_EXHAUSTED``, and ``DEADLINE_EXHAUSTED`` are spend
    states :func:`evaluate` derives once an attempt actually ran.
    ``NOT_DECLARED`` means no limit was declared. ``NOT_ENFORCED`` means a
    limit was declared but the attempt never ran (a preflight refusal or an
    unavailable command), so there is no spend to measure and no honest
    ``WITHIN`` to report.
    """

    WITHIN = "within"
    TURN_EXHAUSTED = "turn_exhausted"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    NOT_DECLARED = "not_declared"
    NOT_ENFORCED = "not_enforced"


@dataclass(frozen=True, slots=True)
class Budget:
    """A declared budget.

    ``None`` means "no limit of this kind", and ``declared`` is the single
    place that turns two ``None`` values into one name. A declared-but-
    unenforced budget is therefore its own state (``NOT_ENFORCED``), distinct
    from ``WITHIN`` (a declared budget whose attempt did run) and never an
    ambiguous ``None``.
    """

    turn_limit: int | None = None
    deadline_seconds: float | None = None

    @property
    def declared(self) -> bool:
        return self.turn_limit is not None or self.deadline_seconds is not None


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """What was spent, and the state that follows from it."""

    state: BudgetState
    turns_used: int
    seconds_used: float


class TurnCounter:
    """Count ``turn_start`` events from the implementer's own stream."""

    def __init__(self) -> None:
        self._turns = 0

    def feed(self, line: str) -> None:
        """Count one transcript line if it is a turn-start event.

        The line is parsed as JSON and only a ``type == "turn_start"`` field
        counts. Prose that merely mentions the words "turn start" does not.
        """
        if _is_turn_start(line):
            self._turns += 1

    @property
    def turns(self) -> int:
        return self._turns


def evaluate(budget: Budget, turns_used: int, seconds_used: float) -> BudgetUsage:
    """Return the budget state for a measured spend.

    A turn limit trips when the count exceeds it, so a limit of 3 trips on
    the 4th ``turn_start``. A deadline trips when the elapsed monotonic
    seconds exceed it. With no declared budget the state is ``NOT_DECLARED``,
    never a ``None`` standing in for "no budget reached".

    ``evaluate`` never returns ``NOT_ENFORCED``: that state is assigned by
    the receipt layer for a declared budget whose attempt never ran, where
    there is no spend to evaluate.
    """
    if budget.turn_limit is not None and turns_used > budget.turn_limit:
        return BudgetUsage(BudgetState.TURN_EXHAUSTED, turns_used, seconds_used)
    if budget.deadline_seconds is not None and seconds_used > budget.deadline_seconds:
        return BudgetUsage(BudgetState.DEADLINE_EXHAUSTED, turns_used, seconds_used)
    if not budget.declared:
        return BudgetUsage(BudgetState.NOT_DECLARED, turns_used, seconds_used)
    return BudgetUsage(BudgetState.WITHIN, turns_used, seconds_used)


def _is_turn_start(line: str) -> bool:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(event, dict) and event.get("type") == _TURN_START_TYPE
