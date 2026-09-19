"""Run one real Pi attempt inside the caller-owned disposable worktree."""

import errno
import json
import os
import re
import secrets
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch
from pathlib import Path
from typing import BinaryIO, Never, Protocol

from .check import check
from .contract import Contract, ContractError, load_contract
from .exits import ExitCode
from .mutation import file_sha256, normalize_relative_path
from .runner import select_carried

MODEL_ENV = "SATYRN_MODEL"
PATCH_ENV = "SATYRN_ATTEMPT_PATCH"
TRANSCRIPT_ENV = "SATYRN_ATTEMPT_TRANSCRIPT"
MUTATION_CONTEXT_ENV = "SATYRN_MUTATION_CONTEXT"
ENGINE_REPO_ENV = "SATYRN_ENGINE_REPO"

_SYMBOL = re.compile(rb"^[ \t]*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", re.MULTILINE)


def _defined_symbols(content: bytes) -> list[str]:
    """def/class names at any indentation that the accepted base defines in one file
    (matches scope.ts/mutator.ts's I2 rule; indentation allowed)."""
    return sorted({match.group(1).decode("ascii") for match in _SYMBOL.finditer(content)})


class _GitRoutingVariable(StrEnum):
    """Git variables that could redirect owned commands from the attempt repo."""

    ALTERNATE_OBJECT_DIRECTORIES = "GIT_ALTERNATE_OBJECT_DIRECTORIES"
    COMMON_DIR = "GIT_COMMON_DIR"
    DIR = "GIT_DIR"
    INDEX_FILE = "GIT_INDEX_FILE"
    NAMESPACE = "GIT_NAMESPACE"
    OBJECT_DIRECTORY = "GIT_OBJECT_DIRECTORY"
    PREFIX = "GIT_PREFIX"
    WORK_TREE = "GIT_WORK_TREE"


class _ArtifactKind(StrEnum):
    PATCH = "patch"
    TRANSCRIPT = "transcript"


class AttemptCode(StrEnum):
    """Closed outcomes from one E5 attempt."""

    OK = "OK"
    CONTRACT_UNREADABLE = "CONTRACT_UNREADABLE"
    CONTRACT_INVALID_YAML = "CONTRACT_INVALID_YAML"
    CONTRACT_MISSING_FIELD = "CONTRACT_MISSING_FIELD"
    REPO_UNAVAILABLE = "REPO_UNAVAILABLE"
    ATTEMPT_FAILED = "ATTEMPT_FAILED"


_ATTEMPT_TO_EXIT: dict[AttemptCode, ExitCode] = {
    AttemptCode.OK: ExitCode.OK,
    AttemptCode.CONTRACT_UNREADABLE: ExitCode.CONTRACT_UNREADABLE,
    AttemptCode.CONTRACT_INVALID_YAML: ExitCode.CONTRACT_INVALID_YAML,
    AttemptCode.CONTRACT_MISSING_FIELD: ExitCode.CONTRACT_MISSING_FIELD,
    AttemptCode.REPO_UNAVAILABLE: ExitCode.REPO_UNAVAILABLE,
    AttemptCode.ATTEMPT_FAILED: ExitCode.ATTEMPT_FAILED,
}

_CHECK_TO_ATTEMPT: dict[ExitCode, AttemptCode] = {
    ExitCode.CONTRACT_UNREADABLE: AttemptCode.CONTRACT_UNREADABLE,
    ExitCode.CONTRACT_INVALID_YAML: AttemptCode.CONTRACT_INVALID_YAML,
    ExitCode.CONTRACT_MISSING_FIELD: AttemptCode.CONTRACT_MISSING_FIELD,
    ExitCode.REPO_UNAVAILABLE: AttemptCode.REPO_UNAVAILABLE,
}


@dataclass(frozen=True, slots=True)
class AttemptResult:
    """One handled attempt result."""

    code: AttemptCode
    message: str = ""
    model: str | None = None
    command_exit: int | None = None
    #: True once `forward` (the live tee to `deliver`'s budget counter, R18)
    #: was dropped for good during this attempt (`_ForwardSinkFailed`,
    #: raised only after `_write_retrying_backpressure` exhausts
    #: `FORWARD_WRITE_BOUND_SECONDS`). Pi's own outcome is judged
    #: independently either way (see `_run`'s `except _ForwardSinkFailed`),
    #: but a caller counting turns/tokens from that same live stream
    #: (`deliver`) undercounts everything after the drop -- this is the only
    #: surviving channel (attempt's own process exit) that can tell it so,
    #: since the dropped sink is attempt's own stdout/stderr.
    forward_lost: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.code, AttemptCode):
            raise TypeError("code must be an AttemptCode")

    @property
    def exit_code(self) -> ExitCode:
        """Return the stable process exit for this detailed result.

        `ATTEMPT_OK_FORWARD_LOST` is the one exception to the plain
        code-to-exit mapping: an otherwise-successful attempt whose
        `forward` sink was dropped for good still exits 0's neighbour, not
        0, so `deliver` -- which has no other way to learn this, since the
        dropped sink *is* this process's own stdout/stderr -- can still
        create the candidate while marking its receipt's live counter
        untrustworthy instead of silently reporting it as exact.
        """
        if self.code is AttemptCode.OK and self.forward_lost:
            return ExitCode.ATTEMPT_OK_FORWARD_LOST
        return _ATTEMPT_TO_EXIT[self.code]


@dataclass(frozen=True, slots=True)
class AttemptArtifacts:
    """Optional caller-owned artifact destinations outside the repository."""

    patch: _ArtifactDestination | None
    transcript: _ArtifactDestination | None


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    """Filesystem identity used when spelling and case are not authoritative."""

    device: int
    inode: int


@dataclass(slots=True)
class _ArtifactDestination:
    """An artifact path pinned to its already-validated parent.

    ``content_descriptor`` is set only for the transcript (E10): unlike
    every other artifact, which stays absent until it is published by
    ``os.link`` after the run, the transcript is created exclusively here,
    at preparation time, and this descriptor is the open handle Pi writes
    into directly.
    """

    path: Path
    parent_identity: _FileIdentity
    parent_descriptor: int | None
    content_descriptor: int | None = None

    def descriptor(self) -> int:
        """Return the owned parent descriptor while it remains open."""
        if self.parent_descriptor is None:
            raise RuntimeError(f"artifact parent is already closed: {self.path.parent}")
        return self.parent_descriptor

    def take_descriptor(self) -> int | None:
        """Transfer descriptor ownership exactly once for cleanup."""
        descriptor = self.parent_descriptor
        self.parent_descriptor = None
        return descriptor

    def take_content_descriptor(self) -> int | None:
        """Transfer the exclusively-created content descriptor exactly once."""
        descriptor = self.content_descriptor
        self.content_descriptor = None
        return descriptor


