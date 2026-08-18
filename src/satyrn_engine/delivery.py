"""E3 candidate delivery in a temporary detached Git worktree."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from codecs import getincrementaldecoder
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum, StrEnum, auto
from pathlib import Path
from typing import BinaryIO, Literal, Protocol, TypedDict

from .check import check
from .exits import ExitCode

DEFAULT_TIMEOUT = 30.0
RECEIPT_VERSION: Literal[1] = 1


class DeliveryOutcome(StrEnum):
    """Closed vocabulary for the high-level receipt result."""

    CANDIDATE_CREATED = "candidate-created"
    DISCARDED = "discarded"
    REFUSED = "refused"


class DeliveryCode(StrEnum):
    """Closed vocabulary for the authoritative delivery result."""

    OK = "OK"
    CONTRACT_UNREADABLE = "CONTRACT_UNREADABLE"
    CONTRACT_INVALID_YAML = "CONTRACT_INVALID_YAML"
    CONTRACT_MISSING_FIELD = "CONTRACT_MISSING_FIELD"
    REPO_UNAVAILABLE = "REPO_UNAVAILABLE"
    REPO_NOT_GIT = "REPO_NOT_GIT"
    REPO_DIRTY = "REPO_DIRTY"
    INVALID_CANDIDATE_ID = "INVALID_CANDIDATE_ID"
    CANDIDATE_EXISTS = "CANDIDATE_EXISTS"
    COMMAND_UNAVAILABLE = "COMMAND_UNAVAILABLE"
    COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
    COMMAND_FAILED = "COMMAND_FAILED"
    COMMAND_CHANGED_HEAD = "COMMAND_CHANGED_HEAD"
    NO_CHANGES = "NO_CHANGES"
    GIT_FAILED = "GIT_FAILED"
    CLEANUP_FAILED = "CLEANUP_FAILED"


_CODE_TO_OUTCOME: dict[DeliveryCode, DeliveryOutcome] = {
    DeliveryCode.OK: DeliveryOutcome.CANDIDATE_CREATED,
    DeliveryCode.CONTRACT_UNREADABLE: DeliveryOutcome.REFUSED,
    DeliveryCode.CONTRACT_INVALID_YAML: DeliveryOutcome.REFUSED,
    DeliveryCode.CONTRACT_MISSING_FIELD: DeliveryOutcome.REFUSED,
    DeliveryCode.REPO_UNAVAILABLE: DeliveryOutcome.REFUSED,
    DeliveryCode.REPO_NOT_GIT: DeliveryOutcome.REFUSED,
    DeliveryCode.REPO_DIRTY: DeliveryOutcome.REFUSED,
    DeliveryCode.INVALID_CANDIDATE_ID: DeliveryOutcome.REFUSED,
    DeliveryCode.CANDIDATE_EXISTS: DeliveryOutcome.REFUSED,
    DeliveryCode.COMMAND_UNAVAILABLE: DeliveryOutcome.REFUSED,
    DeliveryCode.COMMAND_TIMEOUT: DeliveryOutcome.DISCARDED,
    DeliveryCode.COMMAND_FAILED: DeliveryOutcome.DISCARDED,
    DeliveryCode.COMMAND_CHANGED_HEAD: DeliveryOutcome.DISCARDED,
    DeliveryCode.NO_CHANGES: DeliveryOutcome.DISCARDED,
    DeliveryCode.GIT_FAILED: DeliveryOutcome.REFUSED,
    DeliveryCode.CLEANUP_FAILED: DeliveryOutcome.REFUSED,
}

_CODE_TO_EXIT: dict[DeliveryCode, ExitCode] = {
    DeliveryCode.OK: ExitCode.OK,
    DeliveryCode.CONTRACT_UNREADABLE: ExitCode.CONTRACT_UNREADABLE,
    DeliveryCode.CONTRACT_INVALID_YAML: ExitCode.CONTRACT_INVALID_YAML,
    DeliveryCode.CONTRACT_MISSING_FIELD: ExitCode.CONTRACT_MISSING_FIELD,
    DeliveryCode.REPO_UNAVAILABLE: ExitCode.REPO_UNAVAILABLE,
    DeliveryCode.REPO_NOT_GIT: ExitCode.NO_CANDIDATE,
    DeliveryCode.REPO_DIRTY: ExitCode.NO_CANDIDATE,
    DeliveryCode.INVALID_CANDIDATE_ID: ExitCode.NO_CANDIDATE,
    DeliveryCode.CANDIDATE_EXISTS: ExitCode.NO_CANDIDATE,
    DeliveryCode.COMMAND_UNAVAILABLE: ExitCode.NO_CANDIDATE,
    DeliveryCode.COMMAND_TIMEOUT: ExitCode.NO_CANDIDATE,
    DeliveryCode.COMMAND_FAILED: ExitCode.NO_CANDIDATE,
    DeliveryCode.COMMAND_CHANGED_HEAD: ExitCode.NO_CANDIDATE,
    DeliveryCode.NO_CHANGES: ExitCode.NO_CANDIDATE,
    DeliveryCode.GIT_FAILED: ExitCode.NO_CANDIDATE,
    DeliveryCode.CLEANUP_FAILED: ExitCode.NO_CANDIDATE,
}

_CHECK_REFUSAL_TO_DELIVERY_CODE: dict[ExitCode, DeliveryCode] = {
    ExitCode.CONTRACT_UNREADABLE: DeliveryCode.CONTRACT_UNREADABLE,
    ExitCode.CONTRACT_INVALID_YAML: DeliveryCode.CONTRACT_INVALID_YAML,
    ExitCode.CONTRACT_MISSING_FIELD: DeliveryCode.CONTRACT_MISSING_FIELD,
    ExitCode.REPO_UNAVAILABLE: DeliveryCode.REPO_UNAVAILABLE,
}


class DeliveryPayload(TypedDict):
    """Stable JSON shape emitted for every accepted delivery operation."""

    version: Literal[1]
    outcome: DeliveryOutcome
    code: DeliveryCode
    message: str
    contract_id: str | None
    repository: str
    base_commit: str | None
    candidate_ref: str | None
    candidate_commit: str | None
    changed_paths: list[str] | None
    command_exit: int | None
    worktree_path: str | None


class _Registration(Enum):
    ABSENT_CONFIRMED = auto()
    MAY_EXIST = auto()
    PRESENT_CONFIRMED = auto()


class _CleanupGate(Enum):
    OPEN = auto()
    CLOSED = auto()


class _GroupState(Enum):
    GONE = auto()
    PRESENT = auto()
    UNKNOWN = auto()


class _Process(Protocol):
    pid: int
    returncode: int | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _GitResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class _DeliveryContext:
    repository: str
    root: Path
    environment: dict[str, str]
    contract_id: str
    base_commit: str
    candidate_ref: str


@dataclass(slots=True)
class _AttemptState:
    parent: Path
    worktree: Path
    registration: _Registration = _Registration.ABSENT_CONFIRMED
    parent_exists: bool = True
    cleanup_gate: _CleanupGate = _CleanupGate.OPEN
    process_detail: str | None = None

    @property
    def needs_cleanup(self) -> bool:
        return self.registration is not _Registration.ABSENT_CONFIRMED or self.parent_exists

    def observe_registration(self, registered: bool | None) -> None:
        self.registration = (
            _Registration.PRESENT_CONFIRMED
            if registered is True
            else _Registration.ABSENT_CONFIRMED
            if registered is False
            else _Registration.MAY_EXIST
        )


@dataclass(frozen=True, slots=True)
class _TeardownResult:
    group: _GroupState
    child_reaped: bool
    detail: str | None = None

    @property
    def cleanup_safe(self) -> bool:
        return self.group is _GroupState.GONE and self.child_reaped


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """One stable machine-readable result from an accepted delivery operation."""

    code: DeliveryCode
    message: str
    contract_id: str | None
    repository: str
    base_commit: str | None
    candidate_ref: str | None
    candidate_commit: str | None
    changed_paths: tuple[str, ...] | None
    command_exit: int | None
    worktree_path: str | None
    version: Literal[1] = RECEIPT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.code, DeliveryCode):
            raise TypeError("code must be a DeliveryCode")

    @property
    def outcome(self) -> DeliveryOutcome:
        """Derive the coarse result from the authoritative delivery code."""
        return _CODE_TO_OUTCOME[self.code]

    @property
    def exit_code(self) -> ExitCode:
        """Return the stable shell code without expanding it for every cause."""
        return _CODE_TO_EXIT[self.code]

    def payload(self) -> DeliveryPayload:
        """Return all receipt fields in their stable serialization order."""
        return {
            "version": self.version,
            "outcome": self.outcome,
            "code": self.code,
            "message": self.message,
            "contract_id": self.contract_id,
            "repository": self.repository,
            "base_commit": self.base_commit,
            "candidate_ref": self.candidate_ref,
            "candidate_commit": self.candidate_commit,
            "changed_paths": None if self.changed_paths is None else list(self.changed_paths),
            "command_exit": self.command_exit,
            "worktree_path": self.worktree_path,
        }

    def render(self) -> str:
        """Render exactly one compact UTF-8 JSON object followed by a newline."""
        return json.dumps(self.payload(), ensure_ascii=True, separators=(",", ":")) + "\n"


def deliver(
    repo: Path,
    contract_path: Path,
    command: tuple[str, ...],
    timeout: float = DEFAULT_TIMEOUT,
) -> DeliveryReceipt:
    """Run one command outside the caller checkout and publish one candidate."""
    repository = os.path.abspath(repo)
    checked = check(repo, contract_path)
    if checked.code is not ExitCode.OK:
        return _receipt(
            repository,
            _CHECK_REFUSAL_TO_DELIVERY_CODE[checked.code],
            checked.message,
            contract_id=None if checked.contract is None else checked.contract.id,
        )
    if checked.contract is None:  # pragma: no cover - CheckResult invariant
        raise AssertionError("successful check has no contract")

    prepared = _preflight(repository, checked.contract.id)
    if isinstance(prepared, DeliveryReceipt):
        return prepared
    return _attempt(prepared, command, timeout)


def _preflight(repository: str, contract_id: str) -> _DeliveryContext | DeliveryReceipt:
    environment_result = _sanitized_environment(repository)
    if isinstance(environment_result, str):
        return _receipt(repository, DeliveryCode.GIT_FAILED, environment_result, contract_id=contract_id)
    environment = environment_result

    root_result = _git(Path(repository), environment, "rev-parse", "--show-toplevel")
    if root_result.returncode != 0:
        return _receipt(
            repository,
            DeliveryCode.REPO_NOT_GIT,
            _git_message("cannot resolve repository root", root_result),
            contract_id=contract_id,
        )
    root = Path(os.fsdecode(root_result.stdout.removesuffix(b"\n")))
    try:
        is_root = os.path.samefile(repository, root)
    except OSError:
        is_root = False
    if not is_root:
        return _receipt(
            repository,
            DeliveryCode.REPO_NOT_GIT,
            "repo must name the Git working-tree root",
            contract_id=contract_id,
        )

    head_result = _git(root, environment, "rev-parse", "--verify", "HEAD^{commit}")
    if head_result.returncode != 0:
        return _receipt(
            repository,
            DeliveryCode.REPO_NOT_GIT,
            _git_message("repository has no commit at HEAD", head_result),
            contract_id=contract_id,
        )
    base_commit = head_result.stdout.strip().decode("ascii")

    status_result = _git(
        root,
        environment,
        "--no-optional-locks",
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if status_result.returncode != 0:
        return _receipt(
            repository,
            DeliveryCode.GIT_FAILED,
            _git_message("cannot inspect repository status", status_result),
            contract_id=contract_id,
            base_commit=base_commit,
        )
    if status_result.stdout:
        return _receipt(
            repository,
            DeliveryCode.REPO_DIRTY,
            "repository has tracked or untracked changes",
            contract_id=contract_id,
            base_commit=base_commit,
        )

    try:
        encoded_id = contract_id.encode("utf-8")
    except UnicodeEncodeError:
        encoded_id = b"\0"
    if "/" in contract_id or b"\0" in encoded_id:
        return _receipt(
            repository,
            DeliveryCode.INVALID_CANDIDATE_ID,
            "contract id must be exactly one Git ref component",
            contract_id=contract_id,
            base_commit=base_commit,
        )
    candidate_ref = f"refs/satyrn/candidates/{contract_id}/head"
    ref_format = _git(root, environment, "check-ref-format", candidate_ref)
    if ref_format.returncode != 0:
        return _receipt(
            repository,
            DeliveryCode.INVALID_CANDIDATE_ID,
            "contract id does not form a valid Git ref",
            contract_id=contract_id,
            base_commit=base_commit,
        )

    existing = _ref_exists(root, environment, candidate_ref)
    if existing is None:
        return _receipt(
            repository,
            DeliveryCode.GIT_FAILED,
            "cannot inspect candidate ref",
            contract_id=contract_id,
            base_commit=base_commit,
            candidate_ref=candidate_ref,
        )
    if existing:
        return _receipt(
            repository,
            DeliveryCode.CANDIDATE_EXISTS,
            "candidate ref already exists",
            contract_id=contract_id,
            base_commit=base_commit,
            candidate_ref=candidate_ref,
        )
    return _DeliveryContext(
        repository=repository,
        root=root,
        environment=environment,
        contract_id=contract_id,
        base_commit=base_commit,
        candidate_ref=candidate_ref,
    )


def _attempt(context: _DeliveryContext, command: tuple[str, ...], timeout: float) -> DeliveryReceipt:
    temporary_parent_result = _temporary_parent(context)
    if isinstance(temporary_parent_result, str):
        return _context_receipt(context, DeliveryCode.GIT_FAILED, temporary_parent_result)
    temporary_parent = temporary_parent_result
    worktree = temporary_parent / "worktree"
    state = _AttemptState(parent=temporary_parent, worktree=worktree)
    pending: DeliveryReceipt | None = None
    retain_failed_cleanup = False
    try:
        state.registration = _Registration.MAY_EXIST
        added = _git(
            context.root,
            context.environment,
            "worktree",
            "add",
            "--detach",
            os.fspath(worktree),
            context.base_commit,
        )
        registration = _worktree_registered(context, worktree)
        state.observe_registration(registration)
        if added.returncode != 0 or registration is not True:
            message = (
                _git_message("cannot add isolated worktree", added)
                if added.returncode != 0
                else "cannot confirm that Git registered the isolated worktree"
            )
            pending = _context_receipt(
                context,
                DeliveryCode.GIT_FAILED,
                message,
            )
        else:
            pending = _run_and_commit(context, state, command, timeout)
        if (cleanup := _cleanup_attempt(context, state)) is not None:
            if pending is None:  # pragma: no cover - lifecycle invariant
                raise AssertionError("cleanup ran before delivery produced a result")
            cleanup_message, retained_path = cleanup
            retain_failed_cleanup = True
            return _context_receipt(
                context,
                DeliveryCode.CLEANUP_FAILED,
                f"cleanup failed after pending result {pending.code}: {cleanup_message}",
                candidate_commit=pending.candidate_commit,
                changed_paths=pending.changed_paths,
                command_exit=pending.command_exit,
                worktree_path=os.fspath(retained_path),
            )

        if pending is None:  # pragma: no cover - lifecycle invariant
            raise AssertionError("delivery attempt produced no result")
        if pending.code is not DeliveryCode.OK:
            return pending
        if pending.candidate_commit is None:  # pragma: no cover - receipt invariant
            raise AssertionError("successful pending result has no commit")
        return _publish(context, pending)
    finally:
        if not retain_failed_cleanup and state.needs_cleanup:
            try:
                cleanup = _cleanup_attempt(context, state)
            except BaseException as cleanup_exception:
                _write_cleanup_diagnostic(
                    f"satyrn-engine: cleanup raised {cleanup_exception!r}; retained path: {_retained_path(state)}",
                )
            else:
                if cleanup is not None:
                    _, retained_path = cleanup
                    _write_cleanup_diagnostic(f"satyrn-engine: cleanup failed; retained path: {retained_path}")


def _run_and_commit(
    context: _DeliveryContext,
    state: _AttemptState,
    command: tuple[str, ...],
    timeout: float,
) -> DeliveryReceipt:
    try:
        output = tempfile.TemporaryFile(dir=state.worktree.parent)  # noqa: SIM115 - creation has a named refusal
    except OSError as exc:
        return _context_receipt(
            context,
            DeliveryCode.COMMAND_UNAVAILABLE,
            f"cannot create command output spool: {exc}",
        )
    try:
        state.cleanup_gate = _CleanupGate.CLOSED
        state.process_detail = "command creation did not complete"
        try:
            process = subprocess.Popen(
                command,
                cwd=state.worktree,
                env=context.environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            state.cleanup_gate = _CleanupGate.OPEN
            state.process_detail = None
            return _context_receipt(
                context,
                DeliveryCode.COMMAND_UNAVAILABLE,
                f"cannot start command: {exc}",
            )

        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _apply_teardown(state, _teardown_process_group(process))
            _write_attempt_output(output)
            return _context_receipt(
                context,
                DeliveryCode.COMMAND_TIMEOUT,
                f"command exceeded timeout of {timeout:g} seconds",
            )
        except BaseException:
            try:
                teardown = _teardown_process_group(process)
            except BaseException as teardown_exception:
                state.process_detail = f"process teardown raised {teardown_exception!r}"
            else:
                _apply_teardown(state, teardown)
            _write_attempt_output(output)
            raise
        state.cleanup_gate = _CleanupGate.OPEN
        state.process_detail = None
        _write_attempt_output(output)
        if process.returncode != 0:
            return _context_receipt(
                context,
                DeliveryCode.COMMAND_FAILED,
                f"command exited with status {process.returncode}",
                command_exit=process.returncode,
            )
    finally:
        with suppress(OSError, ValueError):
            output.close()

    head = _git(state.worktree, context.environment, "rev-parse", "--verify", "HEAD^{commit}")
    if head.returncode != 0:
        return _context_receipt(
            context,
            DeliveryCode.GIT_FAILED,
            _git_message("cannot inspect isolated worktree HEAD", head),
            command_exit=0,
        )
    symbolic_head = _git(state.worktree, context.environment, "symbolic-ref", "--quiet", "HEAD")
    if symbolic_head.returncode not in {0, 1}:
        return _context_receipt(
            context,
            DeliveryCode.GIT_FAILED,
            _git_message("cannot inspect isolated worktree HEAD attachment", symbolic_head),
            command_exit=0,
        )
    if head.stdout.strip() != context.base_commit.encode("ascii") or symbolic_head.returncode == 0:
        return _context_receipt(
            context,
            DeliveryCode.COMMAND_CHANGED_HEAD,
            "command changed the isolated worktree HEAD",
            command_exit=0,
        )

    added = _git(state.worktree, context.environment, "add", "-A")
    if added.returncode != 0:
        return _context_receipt(
            context,
            DeliveryCode.GIT_FAILED,
            _git_message("cannot stage candidate tree", added),
            command_exit=0,
        )
    tree = _git(state.worktree, context.environment, "write-tree")
    base_tree = _git(state.worktree, context.environment, "rev-parse", f"{context.base_commit}^{{tree}}")
    if tree.returncode != 0 or base_tree.returncode != 0:
        failed = tree if tree.returncode != 0 else base_tree
        return _context_receipt(
            context,
            DeliveryCode.GIT_FAILED,
            _git_message("cannot compare candidate tree", failed),
            command_exit=0,
        )
    if tree.stdout.strip() == base_tree.stdout.strip():
        return _context_receipt(
            context,
            DeliveryCode.NO_CHANGES,
            "command produced no changes",
            changed_paths=(),
            command_exit=0,
        )

    commit_environment = context.environment | {
        "GIT_AUTHOR_NAME": "satyrn-engine",
        "GIT_AUTHOR_EMAIL": "satyrn-engine@localhost",
        "GIT_COMMITTER_NAME": "satyrn-engine",
        "GIT_COMMITTER_EMAIL": "satyrn-engine@localhost",
    }
    message = f"candidate: {context.contract_id}\n\nbase: {context.base_commit}\n".encode()
    committed = _git(
        state.worktree,
        commit_environment,
        "-c",
        "commit.gpgSign=false",
        "commit-tree",
        tree.stdout.strip().decode("ascii"),
        "-p",
        context.base_commit,
        input_bytes=message,
    )
    if committed.returncode != 0:
        return _context_receipt(
            context,
            DeliveryCode.GIT_FAILED,
            _git_message("cannot create candidate commit", committed),
            command_exit=0,
        )
    candidate_commit = committed.stdout.strip().decode("ascii")
    changed = _git(
        state.worktree,
        context.environment,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        "-z",
        "--no-renames",
        "--no-ext-diff",
        context.base_commit,
        candidate_commit,
    )
    if changed.returncode != 0:
        return _context_receipt(
            context,
            DeliveryCode.GIT_FAILED,
            _git_message("cannot read candidate paths", changed),
            candidate_commit=candidate_commit,
            command_exit=0,
        )
    raw_paths = [path for path in changed.stdout.split(b"\0") if path]
    try:
        changed_paths = tuple(path.decode("utf-8") for path in sorted(raw_paths))
    except UnicodeDecodeError:
        return _context_receipt(
            context,
            DeliveryCode.GIT_FAILED,
            "candidate contains a path that is not valid UTF-8",
            candidate_commit=candidate_commit,
            command_exit=0,
        )
    return _context_receipt(
        context,
        DeliveryCode.OK,
        "candidate created",
        candidate_commit=candidate_commit,
        changed_paths=changed_paths,
        command_exit=0,
    )


def _publish(context: _DeliveryContext, pending: DeliveryReceipt) -> DeliveryReceipt:
    assert pending.candidate_commit is not None
    published = _git(
        context.root,
        context.environment,
        "update-ref",
        "--no-deref",
        context.candidate_ref,
        pending.candidate_commit,
        "",
    )
    if published.returncode == 0:
        return pending
    existing = _ref_exists(context.root, context.environment, context.candidate_ref)
    if existing:
        return _context_receipt(
            context,
            DeliveryCode.CANDIDATE_EXISTS,
            "candidate ref already exists",
            candidate_commit=pending.candidate_commit,
            changed_paths=pending.changed_paths,
            command_exit=pending.command_exit,
        )
    return _context_receipt(
        context,
        DeliveryCode.GIT_FAILED,
        _git_message("cannot publish candidate ref", published),
        candidate_commit=pending.candidate_commit,
        changed_paths=pending.changed_paths,
        command_exit=pending.command_exit,
    )


def _remove_worktree(context: _DeliveryContext, worktree: Path) -> str | None:
    removed = _git(context.root, context.environment, "worktree", "remove", "--force", os.fspath(worktree))
    registered = _worktree_registered(context, worktree)
    if registered is False:
        return None
    if removed.returncode != 0:
        return _git_message("git worktree remove failed", removed)
    if registered is None:
        return "cannot confirm that Git removed the worktree registration"
    return "Git still reports the worktree as registered"


def _cleanup_attempt(
    context: _DeliveryContext,
    state: _AttemptState,
) -> tuple[str, Path] | None:
    if state.cleanup_gate is _CleanupGate.CLOSED:
        detail = state.process_detail or "process teardown was not confirmed"
        return detail, state.worktree
    if state.registration is not _Registration.ABSENT_CONFIRMED:
        if (failure := _remove_worktree(context, state.worktree)) is not None:
            return failure, state.worktree
        state.registration = _Registration.ABSENT_CONFIRMED
    if state.parent_exists:
        try:
            shutil.rmtree(state.parent)
        except OSError as exc:
            return f"temporary directory removal failed: {exc}", state.parent
        state.parent_exists = False
    return None


def _retained_path(state: _AttemptState) -> Path:
    return (
        state.worktree
        if state.cleanup_gate is _CleanupGate.CLOSED
        or state.registration is not _Registration.ABSENT_CONFIRMED
        else state.parent
    )


def _worktree_registered(context: _DeliveryContext, worktree: Path) -> bool | None:
    paths = _worktree_paths(context)
    if paths is None:
        return None
    expected = os.path.realpath(worktree)
    return any(os.path.realpath(path) == expected for path in paths)


def _worktree_paths(context: _DeliveryContext) -> tuple[Path, ...] | None:
    listed = _git(context.root, context.environment, "worktree", "list", "--porcelain", "-z")
    if listed.returncode != 0:
        return None
    return tuple(
        Path(os.fsdecode(field.removeprefix(b"worktree ")))
        for field in listed.stdout.split(b"\0")
        if field.startswith(b"worktree ")
    )


def _temporary_parent(context: _DeliveryContext) -> Path | str:
    if (worktrees := _worktree_paths(context)) is None:
        return "cannot inspect linked worktrees before allocating isolation"
    resolved_worktrees = tuple(Path(os.path.realpath(path)) for path in worktrees)
    candidates = (Path(tempfile.gettempdir()), Path("/tmp"), Path("/var/tmp"))
    attempted: set[Path] = set()
    for candidate in candidates:
        resolved = Path(os.path.realpath(candidate))
        if resolved in attempted or not resolved.is_dir():
            continue
        attempted.add(resolved)
        if any(_is_within(resolved, worktree) for worktree in resolved_worktrees):
            continue
        try:
            parent = Path(tempfile.mkdtemp(prefix="satyrn-engine-", dir=resolved))
        except OSError:
            continue
        if not any(_is_within(parent, worktree) for worktree in resolved_worktrees):
            return parent
        shutil.rmtree(parent)
    return "cannot allocate an isolated directory outside repository worktrees"


def _is_within(path: Path, parent: Path) -> bool:
    return os.path.commonpath((os.path.realpath(path), os.path.realpath(parent))) == os.path.realpath(parent)


def _ref_exists(root: Path, environment: dict[str, str], ref: str) -> bool | None:
    symbolic = _git(root, environment, "symbolic-ref", "--quiet", ref)
    match symbolic.returncode:
        case 0:
            return True
        case 1 | 128:
            pass
        case _:
            return None
    result = _git(root, environment, "for-each-ref", "--format=%(refname)", ref)
    if result.returncode != 0:
        return None
    return ref.encode() in result.stdout.splitlines()


def _sanitized_environment(repository: str) -> dict[str, str] | str:
    probe_environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    probe_environment["GIT_TERMINAL_PROMPT"] = "0"
    result = _git(Path(repository), probe_environment, "rev-parse", "--local-env-vars")
    if result.returncode != 0:
        return _git_message("cannot discover Git local environment variables", result)
    names = {line.decode("ascii") for line in result.stdout.splitlines()}
    names.add("GIT_NAMESPACE")
    environment = {key: value for key, value in os.environ.items() if key not in names}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


def _git(
    cwd: Path,
    environment: dict[str, str],
    *args: str,
    input_bytes: bytes | None = None,
) -> _GitResult:
    git_environment = environment.copy()
    git_environment.pop("GIT_AUTHOR_DATE", None)
    git_environment.pop("GIT_COMMITTER_DATE", None)
    try:
        completed = subprocess.run(
            ("git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args),
            cwd=cwd,
            env=git_environment,
            input=input_bytes,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return _GitResult(127, b"", str(exc).encode(errors="replace"))
    return _GitResult(completed.returncode, completed.stdout, completed.stderr)


def _signal_process_group(process: _Process, process_signal: signal.Signals | int) -> tuple[_GroupState, str | None]:
    try:
        os.killpg(process.pid, process_signal)
    except ProcessLookupError:
        return _GroupState.GONE, None
    except OSError as exc:
        return _GroupState.UNKNOWN, f"cannot signal process group with {process_signal}: {exc}"
    return _GroupState.PRESENT, None


def _probe_process_group(process: _Process) -> tuple[_GroupState, str | None]:
    return _signal_process_group(process, 0)


def _reap_direct_child(process: _Process) -> tuple[bool, str | None]:
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except OSError as exc:
            return False, f"cannot kill direct child: {exc}"
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            return False, "direct child did not exit after SIGKILL"
        except OSError as exc:
            return False, f"cannot reap direct child: {exc}"
    except OSError as exc:
        return False, f"cannot reap direct child: {exc}"
    return True, None


def _teardown_process_group(process: _Process) -> _TeardownResult:
    details: list[str] = []
    group, detail = _signal_process_group(process, signal.SIGTERM)
    if detail is not None:
        details.append(detail)
    if group is _GroupState.PRESENT:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            process.poll()
            group, detail = _probe_process_group(process)
            if detail is not None:
                details.append(detail)
            if group is not _GroupState.PRESENT:
                break
            time.sleep(0.05)
    if group is not _GroupState.GONE:
        group, detail = _signal_process_group(process, signal.SIGKILL)
        if detail is not None:
            details.append(detail)

    child_reaped, detail = _reap_direct_child(process)
    if detail is not None:
        details.append(detail)
    group, detail = _probe_process_group(process)
    if detail is not None:
        details.append(detail)
    if group is _GroupState.PRESENT:
        details.append("process group still exists after SIGKILL")
    return _TeardownResult(group, child_reaped, "; ".join(details) or None)


def _apply_teardown(state: _AttemptState, result: _TeardownResult) -> None:
    state.cleanup_gate = _CleanupGate.OPEN if result.cleanup_safe else _CleanupGate.CLOSED
    state.process_detail = result.detail or (
        None if result.cleanup_safe else "process teardown could not be confirmed"
    )


def _write_cleanup_diagnostic(message: str) -> None:
    try:
        print(message, file=sys.stderr)
    except (OSError, ValueError):
        return


def _write_attempt_output(output: BinaryIO) -> None:
    try:
        output.seek(0)
        if hasattr(sys.stderr, "buffer"):
            while chunk := output.read(64 * 1024):
                sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()
            return
        decoder = getincrementaldecoder("utf-8")(errors="replace")
        while chunk := output.read(64 * 1024):
            sys.stderr.write(decoder.decode(chunk))
        sys.stderr.write(decoder.decode(b"", final=True))
        sys.stderr.flush()
    except (OSError, ValueError):
        return


def _git_message(prefix: str, result: _GitResult) -> str:
    return f"{prefix}: {detail}" if (detail := result.stderr.strip().decode("utf-8", errors="replace")) else prefix


def _context_receipt(
    context: _DeliveryContext,
    code: DeliveryCode,
    message: str,
    *,
    candidate_commit: str | None = None,
    changed_paths: tuple[str, ...] | None = None,
    command_exit: int | None = None,
    worktree_path: str | None = None,
) -> DeliveryReceipt:
    return _receipt(
        context.repository,
        code,
        message,
        contract_id=context.contract_id,
        base_commit=context.base_commit,
        candidate_ref=context.candidate_ref,
        candidate_commit=candidate_commit,
        changed_paths=changed_paths,
        command_exit=command_exit,
        worktree_path=worktree_path,
    )


def _receipt(
    repository: str,
    code: DeliveryCode,
    message: str,
    *,
    contract_id: str | None = None,
    base_commit: str | None = None,
    candidate_ref: str | None = None,
    candidate_commit: str | None = None,
    changed_paths: tuple[str, ...] | None = None,
    command_exit: int | None = None,
    worktree_path: str | None = None,
) -> DeliveryReceipt:
    return DeliveryReceipt(
        code=code,
        message=message,
        contract_id=contract_id,
        repository=repository,
        base_commit=base_commit,
        candidate_ref=candidate_ref,
        candidate_commit=candidate_commit,
        changed_paths=changed_paths,
        command_exit=command_exit,
        worktree_path=worktree_path,
    )
