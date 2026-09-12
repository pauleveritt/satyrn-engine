"""Default-tier tests for the budget-enforcing implementer stream tee.

No model, no network, no subprocess: the clock is a scripted monotonic
function, and the child is a fake ``Popen`` whose merged output is pre-loaded
into a real pipe so ``selectors`` can report it readable without a process.
"""

import io
import os
from collections.abc import Callable

import pytest

import satyrn_engine.delivery as delivery
from satyrn_engine.budget import Budget, BudgetState

_TURN = b'{"type":"turn_start"}\n'
_SUITE_PASSED = b'{"type":"suite","result":"passed"}\n'


class _PipeStdout:
    """A scripted child stdout: a pipe pre-filled with bytes, then EOF."""

    def __init__(self, data: bytes) -> None:
        read_fd, write_fd = os.pipe()
        if data:
            os.write(write_fd, data)
        os.close(write_fd)
        self._fd = read_fd

    def fileno(self) -> int:
        return self._fd

    def read1(self, size: int = -1) -> bytes:
        return os.read(self._fd, size)

    def close(self) -> None:
        os.close(self._fd)


class _FakeProcess:
    def __init__(self, stdout: _PipeStdout, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode


def _clock(*values: float, default: float = 0.0) -> Callable[[], float]:
    """Return a monotonic clock that replays ``values`` then holds ``default``."""
    sequence = iter(values)

    def tick() -> float:
        try:
            return next(sequence)
        except StopIteration:
            return default

    return tick


def _run_stream(
    monkeypatch: pytest.MonkeyPatch,
    data: bytes,
    budget: Budget,
    timeout: float,
    clock: Callable[[], float],
) -> tuple[delivery._StreamOutcome, bytes]:
    stdout = _PipeStdout(data)
    monkeypatch.setattr(delivery.time, "monotonic", clock)
    spool = io.BytesIO()
    try:
        outcome = delivery._stream_implementer(
            _FakeProcess(stdout), spool, budget, timeout
        )
    finally:
        stdout.close()
    return outcome, spool.getvalue()


def test_turn_limit_exceeded_with_suite_passing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passing suite does not stop the turn limit from tripping: the
    implementer's own turn_start events are counted over the stream, and the
    4th trips a limit of 3."""
    data = _TURN * 4 + _SUITE_PASSED

    outcome, spool = _run_stream(
        monkeypatch,
        data,
        Budget(turn_limit=3),
        timeout=30.0,
        clock=_clock(),
    )

    assert outcome.exhausted is BudgetState.TURN_EXHAUSTED
    assert not outcome.command_timed_out
    assert outcome.turns_used == 4
    assert spool == data


def test_deadline_exceeded_with_partial_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deadline trips on the monotonic clock even though the child has
    produced a partial patch and is still running."""
    partial = b'{"type":"turn_start"}\n{"type":"patch","lines":2}\n'

    outcome, spool = _run_stream(
        monkeypatch,
        partial,
        Budget(deadline_seconds=0.5),
        timeout=30.0,
        clock=_clock(0.0, 0.0, 0.6),
    )

    assert outcome.exhausted is BudgetState.DEADLINE_EXHAUSTED
    assert not outcome.command_timed_out
    assert outcome.seconds_used == 0.6
    assert spool == partial


def test_under_reporting_stream_is_not_believed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counter reads the stream's turn_start events, not the model's prose
    or adapter markers: a stream that under-reports its turns still trips on
    the real events."""
    data = (
        b"the turn starts now, only one turn used\n"
        b'{"adapter_marker":"turn_start","index":0}\n'
        b'{"type":"turn_end"}\n'
        + _TURN * 3
    )

    outcome, spool = _run_stream(
        monkeypatch,
        data,
        Budget(turn_limit=2),
        timeout=30.0,
        clock=_clock(),
    )

    assert outcome.exhausted is BudgetState.TURN_EXHAUSTED
    assert outcome.turns_used == 3
    assert spool == data


def test_within_budget_stream_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sibling: a stream inside the turn limit and the deadline ends
    within budget, not exhausted or timed out."""
    data = _TURN * 2 + _SUITE_PASSED

    outcome, spool = _run_stream(
        monkeypatch,
        data,
        Budget(turn_limit=3, deadline_seconds=10.0),
        timeout=30.0,
        clock=_clock(),
    )

    assert outcome.exhausted is None
    assert not outcome.command_timed_out
    assert outcome.turns_used == 2
    assert spool == data