@dataclass(frozen=True, slots=True)
class AttemptContext:
    """Immutable inputs captured before Pi starts."""

    repo: Path
    contract: Contract
    frozen_contract: Path
    base_commit: str
    revisions: Mapping[str, str]
    symbols: Mapping[str, list[str]]
    tracked_writable: tuple[str, ...]
    carried: tuple[str, ...]
    model: str
    engine_repo: Path


@dataclass(frozen=True, slots=True)
class GitResult:
    """The byte-preserving result of one engine-owned Git command."""

    returncode: int
    stdout: bytes
    stderr: bytes


class GitRunner(Protocol):
    """The production and test seam for owned Git commands."""

    def run(
        self,
        repo: Path,
        args: Sequence[str],
        environment: Mapping[str, str],
    ) -> GitResult: ...


class PiRunner(Protocol):
    """The production and test seam for the one Pi child.

    R18: Pi's stdout is forwarded to *both* ``transcript`` and ``forward``
    while Pi runs, not copied from one to the other after it exits --
    budget enforcement in ``deliver`` (``_stream_implementer``) only sees
    ``turn_start``/``message_end``/``entry_appended`` lines through
    ``forward`` (attempt's own stdout, which ``deliver`` pipes and reads
    live), so a post-exit copy would leave a budget unable to trip during
    the run. ``transcript`` keeps the exact same bytes ``attempt`` has
    always written there (a file, or the E10 destination descriptor).
    """

    def run(
        self,
        command: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str],
        transcript: BinaryIO,
        forward: BinaryIO,
        stderr: BinaryIO,
    ) -> int: ...


class _ForwardSinkFailed(OSError):
    """`forward` (attempt's own stdout, tee'd live to `deliver`'s budget
    counter -- R18) failed while Pi was running, independently of Pi
    itself. `SubprocessPiRunner.run` still raises for it -- an operator or
    `deliver` losing that live tee is worth surfacing
    (test_integration_pi_pump.py) -- but it carries the exit code
    `process.wait()` already returned, so `_run` can judge the attempt by
    what Pi actually did instead of by the health of a sink that stopped
    mattering the moment Pi exited.
    """

    def __init__(self, message: str, *, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


#: How long one write to a pump sink (`transcript` or `forward`) may spend
#: waiting to become writable again before the pump gives up on it, the same
#: as any other unrecoverable sink failure (R21). EAGAIN/EWOULDBLOCK on
#: `forward` is expected to clear almost immediately when the reader
#: (`deliver`) is alive and draining -- see `_write_retrying_backpressure`'s
#: docstring for why the write can return EAGAIN at all even though nothing
#: is actually wrong. This bound only matters for a reader that is truly
#: gone (`deliver` killed, `attempt | head`); it exists so that case still
#: cannot hang the pump forever, per R21.
FORWARD_WRITE_BOUND_SECONDS = 30.0


def _wait_writable(fd: int | None, timeout: float) -> None:
    """Block up to ``timeout`` seconds for ``fd`` to accept a write.

    ``fd`` is ``None`` for a sink with no real file descriptor (an in-memory
    test double); there is nothing to poll, so a retry loop around this
    would otherwise spin at full CPU until its deadline (M6) -- sleep for a
    short, bounded slice instead of returning immediately.
    """
    if fd is None:
        time.sleep(min(timeout, 0.01))
        return
    selector = selectors.DefaultSelector()
    try:
        selector.register(fd, selectors.EVENT_WRITE)
        selector.select(timeout=timeout)
    finally:
        selector.close()


def _write_retrying_backpressure(sink: BinaryIO, data: bytes, *, deadline: float) -> None:
    """Write ``data`` to ``sink`` in full, surviving EAGAIN/EWOULDBLOCK.

    `forward` is attempt's own stdout, which `deliver` pipes and reads live
    (R18). On production hosts that pipe's write end can end up sharing an
    open file description with Pi's own stderr (Pi/Node inherits it -- see
    `SubprocessPiRunner.run`'s docstring); Node puts pipes it inherits into
    O_NONBLOCK, and that flag lives on the open file description, not the
    file descriptor number, so it applies to `forward` too even though
    attempt never asked for non-blocking I/O. The result is a `write()` that
    raises ``BlockingIOError`` (EAGAIN/EWOULDBLOCK) or returns fewer bytes
    than given, even though the reader (`deliver`) is alive and draining
    normally -- the write just landed while the pipe's kernel buffer was
    momentarily full.

    Neither shape is data loss on its own: a raised ``BlockingIOError``
    reports how much it did accept via ``characters_written`` (0 when
    nothing was written), and a plain short return means exactly that many
    bytes were consumed. Either way the remainder is retried, waiting for
    writability with `select`/`poll` on the sink's own descriptor when it
    has one (an in-memory test double does not, so retries are immediate).
    Retrying stops once ``deadline`` (a ``time.monotonic()`` value) passes,
    at which point a ``BlockingIOError`` is raised so the caller's existing
    "this sink is dead" handling (R21) drops it -- a reader that is truly
    gone must not hang the pump.
    """
    remaining = data
    try:
        fd = sink.fileno()
    except (AttributeError, OSError, ValueError):
        fd = None
    while remaining:
        try:
            written = sink.write(remaining)
        except BlockingIOError as exc:
            written = getattr(exc, "characters_written", None) or 0
        if written:
            remaining = remaining[written:]
            if not remaining:
                return
        now = time.monotonic()
        if now >= deadline:
            raise BlockingIOError(
                errno.EAGAIN,
                "sink did not accept the remaining bytes before the bound",
            )
        # M6: only fall through to `_wait_writable`'s fd-less sleep when this
        # iteration accepted nothing at all -- a sink with no real fd that is
        # still making partial progress (a test double modelling a partial
        # non-blocking write) is not hot-looping and must keep retrying at
        # full speed, or byte-at-a-time doubles become impractically slow.
        if fd is not None or written == 0:
            _wait_writable(fd, min(deadline - now, 1.0))


def _flush_retrying_backpressure(sink: BinaryIO, *, deadline: float) -> None:
    """Flush ``sink`` in full, surviving EAGAIN/EWOULDBLOCK the same way
    `_write_retrying_backpressure` does for `write()`.

    Production's `forward` is `sys.stdout.buffer`, a real
    `io.BufferedWriter` (an 8192-byte buffer by default) over a pipe that
    can share Pi's O_NONBLOCK open file description (see
    `_write_retrying_backpressure`'s docstring). A single JSONL line is far
    smaller than that buffer, so `write()` usually just fills it -- the
    syscall, and any EAGAIN, happens on the next `flush()`. A
    `BlockingIOError` from `BufferedWriter.flush()` does *not* discard the
    unwritten bytes: CPython keeps them in the buffer for the next
    `flush()` call. Retrying must therefore call `flush()` again, never
    `write()` -- re-writing would duplicate whatever partial amount the
    kernel already accepted.
    """
    try:
        fd = sink.fileno()
    except (AttributeError, OSError, ValueError):
        fd = None
    while True:
        try:
            sink.flush()
            return
        except BlockingIOError:
            now = time.monotonic()
            if now >= deadline:
                raise
            _wait_writable(fd, min(deadline - now, 1.0))


class SubprocessGitRunner:
    """Run Git without shell interpretation and preserve exact stdout bytes."""

    def run(
        self,
        repo: Path,
        args: Sequence[str],
        environment: Mapping[str, str],
    ) -> GitResult:
        completed = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                *args,
            ],
            cwd=repo,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        return GitResult(completed.returncode, completed.stdout, completed.stderr)


