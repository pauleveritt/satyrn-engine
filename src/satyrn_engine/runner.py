"""E7 — running the contract's own test command as a bounded tool result.

`satyrn-evals` V13d found that removing `bash`/`write` and leaving only
`read,edit` cost 8 of 12 successes on a repair task (one-sided Fisher
p = 0.00067): baseline's `bash` calls were overwhelmingly `pytest`, run to
read the failure and then edit. `run_tests` restores exactly that one
capability -- run the suite the contract names, nothing else -- without
reopening a shell (see
docs/superpowers/specs/2026-09-06-e7-model-invocable-test-runner-design.md).

A failing suite is a *result*, not an error: the process ran to completion
(or timed out) and produced an exit code and output, which is exactly what
the model needs to keep working. Only a command that could not be started
at all -- missing or not executable -- is a refusal.

**Correction, 2026-09-06:** the original parameterless `run_tests` tool was
never invoked across four smoke cells, including one that succeeded
without ever running the suite -- the model's prior expects a `bash` tool
that takes a `command` argument, and a closed empty schema does not match
that prior. The TypeScript tool is renamed to `bash` and now accepts a
`command: str` argument so the model's expectation is satisfied at the
schema. The check that matters -- that only the contract's own command
ever runs -- moves here, into `run_tests` itself, because a schema-level
restriction fails invisibly (pi's schema validation runs before every
extension hook, so a rejected call never reaches `execute` and is
unobservable). `run_tests` now takes the model's `command` string and
compares it against the contract's `test_command`; only a match is
executed, and it is always the contract's own argv that runs, never the
model's string.
"""

import os
import re
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .contract import Contract

DEFAULT_TEST_TIMEOUT_SECONDS = 120.0
TAIL_BYTES = 8192
_TRUNCATION_MARKER = f"...[output truncated; showing final {TAIL_BYTES} bytes]...\n"
COMPACT_TAIL_LINES = 20
# The Interfaces prose regex (R2): the code-block version in the brief omits
# the optional `(0:01:15)`-style duration suffix pytest appends past 60s
# (m7), which would otherwise drop a summary line for a slow suite.
_SUMMARY = re.compile(r"^=*\s*(\d+ \w+(, )?)+ in [\d.]+s(\s*\(\d+:\d{2}:\d{2}\))?\s*=*$")
INFRASTRUCTURE = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")
_QUIET: dict[str, object] = {
    "stdin": subprocess.DEVNULL,
    "stdout": subprocess.PIPE,
    "stderr": subprocess.DEVNULL,
    "check": False,
}


class RunnerCode(StrEnum):
    """Closed outcomes for the ``test`` protocol operation.

    There is deliberately no member for a failing suite or a timeout: both
    are carried inside a successful ``OK`` result (`result.exit_code`,
    `result.timed_out`), because running the command to completion -- even
    to a bad outcome -- is what the operation was asked to do.
    """

    OK = "OK"
    TEST_COMMAND_UNAVAILABLE = "TEST_COMMAND_UNAVAILABLE"
    TEST_COMMAND_NOT_ALLOWED = "TEST_COMMAND_NOT_ALLOWED"


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


def command_matches(command: str, declared: tuple[str, ...]) -> bool:
    """Whether a model-supplied `command` string names the declared command.

    The rule is deliberately narrow: strip leading/trailing whitespace from
    `command` and compare it against `declared` joined by single spaces.
    Nothing fuzzier -- a fuzzier match is a guess about intent. A pure
    function so the default test tier can exercise it without spawning
    anything (binding rule 3).
    """
    return command.strip() == " ".join(declared)


def compact_output(text: str) -> str:
    """Failed and errored test ids with their first assertion line, plus the
    summary line.

    Against real ``pytest -q`` output: keep every line starting with
    ``FAILED `` or ``ERROR `` and the final summary line (fenced or not,
    with or without pytest's ``(H:MM:SS)`` suffix past 60s -- m7). When
    nothing matches, the last `COMPACT_TAIL_LINES` lines stand in, so a
    passing ``-q`` run keeps its progress dots (m3).
    """
    lines = text.splitlines()
    kept = [line for line in lines if line.startswith(("FAILED ", "ERROR "))]
    summary = next((line for line in reversed(lines) if _SUMMARY.match(line)), None)
    if kept:
        return "\n".join([*kept, *([summary] if summary else [])]) + "\n"
    return "\n".join(lines[-COMPACT_TAIL_LINES:]) + ("\n" if lines else "")


def carried_paths(repo: Path, contract: Contract, base: str) -> tuple[list[str], bool]:
    """`preserve` + `checks` + tracked test infrastructure, as they exist at
    `base`: every tracked `conftest.py` and any of `INFRASTRUCTURE` that is
    tracked. Order-preserving, deduplicated, filtered to what `base` really
    tracks -- a path named in `preserve`/`checks` but absent at `base` is
    silently dropped here (R3); `run_tests` reports it separately.

    Returns `(paths, ok)`, where `ok` is `False` when `git ls-tree` itself
    exited non-zero (a bad or missing `base`, or a shallow worktree) -- in
    that case `paths` is always `[]`, never a guess. Absent paths are never
    reported here; the receipt's `carried.absent` (Task 8) does that.
    """
    listed = subprocess.run(["git", "ls-tree", "-r", "--name-only", base], cwd=repo, **_QUIET)
    ok = listed.returncode == 0
    tracked = set(listed.stdout.decode("utf-8", errors="replace").splitlines()) if ok else set()
    wanted = [
        *contract.preserve,
        *contract.checks,
        *sorted(p for p in tracked if p == "conftest.py" or p.endswith("/conftest.py")),
        *(p for p in INFRASTRUCTURE if p in tracked),
    ]
    return [p for p in dict.fromkeys(wanted) if p in tracked], ok


