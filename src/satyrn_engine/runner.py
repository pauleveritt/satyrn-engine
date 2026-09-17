"""E7 — running the contract's own test command as a bounded tool result.

`satyrn-evals` V13d found that removing `bash`/`write` and leaving only
`read,edit` cost 8 of 12 successes on a repair task (one-sided Fisher
p = 0.00067): baseline's `bash` calls were overwhelmingly `pytest`, run to
read the failure and then edit. `run_tests` restores exactly that one
capability -- run the suite the contract names, nothing else -- as an
addition alongside native `bash`, not a replacement for it (see
docs/superpowers/specs/2026-09-06-e7-model-invocable-test-runner-design.md).

A failing suite is a *result*, not an error: the process ran to completion
(or timed out) and produced an exit code and output, which is exactly what
the model needs to keep working. Only a command that could not be started
at all -- missing or not executable -- is a refusal.

**Correction, 2026-09-06:** the original parameterless `run_tests` tool was
never invoked across four smoke cells, including one that succeeded
without ever running the suite -- the model's prior expects a `bash` tool
that takes a `command` argument, and a closed empty schema does not match
that prior. **Ruling 1 (Phase 1):** rather than shadow Pi's own `bash` under
that name, the tool is registered as `self_test`, and native `bash` stays
native and is always kept alongside it, bounded directly by guard 4
(`bounds.ts`). `self_test`'s own schema is open (`command` optional and
ignored, `additionalProperties: true`, `packages/engine/runner.ts`) so a
model call that guesses an argument is never rejected before any hook can
see it. The check that matters -- that only the contract's own command ever
runs -- lives here, in `run_tests`: when the model's tool call does supply a
`command` string it is compared (`command_matches`) against the contract's
declared `test_command`, but it is always the contract's own argv that
executes, never the model's string.
"""

import os
import re
import subprocess
import time
from collections.abc import Iterable
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
#: How many `E ` lines after each traceback block's assertion line are kept
#: (Component C, design §4). The depth-3 cells of release one found the seam
#: at R2 and not at R1, and the only difference was one such line
#: (`where None = first.timestamp.tzinfo`). Three is the design's number: it
#: covers pytest's usual `+  where` / `+    where` chain without carrying a
#: whole traceback into the model's context.
EXPLANATION_LINES = 3
_BLOCK = re.compile(r"^_{3,}.+_{3,}$")
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
    compact_bytes: int = 0


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


def _explanations(lines: list[str]) -> list[str]:
    """Per traceback block, the block header and up to `EXPLANATION_LINES`
    `E ` lines *after* that block's first `E ` line.

    The first `E ` line is pytest's assertion, which the ``FAILED`` summary
    line already carries; what release one's R1/R2 pair showed to matter is
    the lines after it. Blocks are delimited by pytest's ``___ name ___``
    rule. A block with no `E ` line, or only the assertion, contributes
    nothing -- not even its header -- so a passing run is untouched.
    """
    kept: list[str] = []
    header: str | None = None
    seen_assertion = False
    taken = 0
    for line in lines:
        if _BLOCK.match(line.strip()):
            header, seen_assertion, taken = line.strip(), False, 0
            continue
        stripped = line.lstrip()
        if not (stripped == "E" or stripped.startswith("E ")):
            continue
        if not seen_assertion:
            seen_assertion = True
            continue
        if taken >= EXPLANATION_LINES:
            continue
        if header is not None:
            kept.append(header)
            header = None
        kept.append(line.rstrip())
        taken += 1
    return kept


def compact_output(text: str) -> str:
    """Failed and errored test ids with their first assertion line, each
    failure's explanation lines, and the summary line.

    Against real ``pytest -q`` output: keep every line starting with
    ``FAILED `` or ``ERROR ``, the final summary line (fenced or not, with
    or without pytest's ``(H:MM:SS)`` suffix past 60s -- m7), and for each
    traceback block up to `EXPLANATION_LINES` ``E `` lines after that
    block's assertion, under the block's own header (Component C). When
    nothing matches, the last `COMPACT_TAIL_LINES` lines stand in, so a
    passing ``-q`` run keeps its progress dots (m3).
    """
    lines = text.splitlines()
    kept = [line for line in lines if line.startswith(("FAILED ", "ERROR "))]
    summary = next((line for line in reversed(lines) if _SUMMARY.match(line)), None)
    if kept:
        return "\n".join([*kept, *_explanations(lines), *([summary] if summary else [])]) + "\n"
    return "\n".join(lines[-COMPACT_TAIL_LINES:]) + ("\n" if lines else "")