class SubprocessPiRunner:
    """Start the one synchronous Pi print-mode child."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._termination_signal: int | None = None

    def request_termination(self, signal_number: int) -> None:
        """Forward an outer termination signal to the active Pi child."""
        self._termination_signal = signal_number
        if self._process is not None:
            self._forward_termination()

    def _forward_termination(self) -> None:
        process = self._process
        if self._termination_signal is None or process is None or process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, self._termination_signal)
            else:
                process.send_signal(self._termination_signal)
        except ProcessLookupError:
            return
        except OSError:
            with suppress(OSError):
                process.terminate()

    def run(
        self,
        command: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str],
        transcript: BinaryIO,
        forward: BinaryIO,
        stderr: BinaryIO,
    ) -> int:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=stderr,
            start_new_session=os.name == "posix",
        )
        self._process = process
        self._forward_termination()
        sink_errors: dict[str, BaseException] = {}

        def pump() -> None:
            # R18: a reader thread over Popen.stdout, not a post-exit copy --
            # every line reaches `transcript` and `forward` while Pi runs,
            # flushed at once so deliver's live budget counter sees it.
            # R21: a failing sink is dropped, never the read loop. If
            # `forward` breaks (deliver killed, `attempt | head`) or the
            # transcript cannot be written, the pump keeps draining Pi's
            # stdout into the surviving sink, so Pi never blocks on a full
            # pipe and `process.wait()` returns. The first error per sink is
            # raised by run() after Pi exits (test_integration_pi_pump.py
            # pins this: the caller must learn a sink died even though the
            # read loop kept going).
            #
            # `forward` exists only to tee live output to `deliver`'s budget
            # counter (R18) while Pi runs; once `process.wait()` has
            # returned, that counter has nothing left to watch. A plain
            # `OSError` here does not know that -- and on 2026-09-19, three
            # self-hosted route-proof cells whose Pi child ended cleanly
            # (`agent_end`, `stop`, a landed mutation) after a `forward`
            # write failure came back ATTEMPT_FAILED with a 0-byte patch:
            # `_run`'s `except OSError` treated a dead telemetry pipe the
            # same as Pi itself failing and returned before `git diff` ever
            # ran. `_ForwardSinkFailed` carries the exit code `process.wait`
            # already returned so `_run` can still judge the attempt by
            # Pi's own outcome. See
            # test_a_broken_forward_sink_does_not_discard_a_clean_pi_exit.
            #
            # A write can also fail *transiently* with EAGAIN/EWOULDBLOCK
            # (`_write_retrying_backpressure`'s docstring has the full
            # mechanism) even though the reader is alive and draining --
            # that must be retried, not treated as a dead sink, or a live
            # counter goes blind for the rest of the run while the receipt
            # still reports it as trustworthy. See
            # test_a_flaky_forward_recovers_and_receives_every_byte_in_order
            # and test_a_partial_forward_write_is_completed_byte_for_byte.
            assert process.stdout is not None
            sinks = {"transcript": transcript, "forward": forward}
            try:
                for line in process.stdout:
                    for name in [name for name in sinks if name not in sink_errors]:
                        try:
                            # M5: per write, not per run -- this deadline bounds
                            # only this one line's write+flush retrying. The
                            # total bound on a stalled sink across the whole
                            # attempt is deliver's own command deadline, not
                            # this one.
                            deadline = time.monotonic() + FORWARD_WRITE_BOUND_SECONDS
                            _write_retrying_backpressure(sinks[name], line, deadline=deadline)
                            _flush_retrying_backpressure(sinks[name], deadline=deadline)
                        except (OSError, ValueError) as exc:
                            sink_errors[name] = exc
            except BaseException as exc:  # noqa: BLE001 - surfaced by run(), not swallowed
                sink_errors.setdefault("pipe", exc)

        reader = threading.Thread(target=pump, name="satyrn-attempt-pi-pump", daemon=True)
        reader.start()
        try:
            exit_code = process.wait()
        finally:
            # Join before returning: the pipe's write end can outlive
            # `process.wait()` by a few scheduler ticks, and the transcript
            # must be complete before `_run` flushes/closes it.
            reader.join()
            self._process = None
            if process.stdout is not None:
                process.stdout.close()
        for name in ("pipe", "transcript", "forward"):
            if name in sink_errors:
                exc = sink_errors[name]
                message = f"cannot {'read Pi output' if name == 'pipe' else 'write Pi output to ' + name}: {exc}"
                if name == "forward":
                    raise _ForwardSinkFailed(message, exit_code=exit_code) from exc
                raise OSError(message) from exc
        return exit_code


BASH_BOUND_SECONDS = 120  # pinned to packages/engine/bounds.ts DEFAULT_TIMEOUT_SECONDS by tests/test_bounds_pin.py

#: How many paths a prompt list names before it collapses to a count
#: (design §5.2). The Engine prompt was 3 to 4 times Baseline's, almost all
#: of it the tracked test files enumerated twice -- once under a writable
#: pattern's "(existing: ...)" and once in the carried block. Eight is
#: enough to name a real task's files and short enough that a self-hosted
#: base's hundreds collapse.
PROMPT_LIST_CAP = 8


def build_prompt(contract: Contract, existing: Sequence[str], tracked: Sequence[str] = ()) -> str:
    """Build the E5 handoff prompt: every fact inline, nothing pointed at.

    ``existing`` is the writable-pattern-matched tracked *regular* files
    (what ``AttemptContext.revisions`` holds); ``tracked`` is every
    writable-pattern-matched tracked path regardless of regularity. R14:
    `_prepare` refuses before this ever runs when an exact pattern names a
    tracked symlink or directory, but the label here must not lie even in
    isolation, so an exact pattern present in ``tracked`` but absent from
    ``existing`` is never called "(new file)".
    """

    def writable_line(pattern: str) -> str:
        beneath = sorted(path for path in existing if fnmatch(path, pattern)
                         and path not in contract.preserve and path not in contract.checks)
        if len(beneath) > PROMPT_LIST_CAP:
            return f"- {pattern}  ({len(beneath)} existing files)"
        if beneath:
            return f"- {pattern}  (existing: {', '.join(beneath)})"
        is_exact = not any(char in pattern for char in "*?[")
        if is_exact and pattern not in tracked:
            return f"- {pattern}  (new file)"
        return f"- {pattern}"

    def block(title: str, items: Sequence[str]) -> str:
        if not items:
            return ""
        if len(items) > PROMPT_LIST_CAP:
            return f"{title}\n- {len(items)} files\n\n"
        return f"{title}\n" + "\n".join(f"- {item}" for item in items) + "\n\n"

    writable = (
        "Writable paths (edit and write are refused elsewhere; new files are allowed under these):\n"
        + "\n".join(writable_line(pattern) for pattern in contract.writable_paths)
        + "\n\n"
    )
    verify = (
        f'Verify with the self_test tool before finishing: it runs "{" ".join(contract.test_command)}"'
        f'{" (and the checks)" if contract.checks else ""} and returns failed test ids with their first '
        "assertion line. "
        if contract.test_command
        else ""
    )
    budget = (
        f"Budget: {contract.token_budget} output tokens and {contract.turn_budget} turns. "
        if contract.token_budget is not None and contract.turn_budget is not None
        else ""
    )
    return (
        f"Implement this bounded task:\n{contract.task}\n\n"
        + writable
        + block(
            "Tests carried from the accepted base; they are restored before every self-test, "
            "so edits to them never count:",
            contract.preserve,
        )
        + block("Developer checks that must pass:", contract.checks)
        + verify
        + f"Shell commands are bounded at {BASH_BOUND_SECONDS} seconds. "
        + budget
        + "Stop when the task is complete."
    )


def record_invocation(contract_path: Path, argv: tuple[str, ...]) -> None:
    """Record what determines the model's system prompt, beside the contract.

    pi assembles the system prompt from the argv, its own version and the cwd,
    and emits no system message into the transcript -- so the prompt itself
    cannot be recovered from a finished run. Everything that determines it can
    be, and this writes that beside the contract, in a directory the caller
    already retains.

    Only the argv: pi's version is already pinned in the caller's arm record and
    verified by its preflight, and probing it here would put a subprocess in
    the attempt path that the default test tier forbids outright.

    Never raises. Evidence capture is not a reason to lose a model attempt, so
    an unwritable path is dropped rather than propagated.
    """
    try:
        contract_path.with_suffix(".invocation.json").write_text(
            json.dumps({"argv": list(argv)}, indent=2)
            + "\n"
        )
    except OSError:
        return


def build_pi_command(
    engine_repo: Path,
    model: str,
    prompt: str,
    *,
    test_command: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Return the exact hermetic Pi child argv.

    `engine.ts`, `mutator.ts`, `scope.ts` and `bounds.ts` are always loaded;
    `--extension .../runner.ts` (the `self_test` tool, Ruling 1) is added
    only when the contract declares `test_command`, and `self_test` is then
    the only addition to `--tools` -- native `bash` is kept in both arms.
    """
    package = engine_repo / "packages" / "engine"
    extensions: tuple[str, ...] = (
        "--extension",
        os.fspath(package / "engine.ts"),
        "--extension",
        os.fspath(package / "mutator.ts"),
        "--extension",
        os.fspath(package / "scope.ts"),
        "--extension",
        os.fspath(package / "bounds.ts"),
    )
    if test_command:
        extensions += ("--extension", os.fspath(package / "runner.ts"))
    return (
        "pi",
        "--print",
        "--mode",
        "json",
        "--no-session",
        "--model",
        model,
        "--no-extensions",
        *extensions,
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        "--no-approve",
        "--tools",
        # `--tools` gates extension-registered tools too, not just pi's
        # built-ins: with `read,edit` the registered `bash` answered
        # "Tool bash not found" for every call the model made
        # (2026-09-06 smoke). The name must appear here or the tool does
        # not exist, however carefully it was registered.
        #
        # Ruling 1: the runner is registered as `self_test`, not `bash` --
        # native `bash` stays native and guard 4 (bounds.ts) bounds it
        # directly, so both native tools are always kept and `self_test` is
        # added only when a test command exists to run.
        "read,bash,edit,write,self_test" if test_command else "read,bash,edit,write",
        prompt,
    )


