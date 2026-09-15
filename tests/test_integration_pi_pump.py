"""R21: Pi's stdout pump keeps draining when a sink fails.

`SubprocessPiRunner.run` reads Pi's stdout on a thread and writes each line to
the transcript and to `forward` (R18). If `forward` breaks (deliver killed,
`attempt | head`) the pump used to stop reading, Pi blocked on a full pipe and
`process.wait()` never returned. Each row runs a real child that writes far
more than a pipe buffer (64 KB) and must return within the join timeout.
"""

import io
import subprocess
import sys
import threading
from pathlib import Path

import pytest

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
