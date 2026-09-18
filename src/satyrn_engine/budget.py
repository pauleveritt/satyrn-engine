"""Engine-owned whole-attempt turn, token, and wall-clock budget.

A budget is a declaration (:class:`Budget`) plus an accounting of what was
spent (:class:`BudgetUsage`). :class:`TurnCounter` is the one counter over
the implementer's own stream: it reads the implementer's own JSON-lines
transcript, never the model's prose, and counts turns (``turn_start``),
assistant token usage (``message_end``, input and output; ``cacheRead`` is
never counted), tool calls (``tool_execution_start``), and guard firings
(``entry_appended`` whose ``entry.customType`` is one of ``GUARD_KINDS``).
"""

import json
from dataclasses import dataclass
from enum import StrEnum

GUARD_KINDS: tuple[str, ...] = (
    "loop_broken",
    "scope_refused",
    "symbol_preserved",
    "command_bounded",
    "command_timed_out",
    "self_test_redirected",
    "self_test_detected",
    "self_test_enforced",
    "finish_nudged",
    "runaway_resumed",
)


class BudgetState(StrEnum):
    """Closed vocabulary for where an attempt stands against its budget.

    ``WITHIN``, ``TURN_EXHAUSTED``, ``TOKEN_EXHAUSTED``, and
    ``DEADLINE_EXHAUSTED`` are spend states :func:`evaluate` derives once an
    attempt actually ran. ``NOT_DECLARED`` means no limit was declared.
    ``NOT_ENFORCED`` means a limit was declared but the attempt never ran (a
    preflight refusal or an unavailable command), so there is no spend to
    measure and no honest ``WITHIN`` to report.
    """

    WITHIN = "within"
    TURN_EXHAUSTED = "turn_exhausted"
    TOKEN_EXHAUSTED = "token_exhausted"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    NOT_DECLARED = "not_declared"
    NOT_ENFORCED = "not_enforced"


@dataclass(frozen=True, slots=True)
class Budget:
    """A declared budget.

    ``None`` means "no limit of this kind", and ``declared`` is the single
    place that turns three ``None`` values into one name. A declared-but-
    unenforced budget is therefore its own state (``NOT_ENFORCED``), distinct
    from ``WITHIN`` (a declared budget whose attempt did run) and never an
    ambiguous ``None``.
    """

    turn_limit: int | None = None
    deadline_seconds: float | None = None
    token_limit: int | None = None

    @property
    def declared(self) -> bool:
        return (
            self.turn_limit is not None
            or self.deadline_seconds is not None
            or self.token_limit is not None
        )


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """What was spent, and the state that follows from it."""

    state: BudgetState
    turns_used: int
    seconds_used: float
    tokens_used: int = 0


class TurnCounter:
    """The one counter over the implementer's own stream: turns, assistant
    tokens (input and output; cacheRead excluded), tool calls, guard
    firings."""

    def __init__(self) -> None:
        self._turns = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.tool_calls = 0
        self.guard_firings: dict[str, int] = dict.fromkeys(GUARD_KINDS, 0)

    def feed(self, line: str) -> None:
        """Count one transcript line if it carries a countable event.

        The line is parsed as JSON; prose that merely mentions the words
        "turn start" or a token count does not count."""
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        match event.get("type"):
            case "turn_start":
                self._turns += 1
            case "tool_execution_start":
                self.tool_calls += 1
            case "message_end":
                message = event.get("message")
                if isinstance(message, dict) and message.get("role") == "assistant":
                    usage = message.get("usage")
                    if isinstance(usage, dict):
                        self.tokens_in += _count(usage.get("input"))
                        self.tokens_out += _count(usage.get("output"))
            case "entry_appended":
                entry = event.get("entry")
                if (
                    isinstance(entry, dict)
                    and entry.get("type") == "custom"
                    and entry.get("customType") in self.guard_firings
                ):
                    self.guard_firings[entry["customType"]] += 1

    @property
    def turns(self) -> int:
        return self._turns


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def evaluate(
    budget: Budget, turns_used: int, seconds_used: float, tokens_used: int = 0
) -> BudgetUsage:
    """Return the budget state for a measured spend.

    A turn limit trips when the count exceeds it, so a limit of 3 trips on
    the 4th ``turn_start``; a token limit trips the same way. A deadline
    trips when the elapsed monotonic seconds exceed it. With no declared
    budget the state is ``NOT_DECLARED``, never a ``None`` standing in for
    "no budget reached".

    Token exhaustion is checked first: it wins over a turn or deadline trip
    on the same observation, since it is the tightest of the three whole-
    attempt limits to have already been crossed.

    ``evaluate`` never returns ``NOT_ENFORCED``: that state is assigned by
    the receipt layer for a declared budget whose attempt never ran, where
    there is no spend to evaluate.
    """
    if budget.token_limit is not None and tokens_used > budget.token_limit:
        return BudgetUsage(BudgetState.TOKEN_EXHAUSTED, turns_used, seconds_used, tokens_used)
    if budget.turn_limit is not None and turns_used > budget.turn_limit:
        return BudgetUsage(BudgetState.TURN_EXHAUSTED, turns_used, seconds_used, tokens_used)
    if budget.deadline_seconds is not None and seconds_used > budget.deadline_seconds:
        return BudgetUsage(BudgetState.DEADLINE_EXHAUSTED, turns_used, seconds_used, tokens_used)
    if not budget.declared:
        return BudgetUsage(BudgetState.NOT_DECLARED, turns_used, seconds_used, tokens_used)
    return BudgetUsage(BudgetState.WITHIN, turns_used, seconds_used, tokens_used)