def attempt(
    repo: Path,
    contract_path: Path,
    model: str,
    *,
    environment: Mapping[str, str] | None = None,
    git_runner: GitRunner | None = None,
    pi_runner: PiRunner | None = None,
    stdout: BinaryIO | None = None,
    stderr: BinaryIO | None = None,
) -> AttemptResult:
    """Run one model attempt in ``repo`` and preserve requested artifacts."""
    root_candidate = Path(os.path.abspath(repo))
    contract_candidate = Path(os.path.abspath(contract_path))
    checked = check(root_candidate, contract_candidate)
    if checked.code is not ExitCode.OK:
        return AttemptResult(
            code=_CHECK_TO_ATTEMPT[checked.code],
            message=checked.message,
            model=model,
        )
    if checked.contract is None:  # pragma: no cover - CheckResult invariant
        raise AssertionError("successful check has no contract")

    env = _clean_environment(environment if environment is not None else os.environ)
    git = git_runner if git_runner is not None else SubprocessGitRunner()
    pi = pi_runner if pi_runner is not None else SubprocessPiRunner()
    output = stdout if stdout is not None else sys.stdout.buffer
    errors = stderr if stderr is not None else sys.stderr.buffer

    artifact_owner: list[_ArtifactDestination] = []
    temporary_parent: Path | None = None
    pending: AttemptResult | None = None
    active_exception: BaseException | None = None
    try:
        prepared = _prepare(root_candidate, checked.contract, model, env, git, artifact_owner)
        if isinstance(prepared, AttemptResult):
            pending = prepared
        else:
            root, base_commit, revisions, symbols, tracked_writable, carried, artifacts, engine_repo = prepared
            try:
                temporary_parent = Path(tempfile.mkdtemp(prefix=".satyrn-attempt-", dir=root.parent))
            except OSError as exc:
                pending = _failed(model, f"cannot create attempt temporary directory: {exc}")
            else:
                frozen_contract = temporary_parent / "contract.yaml"
                try:
                    _freeze_contract(contract_candidate, frozen_contract)
                    frozen_value = load_contract(frozen_contract)
                except (ContractError, OSError) as exc:
                    pending = _failed(model, f"cannot freeze contract: {exc}")
                else:
                    if frozen_value != checked.contract:
                        pending = _failed(model, "contract changed while the attempt was being prepared")
                    else:
                        context = AttemptContext(
                            repo=root,
                            contract=checked.contract,
                            frozen_contract=frozen_contract,
                            base_commit=base_commit,
                            revisions=revisions,
                            symbols=symbols,
                            tracked_writable=tracked_writable,
                            carried=carried,
                            model=model,
                            engine_repo=engine_repo,
                        )
                        pending = _run(context, artifacts, env, git, pi, output, errors, temporary_parent)
    except BaseException as exc:
        active_exception = exc
        raise
    finally:
        cleanup_exception: BaseException | None = None
        if temporary_parent is not None:
            try:
                shutil.rmtree(temporary_parent)
            except BaseException as cleanup_error:
                detail = (
                    f"cannot remove attempt temporary directory: {cleanup_error}; "
                    f"retained path: {temporary_parent}"
                )
                pending, cleanup_exception = _merge_attempt_cleanup(
                    model,
                    pending,
                    active_exception,
                    cleanup_exception,
                    cleanup_error,
                    detail,
                )
        try:
            _close_destinations(artifact_owner)
        except BaseException as cleanup_error:
            detail = f"cannot close artifact parent directory: {_exception_detail(cleanup_error)}"
            pending, cleanup_exception = _merge_attempt_cleanup(
                model,
                pending,
                active_exception,
                cleanup_exception,
                cleanup_error,
                detail,
            )
        if active_exception is None and cleanup_exception is not None:
            raise cleanup_exception
    if pending is None:  # pragma: no cover - pending/result invariant
        raise AssertionError("attempt produced no result")
    return pending