@dataclass(frozen=True, slots=True)
class CarriedRestore:
    """The whole R9 restoration decision for one `run_tests` call.

    `paths` is the carried set actually present at the resolved base and
    restored into the worktree; `refusal` is `None` on success or the
    exact message `run_tests` should refuse with -- the one place either
    fact lives, so a caller cannot type-check its way past a failure the
    way `present, _ = carried_paths(...)` used to (Fix round 2, finding 1).
    """

    paths: tuple[str, ...]
    refusal: str | None = None


def select_carried(contract: Contract, tracked: Iterable[str]) -> list[str]:
    """The carried-set selection rule, pure: ordering, dedup, and the
    tracked filter only -- no git, no restoration.

    Order: `preserve`, then `checks`, then every tracked `conftest.py`
    (sorted), then any of `INFRASTRUCTURE` that is tracked. `tracked` is
    whatever the caller already knows is tracked at some base (`git
    ls-tree` output here; Task 5's `attempt.py` reuses this against the
    `git ls-files` output it already has, without spawning again).
    """
    tracked_set = set(tracked)
    wanted = [
        *contract.preserve,
        *contract.checks,
        *sorted(p for p in tracked_set if p == "conftest.py" or p.endswith("/conftest.py")),
        *(p for p in INFRASTRUCTURE if p in tracked_set),
    ]
    return [p for p in dict.fromkeys(wanted) if p in tracked_set]


def restore_carried(repo: Path, contract: Contract, base_commit: str | None) -> CarriedRestore:
    """The whole R9 decision: resolve `base = base_commit or "HEAD"`, read
    what `base` tracks, restore the carried set into the worktree before
    every self-test, and say whether that succeeded (steering C3/N2, R9).

    A restoration failure is a refusal, not a silent no-op that lets the
    suite run against whatever the model left behind: when `base_commit`
    is given explicitly and `git ls-tree` on it fails (a bad or missing
    base, or a shallow worktree), or when `git checkout` fails to restore
    a non-empty carried set (with an explicit base or the ``HEAD``
    fallback alike), `refusal` names the base and `paths` is empty. Only
    when `base_commit` is `None` and `git ls-tree` itself fails does this
    stay a silent no-op (the pre-existing behavior the non-git-dir
    integration tests rely on) -- there, `base` is `HEAD` and nothing was
    carried forward to begin with.
    """
    base = base_commit or "HEAD"
    listed = subprocess.run(["git", "ls-tree", "-r", "--name-only", base], cwd=repo, **_QUIET)
    ls_ok = listed.returncode == 0
    if base_commit is not None and not ls_ok:
        return CarriedRestore((), f"carried tests could not be read from base {base}")
    tracked = listed.stdout.decode("utf-8", errors="replace").splitlines() if ls_ok else []
    present = select_carried(contract, tracked)
    if present:
        checked_out = subprocess.run(["git", "checkout", base, "--", *present], cwd=repo, **_QUIET)
        if checked_out.returncode != 0:
            return CarriedRestore((), f"carried tests could not be restored from base {base}")
    return CarriedRestore(tuple(present))


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

    restore = restore_carried(repo, contract, base_commit)
    if restore.refusal is not None:
        return RunnerReceipt(RunnerCode.TEST_COMMAND_UNAVAILABLE, restore.refusal)
    present_preserve = [path for path in contract.preserve if path in restore.paths]
    present_checks = [path for path in contract.checks if path in restore.paths]

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

    output = "".join(outputs)
    return RunnerReceipt(
        RunnerCode.OK,
        result=RunnerResult(
            exit_code=exit_code,
            output=output,
            truncated=truncated,
            timed_out=timed_out,
            compact_bytes=len(output.encode("utf-8")),
        ),
    )
