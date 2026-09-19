"""R21: Pi's stdout pump keeps draining when a sink fails.

`SubprocessPiRunner.run` reads Pi's stdout on a thread and writes each line to
the transcript and to `forward` (R18). If `forward` breaks (deliver killed,
`attempt | head`) the pump used to stop reading, Pi blocked on a full pipe and
`process.wait()` never returned. Each row runs a real child that writes far
more than a pipe buffer (64 KB) and must return within the join timeout.
"""

import errno
import fcntl
import io
import json
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from satyrn_engine import attempt as attempt_module
from satyrn_engine.attempt import SubprocessPiRunner
from satyrn_engine.budget import TurnCounter

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


# C1 (Opus review, 2026-09-19): production's `forward` is `sys.stdout.buffer`,
# a real `io.BufferedWriter` (default 8192-byte buffer) over a pipe Node has
# flipped to O_NONBLOCK (`_write_retrying_backpressure`'s docstring). JSONL
# lines are small: `write()` only fills the buffer, and the syscall -- and the
# EAGAIN -- happens in `flush()`, which the pump called unguarded. Every
# double above (`BrokenForward`/`FlakyForward`/`PartialForward`/`DeadForward`)
# subclasses `io.RawIOBase`, whose `flush()` is a no-op, so none of them can
# exercise this path.
EVENT_LINES = 3000
EVENT_CHILD_SCRIPT = (
    "import json, sys\n"
    f"for i in range({EVENT_LINES}):\n"
    "    sys.stdout.buffer.write(json.dumps({'type': 'turn_start'}).encode() + b'\\n')\n"
    "    sys.stdout.buffer.write(json.dumps({'type': 'message_end', 'message': "
    "{'role': 'assistant', 'usage': {'input': 10, 'output': 20}}}).encode() + b'\\n')\n"
)
EVENT_CHILD = [sys.executable, "-c", EVENT_CHILD_SCRIPT]


def _counts(data: bytes) -> tuple[int, int, int]:
    counter = TurnCounter()
    for raw in data.splitlines():
        if raw:
            counter.feed(raw.decode("utf-8"))
    return counter.turns, counter.tokens_in, counter.tokens_out