def _prepare(
    repo: Path,
    contract: Contract,
    model: str,
    environment: Mapping[str, str],
    git: GitRunner,
    artifact_owner: list[_ArtifactDestination],
) -> (
    tuple[
        Path,
        str,
        dict[str, str],
        dict[str, list[str]],
        tuple[str, ...],
        tuple[str, ...],
        AttemptArtifacts,
        Path,
    ]
    | AttemptResult
):
    try:
        root_result = git.run(repo, ("rev-parse", "--show-toplevel"), environment)
    except OSError as exc:
        return _failed(model, f"cannot start Git: {exc}")
    if root_result.returncode != 0:
        return _failed(model, _git_message("cannot resolve repository root", root_result))
    root = Path(os.fsdecode(root_result.stdout.removesuffix(b"\n")))
    try:
        same_root = os.path.samefile(repo, root)
    except OSError:
        same_root = False
    if not same_root:
        return _failed(model, "attempt must run at the Git working-tree root")

    try:
        head = git.run(root, ("rev-parse", "--verify", "HEAD^{commit}"), environment)
        status = git.run(
            root,
            (
                "--no-optional-locks",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ),
            environment,
        )
        listed = git.run(root, ("ls-files", "-z", "--cached"), environment)
        worktrees = git.run(root, ("worktree", "list", "--porcelain", "-z"), environment)
        git_dir = git.run(
            root,
            ("rev-parse", "--path-format=absolute", "--git-dir"),
            environment,
        )
        common_dir = git.run(
            root,
            ("rev-parse", "--path-format=absolute", "--git-common-dir"),
            environment,
        )
    except OSError as exc:
        return _failed(model, f"cannot inspect Git worktree: {exc}")
    if head.returncode != 0:
        return _failed(model, _git_message("repository has no commit at HEAD", head))
    if status.returncode != 0:
        return _failed(model, _git_message("cannot inspect repository status", status))
    if status.stdout:
        return _failed(model, "attempt requires a clean disposable worktree")
    if listed.returncode != 0:
        return _failed(model, _git_message("cannot enumerate tracked files", listed))
    if worktrees.returncode != 0:
        return _failed(model, _git_message("cannot enumerate registered worktrees", worktrees))
    if git_dir.returncode != 0 or common_dir.returncode != 0:
        failed = git_dir if git_dir.returncode != 0 else common_dir
        return _failed(model, _git_message("cannot resolve Git administrative directories", failed))

    revisions: dict[str, str] = {}
    symbols: dict[str, list[str]] = {}
    tracked_paths: list[str] = []
    matched_tracked: list[str] = []
    for raw_path in listed.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = os.fsdecode(raw_path)
        try:
            normalized = normalize_relative_path(path)
        except ValueError:
            continue
        tracked_paths.append(normalized)
        if not any(fnmatch(normalized, pattern) for pattern in contract.writable_paths):
            continue
        matched_tracked.append(normalized)
        try:
            content = _read_tracked_regular(root, normalized)
        except OSError as exc:
            return _failed(model, f"cannot inspect tracked writable file {normalized}: {exc}")
        if content is not None:
            revisions[normalized] = file_sha256(content)
            symbols[normalized] = _defined_symbols(content)

    # R14 (review of Ruling 3/12): an *exact* writable path (no `*`, `?` or
    # `[`) that is tracked but not a regular file -- a symlink, a directory,
    # or a submodule -- is refused before Pi ever starts, rather than left
    # for scope.ts/mutator.ts to merely exclude from revisions the way a
    # pattern-covered symlink still is. `_read_tracked_regular`'s own
    # `O_NOFOLLOW` chain already determined this without following the
    # symlink: `matched_tracked` holds every writable-matched tracked path,
    # `revisions` holds only the regular ones, and the difference for an
    # exact pattern names exactly this case.
    non_regular_exact = sorted(
        pattern
        for pattern in contract.writable_paths
        if not any(char in pattern for char in "*?[") and pattern in matched_tracked and pattern not in revisions
    )
    if non_regular_exact:
        unsafe_path = non_regular_exact[0]
        return _failed(
            model,
            f"writable path {unsafe_path} is tracked but is not a regular file "
            "(symlink or directory); name the file itself",
        )

    if not contract.writable_paths:
        # Ruling 12: a build task whose only writable path is a new file has
        # no revisions yet and must still run -- only a contract that names
        # no writable path at all can never have anything to write.
        return _failed(model, "contract names no writable path")
    carried = tuple(select_carried(contract, tracked_paths))

    forbidden_roots = _forbidden_artifact_roots(root, worktrees.stdout, git_dir.stdout, common_dir.stdout)
    if isinstance(forbidden_roots, str):
        return _failed(model, forbidden_roots)
    engine_repo_text = environment.get(ENGINE_REPO_ENV)
    try:
        if engine_repo_text:
            configured_engine_repo = Path(engine_repo_text)
            engine_repo = (
                configured_engine_repo if configured_engine_repo.is_absolute() else root / configured_engine_repo
            ).resolve()
        else:
            engine_repo = Path(__file__).resolve().parents[2]
    except (OSError, RuntimeError) as exc:
        return _failed(model, f"cannot resolve engine repository: {exc}")
    package = engine_repo / "packages" / "engine"
    required_extensions = ("engine.ts", "mutator.ts", "scope.ts", "bounds.ts")
    if contract.test_command:
        required_extensions += ("runner.ts",)
    if not all((package / name).is_file() for name in required_extensions):
        return _failed(model, f"engine package is unavailable under {engine_repo}")

    artifacts = _artifact_destinations(forbidden_roots, environment, artifact_owner)
    if isinstance(artifacts, str):
        return _failed(model, artifacts)

    return (
        root,
        head.stdout.strip().decode("ascii"),
        revisions,
        symbols,
        tuple(matched_tracked),
        carried,
        artifacts,
        engine_repo,
    )


