"""E7 — running the contract's own test command as a bounded tool result.

`satyrn-evals` V13d found that removing `bash`/`write` and leaving only
`read,edit` cost 8 of 12 successes on a repair task (one-sided Fisher
p = 0.00067): baseline's `bash` calls were overwhelmingly `pytest`, run to
read the failure and then edit. `run_tests` restores exactly that one
capability -- run the suite the contract names, nothing else -- without
reopening a shell. The model supplies no arguments: an argument it could
choose is a shell by another name (see
docs/superpowers/specs/2026-09-06-e7-model-invocable-test-runner-design.md).

A failing suite is a *result*, not an error: the process ran to completion
(or timed out) and produced an exit code and output, which is exactly what
the model needs to keep working. Only a command that could not be started
at all -- missing or not executable -- is a refusal.
"""

import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .contract import Contract

DEFAULT_TEST_TIMEOUT_SECONDS = 120.0
TAIL_BYTES = 8192
_TRUNCATION_MARKER = f"...[output truncated; showing final {TAIL_BYTES} bytes]...\n"


class RunnerCode(StrEnum):
    """Closed outcomes for the ``test`` protocol operation.

    There is deliberately no member for a failing suite or a timeout: both
    are carried inside a successful ``OK`` result (`result.exit_code`,
    `result.timed_out`), because running the command to completion -- even
    to a bad outcome -- is what the operation was asked to do.
    """

    OK = "OK"
    TEST_COMMAND_UNAVAILABLE = "TEST_COMMAND_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RunnerResult:
    """The outcome of one completed (or timed-out) test command run."""

    exit_code: int
    output: str
    truncated: bool
    timed_out: bool


@dataclass(frozen=True, slots=True)
class RunnerReceipt:
    """One handled ``run_tests`` result."""

    code: RunnerCode
    message: str = ""
    result: RunnerResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, RunnerCode):
            raise TypeError("code must be a RunnerCode")
        if not isinstance(self.message, str):
            raise TypeError("message must be a string")
        if self.code is RunnerCode.OK:
            if not isinstance(self.result, RunnerResult):
                raise ValueError("successful runner receipt requires a result")
        elif self.result is not None:
            raise ValueError("refused runner receipt must not carry a result")

    @property
    def ok(self) -> bool:
        """Whether the command was run to completion (or timed out)."""
        return self.code is RunnerCode.OK


def tail_output(data: bytes) -> tuple[str, bool]:
    """Return the last `TAIL_BYTES` of `data`, decoded lossily.

    A pure function so the default test tier can exercise truncation and
    lossy decoding without spawning anything (binding rule 3).
    """
    truncated = len(data) > TAIL_BYTES
    tail = data[-TAIL_BYTES:] if truncated else data
    text = tail.decode("utf-8", errors="replace")
    return (_TRUNCATION_MARKER + text, True) if truncated else (text, False)


def run_tests(
    repo: Path,
    contract: Contract,
    *,
    timeout: float = DEFAULT_TEST_TIMEOUT_SECONDS,
) -> RunnerReceipt:
    """Run the contract's declared command, verbatim, with no shell.

    A missing or non-executable command is `TEST_COMMAND_UNAVAILABLE`,
    `ok: false`. A timeout is `OK`, `ok: true`, with `result.timed_out`
    set. A non-zero exit is `OK`, `ok: true`, with `result.exit_code` set
    -- a failing suite is a result, not an error.
    """
    command = contract.test_command
    if not command:
        return RunnerReceipt(
            RunnerCode.TEST_COMMAND_UNAVAILABLE,
            "contract does not declare a test_command",
        )
    try:
        completed = subprocess.run(
            list(command),
            cwd=repo,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        text, truncated = tail_output(exc.output if isinstance(exc.output, bytes) else b"")
        return RunnerReceipt(
            RunnerCode.OK,
            result=RunnerResult(exit_code=-1, output=text, truncated=truncated, timed_out=True),
        )
    except OSError as exc:
        return RunnerReceipt(
            RunnerCode.TEST_COMMAND_UNAVAILABLE,
            f"cannot run test command {list(command)!r}: {exc}",
        )
    text, truncated = tail_output(completed.stdout)
    return RunnerReceipt(
        RunnerCode.OK,
        result=RunnerResult(
            exit_code=completed.returncode,
            output=text,
            truncated=truncated,
            timed_out=False,
        ),
    )