def test_a_real_buffered_writer_over_a_nonblocking_pipe_receives_every_byte(tmp_path: Path) -> None:
    """A real `io.BufferedWriter` over a real non-blocking `os.pipe()`, drained
    by a slow-but-live reader thread -- exactly production's shape. Every byte
    must arrive, in order; `run()` must return Pi's exit code (0), never raise
    `_ForwardSinkFailed`; and the receipt-level counts derived from `forward`
    (what `deliver`'s live budget counter actually sees) must equal the
    counts derived from the transcript (the artifact of record)."""
    read_fd, write_fd = os.pipe()
    flags = fcntl.fcntl(write_fd, fcntl.F_GETFL)
    fcntl.fcntl(write_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    forward = io.BufferedWriter(io.FileIO(write_fd, "wb", closefd=True), 8192)

    received = bytearray()

    def drain() -> None:
        while True:
            try:
                chunk = os.read(read_fd, 4096)
            except BlockingIOError:
                time.sleep(0.005)
                continue
            if not chunk:
                return
            received.extend(chunk)
            time.sleep(0.001)  # a live but slow reader (deliver parsing JSON)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            with (tmp_path / "t.jsonl").open("wb") as transcript:
                outcome["code"] = SubprocessPiRunner().run(
                    EVENT_CHILD, Path.cwd(), {}, transcript, forward, subprocess.DEVNULL  # type: ignore[arg-type]
                )
        except BaseException as exc:  # noqa: BLE001 - the row inspects it
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(JOIN_SECONDS)
    assert not worker.is_alive(), "pump stopped draining: Pi blocked on a full pipe"
    forward.close()
    reader.join(JOIN_SECONDS)
    assert not reader.is_alive(), "reader thread never saw EOF on the forward pipe"
    os.close(read_fd)

    assert (outcome.get("code"), outcome.get("error")) == (0, None)
    transcript_bytes = (tmp_path / "t.jsonl").read_bytes()
    assert bytes(received) == transcript_bytes
    assert _counts(bytes(received)) == _counts(transcript_bytes)
    assert _counts(transcript_bytes) == (EVENT_LINES, EVENT_LINES * 10, EVENT_LINES * 20)


# I4 (Opus review, 2026-09-19): give Pi its own dedicated stderr pipe instead
# of sharing attempt's own stdout/stderr open file description with it.
# Production shares that OFD because `deliver` spawns `attempt` with
# stderr=STDOUT, so `attempt`'s own `sys.stdout` and `sys.stderr` are two fds
# on the same open file description; the old `SubprocessPiRunner.run` handed
# its own `stderr` parameter straight to Pi's `Popen(stderr=...)`, so
# whatever Node does to Pi's inherited stderr (O_NONBLOCK) landed on that
# shared OFD -- and therefore on `forward` too, C1's whole scenario. This
# test constructs that same shared-OFD relationship directly (`os.dup`, the
# same mechanism `stderr=STDOUT` uses) and proves the fix: Pi never touches
# either descriptor, so `forward`'s own fd must stay blocking no matter what
# the child does to its own stderr, and Pi's stderr text must still reach
# attempt's own stderr sink.
def test_pi_gets_its_own_stderr_pipe_and_forwards_fd_stays_blocking(tmp_path: Path) -> None:
    read_fd, write_fd = os.pipe()
    forward = io.BufferedWriter(io.FileIO(write_fd, "wb", closefd=True), 8192)
    # The same mechanism `subprocess.Popen(stderr=subprocess.STDOUT)` uses:
    # a second fd on the very same open file description as `forward`.
    stderr_sink = io.BufferedWriter(io.FileIO(os.dup(write_fd), "wb", closefd=True), 8192)

    child_script = (
        "import fcntl, os, sys\n"
        "flags = fcntl.fcntl(2, fcntl.F_GETFL)\n"
        "fcntl.fcntl(2, fcntl.F_SETFL, flags | os.O_NONBLOCK)\n"
        "sys.stderr.write('hello from pi stderr\\n')\n"
        "sys.stderr.flush()\n"
    )
    command = [sys.executable, "-c", child_script]

    with (tmp_path / "t.jsonl").open("wb") as transcript:
        code = SubprocessPiRunner().run(command, Path.cwd(), {}, transcript, forward, stderr_sink)
    assert code == 0

    forward_flags = fcntl.fcntl(forward.fileno(), fcntl.F_GETFL)
    assert not (forward_flags & os.O_NONBLOCK), "forward's own fd must remain blocking after Pi exits"

    forward.close()
    stderr_sink.close()
    os.set_blocking(read_fd, True)
    received = bytearray()
    while chunk := os.read(read_fd, 65536):
        received.extend(chunk)
    os.close(read_fd)
    assert b"hello from pi stderr" in bytes(received)


def _open_fd_count() -> int:
    return len(os.listdir("/dev/fd"))


# M-B (Opus review, 2026-09-19): `run()` opens `os.pipe()` for Pi's own
# dedicated stderr (I4) before `Popen`. The old code's `try`/`finally` around
# `Popen` closed only the write end; if `Popen` raises (missing executable)
# or `_forward_termination` raises before the drain thread ever starts, the
# read end is handed to no one and leaks for the life of the process. Run it
# enough times that a per-call leak is unmistakable against the handful of
# fds pytest itself holds open (stdio, the collected test files, etc).
def test_a_missing_pi_executable_does_not_leak_the_stderr_pipes_read_end(tmp_path: Path) -> None:
    baseline = _open_fd_count()
    forward = io.BytesIO()
    with (tmp_path / "t.jsonl").open("wb") as transcript:
        for _ in range(50):
            with pytest.raises(FileNotFoundError):
                SubprocessPiRunner().run(
                    ["/nonexistent/satyrn-fake-pi-binary"], Path.cwd(), {}, transcript, forward, subprocess.DEVNULL  # type: ignore[arg-type]
                )
    assert _open_fd_count() <= baseline + 5, "the stderr pipe's read end leaked across missing-executable runs"


class _RecordingSink(io.RawIOBase):
    """Records every `write()` call's own bytes, in order, under a lock so a
    polling reader thread can inspect it while the writer thread keeps
    running."""

    def __init__(self) -> None:
        self.calls: list[bytes] = []
        self.lock = threading.Lock()

    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> int:  # type: ignore[override]
        with self.lock:
            self.calls.append(bytes(data))
        return len(data)

    def snapshot(self) -> bytes:
        with self.lock:
            return b"".join(self.calls)


# M-A (Opus review, 2026-09-19): the drain thread's docstring promises Pi's
# stderr is relayed "line by line as it is read", but the old body called
# `reader.read(65536)` on a `BufferedReader` -- `read(n)` blocks until it has
# `n` bytes or hits EOF, so nothing is relayed until either the pipe fills
# with 64 KB or Pi exits. A short line followed by a long silent stretch (the
# common shape of a hung/slow descendant) sat unrelayed the whole time, and if
# `attempt` is killed under deliver's process-group kill on a timeout, it is
# lost outright. `read1()` returns as soon as any data is available, matching
# the docstring.
def test_pi_stderr_is_relayed_promptly_not_only_at_eof(tmp_path: Path) -> None:
    child_script = (
        "import sys, time\n"
        "sys.stderr.write('quick line\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(2)\n"
    )
    command = [sys.executable, "-c", child_script]
    forward = io.BytesIO()
    stderr_sink = _RecordingSink()
    outcome: dict[str, object] = {}

    def target() -> None:
        with (tmp_path / "t.jsonl").open("wb") as transcript:
            outcome["code"] = SubprocessPiRunner().run(
                command, Path.cwd(), {}, transcript, forward, stderr_sink  # type: ignore[arg-type]
            )

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    deadline = time.monotonic() + 0.5
    seen = False
    while time.monotonic() < deadline:
        if b"quick line" in stderr_sink.snapshot():
            seen = True
            break
        time.sleep(0.01)
    assert worker.is_alive(), "the child should still be sleeping when this assertion runs"
    assert seen, "Pi's stderr line was not relayed within 0.5s -- still buffered until EOF/64KB"
    worker.join(JOIN_SECONDS)
    assert not worker.is_alive()
    assert outcome.get("code") == 0


# M-D (Opus review, 2026-09-19): under deliver's real spawn shape
# (`stderr=STDOUT`), attempt's own stdout (`forward`, the JSONL stream
# deliver parses) and stderr (what the drain thread relays Pi's stderr onto)
# are two fds on one open file description -- the same pipe. A write of more
# than PIPE_BUF bytes is not atomic against a concurrent write from another
# thread to that same pipe, so a single large relayed blob could interleave
# with -- and corrupt -- a `forward` JSONL line mid-write. Deterministic
# proof: relay one stderr line far larger than PIPE_BUF and assert every
# write the sink actually received was capped at PIPE_BUF (the atomicity
# floor) and flushed as its own call, not handed to `write()` in one shot.
def test_pi_stderr_relay_caps_each_write_at_pipe_buf(tmp_path: Path) -> None:
    big_line = b"z" * 5000 + b"\n"
    child_script = f"import sys\nsys.stderr.buffer.write({big_line!r})\nsys.stderr.buffer.flush()\n"
    command = [sys.executable, "-c", child_script]
    forward = io.BytesIO()
    sink = _RecordingSink()
    with (tmp_path / "t.jsonl").open("wb") as transcript:
        code = SubprocessPiRunner().run(command, Path.cwd(), {}, transcript, forward, sink)  # type: ignore[arg-type]
    assert code == 0
    assert sink.calls, "no data was relayed"
    assert all(len(chunk) <= select.PIPE_BUF for chunk in sink.calls), [len(c) for c in sink.calls]
    assert sink.snapshot() == big_line


# M-D, end to end: a large stderr line and a stream of small forwarded JSONL
# lines sharing one pipe (the same shared-OFD construction I4's test above
# uses), proving no splice reaches `forward`'s own stream once the relay is
# capped and both sinks' write+flush calls are serialized under one lock.
def test_a_large_relayed_stderr_line_does_not_splice_into_forwarded_jsonl(tmp_path: Path) -> None:
    read_fd, write_fd = os.pipe()
    forward = io.BufferedWriter(io.FileIO(write_fd, "wb", closefd=True), 8192)
    stderr_sink = io.BufferedWriter(io.FileIO(os.dup(write_fd), "wb", closefd=True), 8192)

    child_script = (
        "import json, sys\n"
        "big = ('y' * 199999 + '\\n').encode()\n"
        "for i in range(200):\n"
        "    sys.stdout.buffer.write((json.dumps({'type': 'turn_start', 'i': i}) + '\\n').encode())\n"
        "    sys.stdout.buffer.flush()\n"
        "    if i % 20 == 0:\n"
        "        sys.stderr.buffer.write(big)\n"
        "        sys.stderr.buffer.flush()\n"
    )
    command = [sys.executable, "-c", child_script]

    received = bytearray()

    def drain() -> None:
        while True:
            chunk = os.read(read_fd, 65536)
            if not chunk:
                return
            received.extend(chunk)

    reader_thread = threading.Thread(target=drain, daemon=True)
    reader_thread.start()

    with (tmp_path / "t.jsonl").open("wb") as transcript:
        code = SubprocessPiRunner().run(command, Path.cwd(), {}, transcript, forward, stderr_sink)
    assert code == 0

    forward.close()
    stderr_sink.close()
    reader_thread.join(JOIN_SECONDS)
    assert not reader_thread.is_alive()
    os.close(read_fd)

    turn_lines = 0
    for raw in bytes(received).splitlines():
        if raw.startswith(b'{"type": "turn_start"'):
            json.loads(raw)  # a splice would corrupt this line and raise
            turn_lines += 1
    assert turn_lines == 200