def _read_tracked_regular(root: Path, path: str) -> bytes | None:
    """Read one regular tracked file without following any path symlink."""
    descriptors: list[int] = []
    active_exception: BaseException | None = None
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent_descriptor = _open_owned_descriptor(descriptors, root, directory_flags)
        components = path.split("/")
        for component in components[:-1]:
            parent_descriptor = _open_owned_descriptor(
                descriptors,
                component,
                directory_flags,
                dir_fd=parent_descriptor,
            )
        target_descriptor = _open_owned_descriptor(
            descriptors,
            components[-1],
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        if not stat.S_ISREG(os.fstat(target_descriptor).st_mode):
            return None
        content = bytearray()
        while chunk := os.read(target_descriptor, 64 * 1024):
            content.extend(chunk)
        return bytes(content)
    except OSError:
        return None
    except BaseException as exc:
        active_exception = exc
        raise
    finally:
        cleanup_exception: BaseException | None = None
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except BaseException as exc:
                if active_exception is not None:
                    active_exception.add_note(f"secondary descriptor cleanup failure: {exc}")
                elif cleanup_exception is None:
                    cleanup_exception = exc
                else:
                    cleanup_exception.add_note(f"secondary descriptor cleanup failure: {exc}")
        if active_exception is None and cleanup_exception is not None:
            raise cleanup_exception


def _open_owned_descriptor(
    descriptors: list[int],
    path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    flags: int,
    mode: int = 0o777,
    *,
    dir_fd: int | None = None,
) -> int:
    """Open and transfer one descriptor without an unguarded handoff."""
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, mode, dir_fd=dir_fd)
        descriptors.append(descriptor)
    except BaseException as error:
        if descriptor is not None and descriptor not in descriptors:
            try:
                os.close(descriptor)
            except BaseException as cleanup_error:
                error.add_note(f"secondary descriptor cleanup failure: {cleanup_error}")
        raise
    return descriptor


