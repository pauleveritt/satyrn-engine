"""Default-tier tests for the budget-enforcing implementer stream tee.

No model, no network, no subprocess: the clock is a scripted monotonic
function, and the child is a fake ``Popen`` whose merged output is pre-loaded
into a real pipe so ``selectors`` can report it readable without a process.
"""

import io
import json
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


class _OpenPipeStdout:
    """A still-running child's stdout: an empty pipe whose write end stays
    open, so the selector blocks until its timeout rather than reporting EOF."""

    def __init__(self) -> None:
        read_fd, write_fd = os.pipe()
        self._fd = read_fd
        self._write_fd = write_fd

    def fileno(self) -> int:
        return self._fd

    def read1(self, size: int = -1) -> bytes:
        return os.read(self._fd, size)

    def close(self) -> None:
        os.close(self._fd)
        os.close(self._write_fd)


class _FragmentStdout:
    """A scripted child stdout whose ``read1`` returns fixed-size fragments,
    so a JSON line can be split across reads. The selector fd is a real pipe
    pre-filled with one byte, so it always reports readable."""

    def __init__(self, data: bytes, fragment_size: int) -> None:
        self._data = data
        self._fragment_size = fragment_size
        self._offset = 0
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"x")
        os.close(write_fd)
        self._fd = read_fd

    def fileno(self) -> int:
        return self._fd

    def read1(self, size: int = -1) -> bytes:
        del size
        if self._offset >= len(self._data):
            return b""
        fragment = self._data[self._offset : self._offset + self._fragment_size]
        self._offset += len(fragment)
        return fragment

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


class _ExitedProcess(_FakeProcess):
    """A child that has already exited by the time the deadline is checked."""

    def poll(self) -> int:
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


def test_token_limit_exceeded_stops_the_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token limit trips over the implementer's own assistant usage: the
    stream stops as soon as the running total crosses it, so the third
    300-token line is never reached and only 600 tokens are counted."""
    data = (
        _assistant_message_end(300) + _assistant_message_end(300) + _assistant_message_end(300)
    )

    outcome, spool = _run_stream(
        monkeypatch,
        data,
        Budget(token_limit=500),
        timeout=30.0,
        clock=_clock(),
    )

    assert outcome.exhausted is BudgetState.TOKEN_EXHAUSTED
    assert not outcome.command_timed_out
    assert outcome.counter.tokens_out == 600
    assert spool == data


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
    assert outcome.counter.turns == 4
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
    assert outcome.counter.turns == 3
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
    assert outcome.counter.turns == 2
    assert spool == data


def test_deadline_longer_than_timeout_fires_deadline_not_command_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Critical precedence: when a budget deadline exceeds the command
    timeout, the effective command deadline is the deadline, so the budget
    deadline fires (retaining the partial candidate) instead of the command
    timeout discarding it."""
    stdout = _OpenPipeStdout()
    monkeypatch.setattr(delivery.time, "monotonic", _clock(0.0, 0.0, 31.0, 61.0))
    spool = io.BytesIO()
    try:
        outcome = delivery._stream_implementer(
            _FakeProcess(stdout), spool, Budget(deadline_seconds=60.0), timeout=30.0
        )
    finally:
        stdout.close()

    assert outcome.exhausted is BudgetState.DEADLINE_EXHAUSTED
    assert not outcome.command_timed_out
    assert outcome.seconds_used == 61.0
    assert spool.getvalue() == b""


def test_no_budget_command_times_out_at_the_command_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sibling: with no budget declared the command deadline is the
    caller's timeout, and crossing it is a command timeout (discard), not a
    retained deadline exhaustion."""
    stdout = _OpenPipeStdout()
    monkeypatch.setattr(delivery.time, "monotonic", _clock(0.0, 0.0, 31.0))
    spool = io.BytesIO()
    try:
        outcome = delivery._stream_implementer(
            _FakeProcess(stdout), spool, Budget(), timeout=30.0
        )
    finally:
        stdout.close()

    assert outcome.command_timed_out
    assert outcome.exhausted is None
    assert outcome.seconds_used == 31.0
    assert spool.getvalue() == b""


def test_deadline_drains_a_process_that_finished_before_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process that wrote its tail and exited just before the deadline is
    reported by its exit, not by a deadline that arrived a moment later, and
    its spool tail survives."""
    data = _TURN + b'{"type":"patch","lines":2}\n'
    stdout = _PipeStdout(data)
    monkeypatch.setattr(delivery.time, "monotonic", _clock(0.0, 0.6))
    spool = io.BytesIO()
    try:
        outcome = delivery._stream_implementer(
            _ExitedProcess(stdout), spool, Budget(deadline_seconds=0.5), timeout=30.0
        )
    finally:
        stdout.close()

    assert outcome.exhausted is None
    assert not outcome.command_timed_out
    assert outcome.counter.turns == 1
    assert spool.getvalue() == data


def test_turn_start_split_across_chunks_is_counted_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A turn_start line split across two read1 fragments is reassembled and
    counted exactly once -- the stream tee must not depend on line alignment."""
    data = _TURN + _SUITE_PASSED
    stdout = _FragmentStdout(data, fragment_size=7)
    monkeypatch.setattr(delivery.time, "monotonic", _clock())
    spool = io.BytesIO()
    try:
        outcome = delivery._stream_implementer(
            _FakeProcess(stdout), spool, Budget(turn_limit=3), timeout=30.0
        )
    finally:
        stdout.close()

    assert outcome.exhausted is None
    assert outcome.counter.turns == 1
    assert spool.getvalue() == data