def restore_carried(repo: Path, contract: Contract, base: str) -> tuple[tuple[str, ...], bool, bool]:
    """Restore the carried set from the accepted base before every
    self-test, so a model's edits to it never count (steering C3/N2).

    Returns `(present, ls_ok, checkout_ok)`: `ls_ok` mirrors
    `carried_paths`'s second element; `checkout_ok` is `False` only when a
    non-empty `present` was found but `git checkout` itself exited non-zero
    (`True` when there was nothing to restore). Callers that must never
    silently skip a restoration -- an explicit `base_commit`, or any
    checkout failure -- check these before trusting `present`.
    """
    present, ls_ok = carried_paths(repo, contract, base)
    checkout_ok = True
    if present:
        checked_out = subprocess.run(["git", "checkout", base, "--", *present], cwd=repo, **_QUIET)
        checkout_ok = checked_out.returncode == 0
    return tuple(present), ls_ok, checkout_ok


def _run_once(argv: list[str], repo: Path, timeout: float) -> tuple[int, str, bool, bool]:
    env = {**os.environ, "COLUMNS": "500"}
    try:
        completed = subprocess.run(
            argv,
            cwd=repo,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=max(timeout, 0.1),
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        text, truncated = tail_output(exc.output if isinstance(exc.output, bytes) else b"")
        return -1, compact_output(text), truncated, True
    text, truncated = tail_output(completed.stdout)
    return completed.returncode, compact_output(text), truncated, False


def run_tests(
    repo: Path,
    contract: Contract,
    command: str | None,
    *,
    base_commit: str | None = None,
    timeout: float = DEFAULT_TEST_TIMEOUT_SECONDS,
) -> RunnerReceipt:
    """Restore the carried set from `base_commit` (or ``HEAD``), then run
    the contract's declared command, verbatim, with no shell -- followed by
    one more run each for `preserve` and `checks`, whichever of them the
    carried set actually found tracked at the base (R3).

    `command` is what the model's tool call supplied; `self_test`'s schema
    is open and the argument is ignored (Ruling 1), so the TS side always
    sends ``None`` here and every run uses the contract's own argv
    regardless. When `command` is not ``None`` it is still compared
    (`command_matches`) against the contract's `test_command`, kept for the
    integration tier's direct calls and for any future caller that does
    supply one; a mismatch is refused as `TEST_COMMAND_NOT_ALLOWED`, naming
    the one command that is allowed. A missing or non-executable declared
    command is `TEST_COMMAND_UNAVAILABLE`, `ok: false`. Every run shares one
    deadline (m1): each gets `timeout` minus what has already elapsed, so
    the whole self-test stays under `DEFAULT_TEST_TIMEOUT_SECONDS`, and a
    timed-out run stops the remaining ones. `RunnerResult.output` is the
    compact form of the runs concatenated; `exit_code` is the first
    non-zero one, else 0.

    A restoration failure is a refusal, not a silent no-op that lets the
    suite run against whatever the model left behind: when `base_commit` is
    given explicitly and `git ls-tree` on it fails (a bad or missing base,
    or a shallow worktree), or when `git checkout` fails to restore a
    non-empty carried set (with an explicit base or the ``HEAD`` fallback
    alike), this returns `TEST_COMMAND_UNAVAILABLE` naming the base and no
    test command runs. Only when `base_commit` is ``None`` and `git ls-tree`
    itself fails does this stay a silent no-op (the pre-existing behavior
    the non-git-dir integration tests rely on) -- there, `base` is `HEAD`
    and nothing was carried forward to begin with.
    """
    declared = contract.test_command
    if not declared:
        return RunnerReceipt(
            RunnerCode.TEST_COMMAND_UNAVAILABLE,
            "contract does not declare a test_command",
        )
    if command is not None and not command_matches(command, declared):
        allowed = " ".join(declared)
        return RunnerReceipt(
            RunnerCode.TEST_COMMAND_NOT_ALLOWED,
            f'only this exact command is allowed: "{allowed}"',
        )

    base = base_commit or "HEAD"
    present, ls_ok, checkout_ok = restore_carried(repo, contract, base)
    if not checkout_ok:
        return RunnerReceipt(
            RunnerCode.TEST_COMMAND_UNAVAILABLE,
            f"carried tests could not be restored from base {base}",
        )
    if base_commit is not None and not ls_ok:
        return RunnerReceipt(
            RunnerCode.TEST_COMMAND_UNAVAILABLE,
            f"carried tests could not be read from base {base}",
        )
    present_preserve = [path for path in contract.preserve if path in present]
    present_checks = [path for path in contract.checks if path in present]

    runs: list[list[str]] = [list(declared)]
    if present_preserve:
        runs.append([*declared, *present_preserve])
    if present_checks:
        runs.append([*declared, *present_checks])

    started = time.monotonic()
    exit_code = 0
    exit_code_set = False
    outputs: list[str] = []
    truncated = False
    timed_out = False
    for argv in runs:
        remaining = timeout - (time.monotonic() - started)
        try:
            code, text, run_truncated, run_timed_out = _run_once(argv, repo, remaining)
        except OSError as exc:
            return RunnerReceipt(
                RunnerCode.TEST_COMMAND_UNAVAILABLE,
                f"cannot run test command {argv!r}: {exc}",
            )
        outputs.append(text)
        truncated = truncated or run_truncated
        timed_out = timed_out or run_timed_out
        if not exit_code_set and code != 0:
            exit_code = code
            exit_code_set = True
        if run_timed_out:
            break

    return RunnerReceipt(
        RunnerCode.OK,
        result=RunnerResult(
            exit_code=exit_code,
            output="".join(outputs),
            truncated=truncated,
            timed_out=timed_out,
        ),
    )