def _run(
    context: AttemptContext,
    artifacts: AttemptArtifacts,
    environment: Mapping[str, str],
    git: GitRunner,
    pi: PiRunner,
    stdout: BinaryIO,
    stderr: BinaryIO,
    temporary_parent: Path,
) -> AttemptResult:
    mutation_context = json.dumps(
        {
            "version": 1,
            "repo": os.fspath(context.repo),
            "contract": os.fspath(context.frozen_contract),
            "revisions": context.revisions,
            "writable_paths": list(context.contract.writable_paths),
            "test_command": list(context.contract.test_command),
            "symbols": context.symbols,
            "carried": list(context.carried),
            "base_commit": context.base_commit,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    child_environment = dict(environment)
    child_environment[ENGINE_REPO_ENV] = os.fspath(context.engine_repo)
    child_environment[MUTATION_CONTEXT_ENV] = mutation_context
    prompt = build_prompt(context.contract, tuple(sorted(context.revisions)), context.tracked_writable)
    command = build_pi_command(
        context.engine_repo,
        context.model,
        prompt,
        test_command=context.contract.test_command,
    )
    # Written before pi starts: the assembled system prompt is a function of
    # this argv, pi's version and the cwd, and pi retains none of it.
    record_invocation(context.frozen_contract, command)
    transcript_destination = artifacts.transcript
    # E10: when a transcript destination was requested, its file was
    # already created exclusively during `_prepare` (see
    # `_artifact_destinations`); the pump thread inside `pi.run` (R18)
    # writes Pi's stdout into that descriptor as it arrives, so there is
    # nothing to publish afterward. The spool below exists only to give
    # Pi's pump somewhere to write when no destination was requested --
    # `stdout` is forwarded live either way, direct from the same pump.
    transcript_spool: Path | None = None
    forward_lost = False

    try:
        if transcript_destination is not None:
            content_descriptor = transcript_destination.take_content_descriptor()
            if content_descriptor is None:  # pragma: no cover - invariant
                raise AssertionError(f"transcript destination has no content descriptor: {transcript_destination.path}")
            try:
                transcript_output = os.fdopen(content_descriptor, "wb")
            except BaseException:
                os.close(content_descriptor)
                raise
        else:
            transcript_spool = temporary_parent / "transcript.jsonl"
            transcript_output = transcript_spool.open("xb")
        active_exception: BaseException | None = None
        try:
            # R18: `pi.run` pumps Pi's stdout to `transcript_output` and to
            # `stdout` (attempt's own stdout) line by line while Pi runs, so
            # a caller reading `stdout` live -- `deliver`'s
            # `_stream_implementer` when a budget is declared -- sees
            # `turn_start`/`message_end`/`entry_appended` lines as they
            # happen and can trip a budget mid-run instead of only after Pi
            # exits. There is nothing left to forward here afterward.
            command_exit = pi.run(
                command,
                context.repo,
                child_environment,
                transcript_output,
                stdout,
                stderr,
            )
            transcript_output.flush()
            os.fsync(transcript_output.fileno())
            stdout.flush()
        except BaseException as exc:
            active_exception = exc
            raise
        finally:
            try:
                transcript_output.close()
            except BaseException as cleanup_error:
                transcript_output_path = (
                    transcript_destination.path if transcript_spool is None else transcript_spool
                )
                detail = f"cannot close transcript {transcript_output_path}: {cleanup_error}"
                if active_exception is not None:
                    active_exception.add_note(f"secondary cleanup failure: {detail}")
                else:
                    _raise_cleanup_failure(cleanup_error, detail)
    except _ForwardSinkFailed as exc:
        # `forward` (the live tee to `deliver`'s budget counter) died, but
        # Pi itself already exited -- judge the attempt by that exit code,
        # same as a normal `pi.run()` return, instead of discarding a
        # completed, possibly successful attempt over a dead telemetry pipe.
        # `forward_lost` follows this AttemptResult all the way to the exit
        # code (see `AttemptResult.exit_code`) so a caller counting turns
        # from that same dead stream (`deliver`) can learn its count is no
        # longer trustworthy for the rest of the run.
        command_exit = exc.exit_code
        forward_lost = True
    except OSError as exc:
        return _failed(context.model, f"cannot run Pi: {_exception_detail(exc)}")

    try:
        patch = git.run(
            context.repo,
            (
                "diff",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                context.base_commit,
                "--",
            ),
            environment,
        )
    except OSError as exc:
        return _failed(
            context.model,
            f"cannot start Git diff: {exc}",
            command_exit=command_exit,
            forward_lost=forward_lost,
        )
    if patch.returncode != 0:
        return _failed(
            context.model,
            _git_message("cannot produce attempt patch", patch),
            command_exit=command_exit,
            forward_lost=forward_lost,
        )
    if patch.stdout and artifacts.patch is not None:
        try:
            _publish_bytes(patch.stdout, artifacts.patch)
        except (OSError, ValueError) as exc:
            return _failed(
                context.model,
                f"cannot publish patch: {_exception_detail(exc)}",
                command_exit=command_exit,
                forward_lost=forward_lost,
            )

    if command_exit != 0:
        return _failed(
            context.model,
            f"Pi exited with status {command_exit}",
            command_exit=command_exit,
            forward_lost=forward_lost,
        )
    return AttemptResult(AttemptCode.OK, model=context.model, command_exit=0, forward_lost=forward_lost)


def _clean_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = dict(source)
    for name in _GitRoutingVariable:
        environment.pop(name, None)
    virtual_environment = environment.pop("VIRTUAL_ENV", None)
    if virtual_environment:
        environment["PATH"] = os.pathsep.join(
            entry
            for entry in environment.get("PATH", "").split(os.pathsep)
            if entry and not Path(entry).is_relative_to(virtual_environment)
        )
    environment.pop("SSH_AUTH_SOCK", None)
    environment.pop("GIT_AUTHOR_DATE", None)
    environment.pop("GIT_COMMITTER_DATE", None)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_GRAFT_FILE"] = os.devnull
    return environment


def _forbidden_artifact_roots(
    repo: Path,
    worktree_output: bytes,
    git_dir_output: bytes,
    common_dir_output: bytes,
) -> tuple[tuple[Path, _FileIdentity], ...] | str:
    paths: list[Path] = []
    for field in worktree_output.split(b"\0"):
        if field.startswith(b"worktree "):
            paths.append(_absolute_git_path(repo, field.removeprefix(b"worktree ")))
    if not paths:
        return "Git reported no registered worktrees"
    for label, output in (("Git directory", git_dir_output), ("Git common directory", common_dir_output)):
        value = output.removesuffix(b"\n")
        if not value or b"\0" in value:
            return f"{label} has an invalid path"
        paths.append(_absolute_git_path(repo, value))

    roots: list[tuple[Path, _FileIdentity]] = []
    for path in paths:
        try:
            identity = _identity(path.stat())
        except (OSError, ValueError) as exc:
            return f"cannot inspect protected Git path {path}: {exc}"
        if all(identity != existing for _, existing in roots):
            roots.append((path, identity))
    return tuple(roots)


def _absolute_git_path(repo: Path, value: bytes) -> Path:
    path = Path(os.fsdecode(value))
    return path if path.is_absolute() else repo / path


def _artifact_destinations(
    forbidden_roots: Sequence[tuple[Path, _FileIdentity]],
    environment: Mapping[str, str],
    owner: list[_ArtifactDestination] | None = None,
) -> AttemptArtifacts | str:
    patch = _artifact_path(environment.get(PATCH_ENV))
    transcript = _artifact_path(environment.get(TRANSCRIPT_ENV))
    candidates: dict[_ArtifactKind, tuple[Path, _FileIdentity] | None] = {
        _ArtifactKind.PATCH: None,
        _ArtifactKind.TRANSCRIPT: None,
    }
    for label, candidate in ((_ArtifactKind.PATCH, patch), (_ArtifactKind.TRANSCRIPT, transcript)):
        if candidate is None:
            continue
        parent = candidate.parent
        try:
            parent_status = parent.stat(follow_symlinks=False)
        except (OSError, ValueError):
            return f"{label} artifact parent must be a real directory: {parent}"
        if not stat.S_ISDIR(parent_status.st_mode):
            return f"{label} artifact parent must be a real directory: {parent}"
        if os.path.lexists(candidate):
            return f"{label} artifact already exists: {candidate}"
        parent_identity = _identity(parent_status)
        if _inside_protected_root(parent, forbidden_roots):
            return (
                f"{label} artifact must be outside the repository, every registered worktree, "
                f"and Git administrative directory: {candidate}"
            )
        candidates[label] = candidate, parent_identity

    patch_candidate = candidates[_ArtifactKind.PATCH]
    transcript_candidate = candidates[_ArtifactKind.TRANSCRIPT]
    if (
        patch_candidate is not None
        and transcript_candidate is not None
        and patch_candidate[1] == transcript_candidate[1]
        and patch_candidate[0].name.casefold() == transcript_candidate[0].name.casefold()
    ):
        return "patch and transcript artifact paths must be different"

    destinations: dict[_ArtifactKind, _ArtifactDestination | None] = {
        _ArtifactKind.PATCH: None,
        _ArtifactKind.TRANSCRIPT: None,
    }
    opened = owner if owner is not None else []
    try:
        for label, candidate in candidates.items():
            if candidate is None:
                continue
            path, expected_identity = candidate
            destination = _open_artifact_destination(path, expected_identity, opened)
            descriptor = destination.descriptor()
            if _identity(os.fstat(descriptor)) != expected_identity:
                raise OSError(f"{label} artifact parent changed during preparation: {path.parent}")
            if label is _ArtifactKind.TRANSCRIPT:
                # E10: create the transcript exclusively now, through the
                # already-pinned parent, instead of only checking for it.
                # `O_EXCL` gives the same "nothing else created this path"
                # proof `os.link` gave after publication -- obtained here,
                # before the run, so Pi can write directly into it.
                try:
                    destination.content_descriptor = os.open(
                        path.name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=descriptor,
                    )
                except FileExistsError:
                    raise FileExistsError(f"{label} artifact already exists: {path}") from None
            else:
                try:
                    os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise FileExistsError(f"{label} artifact already exists: {path}")
            destinations[label] = destination
    except (OSError, ValueError) as exc:
        try:
            _close_destinations(opened)
        except BaseException as cleanup_error:
            if isinstance(cleanup_error, OSError):
                return f"cannot prepare artifact destination: {exc}; {_exception_detail(cleanup_error)}"
            cleanup_error.add_note(f"artifact preparation also failed: {exc}")
            raise
        return f"cannot prepare artifact destination: {exc}"
    except BaseException as exc:
        try:
            _close_destinations(opened)
        except BaseException as cleanup_error:
            exc.add_note(f"secondary cleanup failure: {_exception_detail(cleanup_error)}")
        raise
    return AttemptArtifacts(
        patch=destinations[_ArtifactKind.PATCH],
        transcript=destinations[_ArtifactKind.TRANSCRIPT],
    )


def _open_artifact_destination(
    path: Path,
    expected_identity: _FileIdentity,
    opened: list[_ArtifactDestination],
) -> _ArtifactDestination:
    """Open and transfer one artifact parent without an ownership gap."""
    descriptor: int | None = None
    destination: _ArtifactDestination | None = None
    try:
        descriptor = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        destination = _ArtifactDestination(path, expected_identity, descriptor)
        opened.append(destination)
    except BaseException as error:
        transferred = destination is not None and any(owned is destination for owned in opened)
        if descriptor is not None and not transferred:
            try:
                os.close(descriptor)
            except BaseException as cleanup_error:
                error.add_note(f"secondary descriptor cleanup failure: {cleanup_error}")
        raise
    if destination is None:  # pragma: no cover - successful construction invariant
        raise AssertionError("artifact destination was not constructed")
    return destination


def _artifact_path(value: str | None) -> Path | None:
    return None if value is None else Path(os.path.abspath(value))


def _identity(status: os.stat_result) -> _FileIdentity:
    return _FileIdentity(status.st_dev, status.st_ino)


def _inside_protected_root(
    parent: Path,
    forbidden_roots: Sequence[tuple[Path, _FileIdentity]],
) -> bool:
    try:
        canonical_parent = parent.resolve(strict=True)
        ancestors = (canonical_parent, *canonical_parent.parents)
        identities = {_identity(ancestor.stat()) for ancestor in ancestors}
    except (OSError, RuntimeError, ValueError):
        return True
    return any(identity in identities for _, identity in forbidden_roots)


def _close_destinations(destinations: Sequence[_ArtifactDestination]) -> None:
    primary: BaseException | None = None
    for destination in reversed(destinations):
        if (content_descriptor := destination.take_content_descriptor()) is not None:
            # E10: a prepared-but-never-run transcript (e.g. a later
            # preparation step failed) still owns its exclusively-created
            # content descriptor; close it here rather than leak it.
            try:
                os.close(content_descriptor)
            except BaseException as cleanup_error:
                detail = (
                    f"cannot close transcript artifact descriptor {destination.path}: {cleanup_error}; "
                    "descriptor ownership released without retry"
                )
                if primary is None:
                    primary = cleanup_error
                    primary.add_note(detail)
                else:
                    primary.add_note(f"secondary cleanup failure: {detail}")
        if (descriptor := destination.take_descriptor()) is None:
            continue
        try:
            os.close(descriptor)
        except BaseException as cleanup_error:
            detail = (
                f"cannot close artifact parent directory {destination.path.parent}: {cleanup_error}; "
                "descriptor ownership released without retry"
            )
            if primary is None:
                primary = cleanup_error
                primary.add_note(detail)
            else:
                primary.add_note(f"secondary cleanup failure: {detail}")
    if primary is not None:
        raise primary


def _merge_attempt_cleanup(
    model: str,
    pending: AttemptResult | None,
    active_exception: BaseException | None,
    cleanup_exception: BaseException | None,
    error: BaseException,
    detail: str,
) -> tuple[AttemptResult | None, BaseException | None]:
    """Apply cleanup precedence without hiding a primary exception."""
    if active_exception is not None:
        active_exception.add_note(f"secondary cleanup failure: {detail}")
    elif cleanup_exception is not None:
        cleanup_exception.add_note(f"secondary cleanup failure: {detail}")
    elif isinstance(error, OSError) and pending is not None:
        previous = f"; prior result {pending.code}: {pending.message}" if pending.message else ""
        pending = _failed(model, f"{detail}{previous}", command_exit=pending.command_exit)
    else:
        if pending is not None and pending.message:
            error.add_note(f"prior result {pending.code}: {pending.message}")
        error.add_note(detail)
        cleanup_exception = error
    return pending, cleanup_exception


def _publish_bytes(content: bytes, destination: _ArtifactDestination) -> None:
    def write(output: BinaryIO) -> None:
        output.write(content)

    _publish(destination, write)


def _publish(destination: _ArtifactDestination, write: _ArtifactWriter) -> None:
    parent_descriptor = destination.descriptor()
    actual_identity = _identity(os.fstat(parent_descriptor))
    if actual_identity != destination.parent_identity:  # pragma: no cover - open-FD invariant
        raise OSError(f"artifact parent descriptor changed before publication: {destination.path.parent}")
    temporary_name, temporary_descriptor = _create_artifact_temporary(parent_descriptor)
    publication_exception: BaseException | None = None
    try:
        try:
            _write_artifact_temporary(temporary_descriptor, write)
        except BaseException as exc:
            publication_exception = exc
            raise
        os.link(
            temporary_name,
            destination.path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except BaseException as exc:
        publication_exception = exc
        raise
    finally:
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except BaseException as cleanup_error:
            retained = destination.path.parent / temporary_name
            detail = f"cannot remove artifact temporary: {cleanup_error}; retained path: {retained}"
            if publication_exception is not None:
                publication_exception.add_note(f"secondary cleanup failure: {detail}")
            else:
                _raise_cleanup_failure(cleanup_error, detail)


def _freeze_contract(source: Path, destination: Path) -> None:
    destination.write_bytes(source.read_bytes())


class _ArtifactWriter(Protocol):
    def __call__(self, output: BinaryIO) -> None: ...


def _write_artifact_temporary(descriptor: int, write: _ArtifactWriter) -> None:
    try:
        output = os.fdopen(descriptor, "wb")
    except BaseException as open_error:
        try:
            os.close(descriptor)
        except BaseException as cleanup_error:
            open_error.add_note(f"secondary cleanup failure: cannot close artifact temporary: {cleanup_error}")
        raise
    active_exception: BaseException | None = None
    try:
        write(output)
        output.flush()
        os.fsync(output.fileno())
    except BaseException as exc:
        active_exception = exc
        raise
    finally:
        try:
            output.close()
        except BaseException as cleanup_error:
            detail = f"cannot close artifact temporary: {cleanup_error}"
            if active_exception is not None:
                active_exception.add_note(f"secondary cleanup failure: {detail}")
            else:
                _raise_cleanup_failure(cleanup_error, detail)


def _raise_cleanup_failure(error: BaseException, detail: str) -> Never:
    """Map ordinary cleanup I/O failures while preserving unexpected exceptions."""
    if isinstance(error, OSError):
        raise OSError(detail) from error
    error.add_note(detail)
    raise error


def _create_artifact_temporary(parent_descriptor: int) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _ in range(128):
        name = f".satyrn-attempt-{secrets.token_hex(12)}.tmp"
        try:
            return name, os.open(name, flags, 0o600, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
    raise FileExistsError("cannot allocate an exclusive artifact temporary")


def _exception_detail(error: BaseException) -> str:
    notes = getattr(error, "__notes__", ())
    return "; ".join((str(error), *notes))


def _failed(
    model: str,
    message: str,
    *,
    command_exit: int | None = None,
    forward_lost: bool = False,
) -> AttemptResult:
    return AttemptResult(
        AttemptCode.ATTEMPT_FAILED,
        message=message,
        model=model,
        command_exit=command_exit,
        forward_lost=forward_lost,
    )


def _git_message(prefix: str, result: GitResult) -> str:
    detail = result.stderr.strip().decode("utf-8", errors="replace")
    return f"{prefix}: {detail}" if detail else prefix
