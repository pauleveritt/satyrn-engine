"""R21: Pi's stdout pump keeps draining when a sink fails.

`SubprocessPiRunner.run` reads Pi's stdout on a thread and writes each line to
the transcript and to `forward` (R18). If `forward` breaks (deliver killed,
`attempt | head`) the pump used to stop reading, Pi blocked on a full pipe and
`process.wait()` never returned. Each row runs a real child that writes far
more than a pipe buffer (64 KB) and must return within the join timeout.
"""

import errno
import io
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from satyrn_engine import attempt as attempt_module
from satyrn_engine.attempt import SubprocessPiRunner

pytestmark = pytest.mark.integration

LINES = 20_000
LINE = b"x" * 99 + b"\n"
CHILD = [sys.executable, "-c", f"import sys\nfor _ in range({LINES}): sys.stdout.buffer.write({LINE!r})\n"]
JOIN_SECONDS = 30


class BrokenForward(io.RawIOBase):
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls = 0

    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> int:  # type: ignore[override]
        self.calls += 1
        raise self.exc


def _run_bounded(transcript, forward) -> tuple[int | None, BaseException | None]:
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            outcome["code"] = SubprocessPiRunner().run(
                CHILD, Path.cwd(), {}, transcript, forward, subprocess.DEVNULL  # type: ignore[arg-type]
            )
        except BaseException as exc:  # noqa: BLE001 - the row inspects it
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(JOIN_SECONDS)
    assert not worker.is_alive(), "pump stopped draining: Pi blocked on a full pipe"
    return outcome.get("code"), outcome.get("error")  # type: ignore[return-value]


def test_healthy_sinks_receive_every_line_and_the_exit_code(tmp_path: Path) -> None:
    forward = io.BytesIO()
    with (tmp_path / "t.jsonl").open("wb") as transcript:
        code, error = _run_bounded(transcript, forward)
    assert (code, error) == (0, None)
    assert forward.getvalue() == LINE * LINES
    assert (tmp_path / "t.jsonl").read_bytes() == LINE * LINES


@pytest.mark.parametrize("exc", [BrokenPipeError(32, "Broken pipe"), ValueError("I/O operation on closed file")])
def test_a_broken_forward_keeps_the_transcript_complete_and_raises_oserror(tmp_path: Path, exc: BaseException) -> None:
    forward = BrokenForward(exc)
    with (tmp_path / "t.jsonl").open("wb") as transcript:
        code, error = _run_bounded(transcript, forward)
    assert code is None
    assert isinstance(error, OSError)
    assert "forward" in str(error)
    assert forward.calls == 1  # a dead sink is not retried line by line
    assert (tmp_path / "t.jsonl").read_bytes() == LINE * LINES


def test_a_broken_transcript_keeps_forwarding_and_raises_oserror(tmp_path: Path) -> None:
    forward = io.BytesIO()
    code, error = _run_bounded(BrokenForward(OSError(28, "No space left on device")), forward)
    assert code is None
    assert isinstance(error, OSError)
    assert "transcript" in str(error)
    assert forward.getvalue() == LINE * LINES


class FlakyForward(io.RawIOBase):
    """Raises EAGAIN for its first ``fail_times`` calls, then accepts in full.

    Models the production failure: `forward` shares an open file description
    that a child (Pi/Node) has switched to O_NONBLOCK, so a write can return
    EAGAIN even though the reader is alive and draining -- the write must be
    retried, not treated as a dead sink.
    """

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0
        self.buffer = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> int:  # type: ignore[override]
        self.calls += 1
        if self.calls <= self.fail_times:
            raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
        self.buffer.extend(data)
        return len(data)


def test_a_flaky_forward_recovers_and_receives_every_byte_in_order(tmp_path: Path) -> None:
    forward = FlakyForward(fail_times=5)
    with (tmp_path / "t.jsonl").open("wb") as transcript:
        code, error = _run_bounded(transcript, forward)
    assert (code, error) == (0, None)
    assert bytes(forward.buffer) == LINE * LINES
    assert forward.calls > LINES  # the first five writes were retried, not skipped
    assert (tmp_path / "t.jsonl").read_bytes() == LINE * LINES


class PartialForward(io.RawIOBase):
    """Accepts only the first 10 bytes of any write, raising EAGAIN with
    ``characters_written`` set for the rest -- the standard shape of a
    partial non-blocking write."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> int:  # type: ignore[override]
        chunk = bytes(data[:10])
        self.buffer.extend(chunk)
        if len(chunk) < len(data):
            raise BlockingIOError(errno.EAGAIN, "partial write", len(chunk))
        return len(chunk)


def test_a_partial_forward_write_is_completed_byte_for_byte(tmp_path: Path) -> None:
    forward = PartialForward()
    with (tmp_path / "t.jsonl").open("wb") as transcript:
        code, error = _run_bounded(transcript, forward)
    assert (code, error) == (0, None)
    assert bytes(forward.buffer) == LINE * LINES
    assert (tmp_path / "t.jsonl").read_bytes() == LINE * LINES


class DeadForward(io.RawIOBase):
    """Never becomes writable -- models a reader that is truly gone."""

    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> int:  # type: ignore[override]
        raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")


def test_a_permanently_blocked_forward_is_dropped_after_the_bound_and_pi_still_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keep the test fast: the bound is a production safety net, not something
    # this row needs to wait out at full length.
    monkeypatch.setattr(attempt_module, "FORWARD_WRITE_BOUND_SECONDS", 0.2)
    forward = DeadForward()
    with (tmp_path / "t.jsonl").open("wb") as transcript:
        code, error = _run_bounded(transcript, forward)
    assert code is None
    assert isinstance(error, OSError)
    assert "forward" in str(error)
    assert (tmp_path / "t.jsonl").read_bytes() == LINE * LINES
