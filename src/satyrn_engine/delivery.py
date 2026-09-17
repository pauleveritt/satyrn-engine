"""E3 candidate delivery in a temporary detached Git worktree."""

import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from codecs import getincrementaldecoder
from contextlib import suppress
from dataclasses import dataclass, replace
from enum import Enum, StrEnum, auto
from pathlib import Path
from typing import BinaryIO, Literal, Protocol, TypedDict

from .budget import GUARD_KINDS, Budget, BudgetState, BudgetUsage, TurnCounter, evaluate
from .check import check
from .contract import Contract
from .derive import size_refusal as _derive_size_refusal
from .exits import ExitCode
from .runner import INFRASTRUCTURE, select_carried, tail_output

DEFAULT_TIMEOUT = 30.0
RECEIPT_VERSION: Literal[1] = 1
_STREAM_POLL_SECONDS = 0.05
_NO_BUDGET = Budget()


class DeliveryOutcome(StrEnum):
    """Closed vocabulary for the high-level receipt result."""

    CANDIDATE_CREATED = "candidate-created"
    DISCARDED = "discarded"
    REFUSED = "refused"


class ValidationOutcome(StrEnum):
    """What the engine's own run of the contract's test command established.

    ``None`` never stands in for a missing verdict. ``NOT_REQUESTED`` means
    "no ``test_command`` declared", ``UNAVAILABLE`` means "declared but not
    runnable", and ``NOT_APPLICABLE`` means "no candidate was created, so
    there is nothing to validate". The three are distinct so a reader never
    has to guess which absence a value is standing in for.
    """

    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"        # declared, but not runnable
    NOT_REQUESTED = "not_requested"    # no test_command declared
    NOT_APPLICABLE = "not_applicable"  # no candidate was created


class DeliveryCode(StrEnum):
    """Closed vocabulary for the authoritative delivery result."""

    OK = "OK"
    TESTS_FAILED = "TESTS_FAILED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
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
    DeliveryCode.TESTS_FAILED: DeliveryOutcome.CANDIDATE_CREATED,
    DeliveryCode.BUDGET_EXHAUSTED: DeliveryOutcome.CANDIDATE_CREATED,
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
    DeliveryCode.TESTS_FAILED: ExitCode.TESTS_FAILED,
    DeliveryCode.BUDGET_EXHAUSTED: ExitCode.BUDGET_EXHAUSTED,
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
    validation: ValidationOutcome
    validation_exit: int | None
    validation_output: str | None
    validation_output_bytes: int | None
    worktree_path: str | None
    budget: BudgetPayload
    turns: int
    tool_calls: int
    tokens_in: int
    tokens_out: int
    guard_firings: dict[str, int]
    carried: dict[str, list[str]]
    size_refusal: str | None


class BudgetPayload(TypedDict):
    """The always-present budget accounting on every receipt payload."""

    state: str
    turns_used: int
    seconds_used: float
    turn_limit: int | None
    deadline_seconds: float | None
    token_limit: int | None
    tokens_used: int


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
    test_command: tuple[str, ...] = ()
    budget: Budget = Budget()
    contract: Contract | None = None
    size_refusal: str | None = None


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
class GuardFirings:
    """One guard-firing count per :data:`~satyrn_engine.budget.GUARD_KINDS`.

    Read from :class:`~satyrn_engine.budget.TurnCounter` -- the child's own
    ``entry_appended`` stream, never a file the model's shell can reach
    (Ruling 7)."""

    loop_broken: int = 0
    scope_refused: int = 0
    symbol_preserved: int = 0
    command_bounded: int = 0
    command_timed_out: int = 0
    self_test_redirected: int = 0
    self_test_enforced: int = 0

    @classmethod
    def from_counter(cls, counter: TurnCounter) -> GuardFirings:
        return cls(**{kind: counter.guard_firings[kind] for kind in GUARD_KINDS})

    def payload(self) -> dict[str, int]:
        return {kind: getattr(self, kind) for kind in GUARD_KINDS}


@dataclass(frozen=True, slots=True)
class Carried:
    """R9's carried-set accounting for one validation run.

    ``preserve``/``checks`` are the contract's own declared paths that were
    tracked at the base and therefore restored; ``infrastructure`` is every
    other restored path (tracked ``conftest.py`` files and tracked
    :data:`~satyrn_engine.runner.INFRASTRUCTURE`); ``absent`` is a declared
    ``preserve``/``checks`` path that was not tracked at the base, so
    nothing was restored for it; ``tampered`` is every carried path (or any
    infrastructure-shaped path) the candidate commit changed or added."""

    preserve: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    infrastructure: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()
    tampered: tuple[str, ...] = ()

    def payload(self) -> dict[str, list[str]]:
        return {name: list(getattr(self, name)) for name in ("preserve", "checks", "infrastructure", "absent", "tampered")}


_EMPTY_CONTRACT = Contract(id="", task="")
_NO_GUARD_FIRINGS = GuardFirings()
_NO_CARRIED = Carried()


def count_spool(spool: BinaryIO) -> TurnCounter:
    """Feed a finished spool (an anonymous temporary file) through the one
    counter (Ruling 13): used only when no budget was declared, since a
    declared budget already counted the same stream live."""
    counter = TurnCounter()
    spool.seek(0)
    for raw in spool:
        counter.feed(raw.decode("utf-8", errors="replace").rstrip("\n"))
    return counter


def _is_infrastructure(path: str) -> bool:
    return path == "conftest.py" or path.endswith("/conftest.py") or path in INFRASTRUCTURE


class _CarriedRestoreFailed(Exception):
    """R9 at validation: the carried set could not be read or restored from
    the accepted base, so validation must not silently run without it."""


def restore_carried_at(
    worktree: Path,
    environment: dict[str, str],
    base_commit: str,
    contract: Contract | None,
    changed_paths: tuple[str, ...],
) -> Carried:
    """Restore the carried set from the accepted base into the validation
    checkout; name what the candidate touched.

    Selection reuses :func:`~satyrn_engine.runner.select_carried` -- the one
    place the carried-set rule (``preserve``, ``checks``, then tracked
    ``conftest.py`` sorted, then tracked
    :data:`~satyrn_engine.runner.INFRASTRUCTURE`, deduplicated and filtered
    to what is tracked at ``base_commit``) is stated. Raises
    :class:`_CarriedRestoreFailed` when ``git ls-tree`` on ``base_commit``
    fails, or when restoring a non-empty carried set fails -- R9 applies at
    validation exactly as it does before every ``self_test`` (Ruling 9)."""
    listed = _git(worktree, environment, "ls-tree", "-r", "--name-only", base_commit)
    if listed.returncode != 0:
        raise _CarriedRestoreFailed(f"carried tests could not be read from base {base_commit}")
    tracked = set(listed.stdout.decode("utf-8", errors="replace").splitlines())
    declared = contract if contract is not None else _EMPTY_CONTRACT
    # `select_carried` is the one place ordering and dedup are decided (a
    # path declared in both `preserve` and `checks` appears once); `restore`
    # reuses its output rather than rebuilding it, so a duplicate here would
    # be restored -- and, downstream, run -- twice.
    selected = select_carried(declared, tracked)
    preserve_set = set(declared.preserve)
    checks_set = set(declared.checks)
    preserve = tuple(p for p in selected if p in preserve_set)
    checks = tuple(p for p in selected if p in checks_set and p not in preserve_set)
    infrastructure = tuple(p for p in selected if p not in preserve_set and p not in checks_set)
    absent = tuple(p for p in (*declared.preserve, *declared.checks) if p not in tracked)
    restore = selected
    if restore:
        checked_out = _git(worktree, environment, "checkout", base_commit, "--", *restore)
        if checked_out.returncode != 0:
            raise _CarriedRestoreFailed(f"carried tests could not be restored from base {base_commit}")
    carried_set = set(restore)
    tampered = tuple(sorted(p for p in changed_paths if p in carried_set or _is_infrastructure(p)))
    return Carried(preserve, checks, infrastructure, absent, tampered)


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
    validation: ValidationOutcome = ValidationOutcome.NOT_APPLICABLE
    validation_exit: int | None = None
    validation_output: str | None = None
    validation_output_bytes: int | None = None
    version: Literal[1] = RECEIPT_VERSION
    budget: Budget = Budget()
    budget_usage: BudgetUsage = BudgetUsage(BudgetState.NOT_DECLARED, 0, 0.0)
    turns: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    guard_firings: GuardFirings = GuardFirings()
    carried: Carried = Carried()
    size_refusal: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, DeliveryCode):
            raise TypeError("code must be a DeliveryCode")
        if not isinstance(self.validation, ValidationOutcome):
            raise TypeError("validation must be a ValidationOutcome")
        if not isinstance(self.budget, Budget):
            raise TypeError("budget must be a Budget")
        if not isinstance(self.budget_usage, BudgetUsage):
            raise TypeError("budget_usage must be a BudgetUsage")
        if not isinstance(self.guard_firings, GuardFirings):
            raise TypeError("guard_firings must be a GuardFirings")
        if not isinstance(self.carried, Carried):
            raise TypeError("carried must be a Carried")

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
            "validation": self.validation,
            "validation_exit": self.validation_exit,
            "validation_output": self.validation_output,
            "validation_output_bytes": self.validation_output_bytes,
            "worktree_path": self.worktree_path,
            "budget": {
                "state": self.budget_usage.state,
                "turns_used": self.budget_usage.turns_used,
                "seconds_used": self.budget_usage.seconds_used,
                "turn_limit": self.budget.turn_limit,
                "deadline_seconds": self.budget.deadline_seconds,
                "token_limit": self.budget.token_limit,
                "tokens_used": self.budget_usage.tokens_used,
            },
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "guard_firings": self.guard_firings.payload(),
            "carried": self.carried.payload(),
            "size_refusal": self.size_refusal,
        }

    def render(self) -> str:
        """Render exactly one compact UTF-8 JSON object followed by a newline."""
        return json.dumps(self.payload(), ensure_ascii=True, separators=(",", ":")) + "\n"


def _resolve_budget(
    contract: Contract | None,
    turn_limit: int | None,
    deadline_seconds: float | None,
    token_limit: int | None,
) -> Budget:
    """The contract's own budget fields, each overridden by an explicit
    argument when one is given. ``None`` means "not overridden", so the
    contract's value (or no limit, with no contract) applies."""
    if contract is None:
        return Budget(turn_limit, deadline_seconds, token_limit)
    return Budget(
        turn_limit if turn_limit is not None else contract.turn_budget,
        deadline_seconds if deadline_seconds is not None else contract.deadline_seconds,
        token_limit if token_limit is not None else contract.token_budget,
    )


def deliver(
    repo: Path,
    contract_path: Path,
    command: tuple[str, ...],
    timeout: float = DEFAULT_TIMEOUT,
    *,
    base: str | None = None,
    turn_limit: int | None = None,
    deadline_seconds: float | None = None,
    token_limit: int | None = None,
) -> DeliveryReceipt:
    """Run one command outside the caller checkout and publish one candidate.

    ``turn_limit``, ``deadline_seconds``, and ``token_limit`` override the
    contract's own ``turn_budget``/``deadline_seconds``/``token_budget``;
    ``None`` means "not overridden", so the contract value (or no budget)
    applies.
    """
    repository = os.path.abspath(repo)
    checked = check(repo, contract_path)
    contract = checked.contract
    budget = _resolve_budget(contract, turn_limit, deadline_seconds, token_limit)
    if checked.code is not ExitCode.OK:
        # Deliberate: no contract was read on this path, so no request text
        # exists to measure. `size_refusal` means "the size boundary fired
        # on this request" -- asserting a value here would claim a
        # measurement that was never made. Stays `None` (the default).
        return _receipt(
            repository,
            _CHECK_REFUSAL_TO_DELIVERY_CODE[checked.code],
            checked.message,
            contract_id=contract.id if contract is not None else None,
            budget=budget,
        )
    if contract is None:  # pragma: no cover - CheckResult invariant
        raise AssertionError("successful check has no contract")

    refusal = _derive_size_refusal(contract.task)
    prepared = _preflight(
        repository,
        contract.id,
        base,
        test_command=contract.test_command,
        budget=budget,
        contract=contract,
        size_refusal=refusal,
    )
    if isinstance(prepared, DeliveryReceipt):
        return prepared
    return _attempt(prepared, command, timeout)


def base_is_wellformed(base: str) -> bool:
    """Whether a caller-supplied base is usable as a commit-ish.

    Blank is **not** absent. Mapping it to ``HEAD`` would silently base a
    chained phase on the caller's head instead of its predecessor's commit,
    which is the one failure HP3 exists to prevent and the one that looks
    like success.
    """
    return bool(base.strip())


def _base_argv(base: str | None) -> tuple[str, ...]:
    """The argv that resolves a delivery's base commit.

    ``None`` reproduces today's behaviour exactly, so every existing caller
    and every recorded receipt is unaffected. A supplied base is peeled with
    ``^{commit}`` for the same reason ``HEAD`` is: branching from a tag object
    rather than its commit fails later, far from the cause.
    """
    return ("rev-parse", "--verify", f"{base or 'HEAD'}^{{commit}}")


def _preflight(
    repository: str,
    contract_id: str,
    base: str | None = None,
    *,
    test_command: tuple[str, ...] = (),
    budget: Budget = _NO_BUDGET,
    contract: Contract | None = None,
    size_refusal: str | None = None,
) -> _DeliveryContext | DeliveryReceipt:
    environment_result = _sanitized_environment(repository)
    if isinstance(environment_result, str):
        return _receipt(
            repository,
            DeliveryCode.GIT_FAILED,
            environment_result,
            contract_id=contract_id,
            budget=budget,
            size_refusal=size_refusal,
        )
    environment = environment_result

    root_result = _git(Path(repository), environment, "rev-parse", "--show-toplevel")
    if root_result.returncode != 0:
        return _receipt(
            repository,
            DeliveryCode.REPO_NOT_GIT,
            _git_message("cannot resolve repository root", root_result),
            contract_id=contract_id,
            budget=budget,
            size_refusal=size_refusal,
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
            budget=budget,
            size_refusal=size_refusal,
        )

    if base is not None and not base_is_wellformed(base):
        return _receipt(
            repository,
            DeliveryCode.REPO_NOT_GIT,
            f"base is blank: {base!r}",
            contract_id=contract_id,
            budget=budget,
            size_refusal=size_refusal,
        )
    head_result = _git(root, environment, *_base_argv(base))
    if head_result.returncode != 0:
        return _receipt(
            repository,
            DeliveryCode.REPO_NOT_GIT,
            _git_message(
                "repository has no commit at HEAD"
                if base is None
                else f"cannot resolve base {base!r}",
                head_result,
            ),
            contract_id=contract_id,
            budget=budget,
            size_refusal=size_refusal,
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
            budget=budget,
            size_refusal=size_refusal,
        )
    if status_result.stdout:
        return _receipt(
            repository,
            DeliveryCode.REPO_DIRTY,
            "repository has tracked or untracked changes",
            contract_id=contract_id,
            base_commit=base_commit,
            budget=budget,
            size_refusal=size_refusal,
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
            budget=budget,
            size_refusal=size_refusal,
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
            budget=budget,
            size_refusal=size_refusal,
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
            budget=budget,
            size_refusal=size_refusal,
        )
    if existing:
        return _receipt(
            repository,
            DeliveryCode.CANDIDATE_EXISTS,
            "candidate ref already exists",
            contract_id=contract_id,
            base_commit=base_commit,
            candidate_ref=candidate_ref,
            budget=budget,
            size_refusal=size_refusal,
        )
    return _DeliveryContext(
        repository=repository,
        root=root,
        environment=environment,
        contract_id=contract_id,
        base_commit=base_commit,
        candidate_ref=candidate_ref,
        test_command=test_command,
        budget=budget,
        contract=contract,
        size_refusal=size_refusal,
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
            if pending.code in (DeliveryCode.OK, DeliveryCode.BUDGET_EXHAUSTED):
                pending = _validate_candidate(context, state, pending, timeout)
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
                validation=pending.validation,
                validation_exit=pending.validation_exit,
                validation_output=pending.validation_output,
                validation_output_bytes=pending.validation_output_bytes,
                budget=pending.budget,
                budget_usage=pending.budget_usage,
                turns=pending.turns,
                tool_calls=pending.tool_calls,
                tokens_in=pending.tokens_in,
                tokens_out=pending.tokens_out,
                guard_firings=pending.guard_firings,
                carried=pending.carried,
            )

        if pending is None:  # pragma: no cover - lifecycle invariant
            raise AssertionError("delivery attempt produced no result")
        if pending.outcome is not DeliveryOutcome.CANDIDATE_CREATED:
            return pending
        if pending.candidate_commit is None:  # pragma: no cover - receipt invariant
            raise AssertionError("candidate-created pending result has no commit")
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


def _budget_exhaustion_detail(exhausted: BudgetState, usage: BudgetUsage) -> str:
    """The clause that names what tripped, for the exhaustion message.

    Token exhaustion is checked first by :func:`~satyrn_engine.budget.evaluate`,
    so ``exhausted`` is one of exactly these three states here."""
    return {
        BudgetState.TURN_EXHAUSTED: f"turn limit exhausted after {usage.turns_used} turns",
        BudgetState.TOKEN_EXHAUSTED: (
            f"token budget exhausted after {usage.tokens_used} output tokens"
        ),
        BudgetState.DEADLINE_EXHAUSTED: (
            f"deadline exhausted after {usage.seconds_used:.0f} seconds"
        ),
    }[exhausted]


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
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                command,
                cwd=state.worktree,
                env=context.environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if context.budget.declared else output,
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

        exhausted: BudgetState | None = None
        timed_out = False
        seconds_used = 0.0
        stream_counter: TurnCounter | None = None
        try:
            if context.budget.declared:
                stream = _stream_implementer(process, output, context.budget, timeout)
                exhausted = stream.exhausted
                timed_out = stream.command_timed_out
                stream_counter = stream.counter
                seconds_used = stream.seconds_used
            else:
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
        except BaseException:
            try:
                teardown = _teardown_process_group(process)
            except BaseException as teardown_exception:
                state.process_detail = f"process teardown raised {teardown_exception!r}"
            else:
                _apply_teardown(state, teardown)
            _write_attempt_output(output)
            raise

        if timed_out or exhausted is not None:
            _apply_teardown(state, _teardown_process_group(process))
        else:
            # No teardown ran, so the process exited on its own and cleanup is
            # safe. When teardown did run, `_apply_teardown` owns the gate: a
            # non-clean teardown must leave it CLOSED so `_cleanup_attempt`
            # withholds `git worktree remove --force` against a live group.
            state.cleanup_gate = _CleanupGate.OPEN
            state.process_detail = None
        # One counter either way (Ruling 13): a declared budget already ran
        # one live over the streamed output; with none declared, the spool
        # -- otherwise uninspected -- is fed through the same class here.
        counter = stream_counter if stream_counter is not None else count_spool(output)
        turns_used = counter.turns
        tokens_used = counter.tokens_out
        if exhausted is not None:
            usage = BudgetUsage(exhausted, turns_used, seconds_used, tokens_used=tokens_used)
        elif context.budget.declared:
            usage = BudgetUsage(BudgetState.WITHIN, turns_used, seconds_used, tokens_used=tokens_used)
        else:
            usage = BudgetUsage(BudgetState.NOT_DECLARED, turns_used, seconds_used, tokens_used=tokens_used)
        _write_attempt_output(output)
        if timed_out:
            return _context_receipt(
                context,
                DeliveryCode.COMMAND_TIMEOUT,
                f"command exceeded timeout of {timeout:g} seconds",
                budget=context.budget,
                budget_usage=usage,
                turns=counter.turns,
                tool_calls=counter.tool_calls,
                tokens_in=counter.tokens_in,
                tokens_out=counter.tokens_out,
                guard_firings=GuardFirings.from_counter(counter),
            )
        if exhausted is None and process.returncode != 0:
            return _context_receipt(
                context,
                DeliveryCode.COMMAND_FAILED,
                f"command exited with status {process.returncode}",
                command_exit=process.returncode,
                budget=context.budget,
                budget_usage=usage,
                turns=counter.turns,
                tool_calls=counter.tool_calls,
                tokens_in=counter.tokens_in,
                tokens_out=counter.tokens_out,
                guard_firings=GuardFirings.from_counter(counter),
            )
    finally:
        with suppress(OSError, ValueError):
            output.close()
        if process is not None and (stdout := getattr(process, "stdout", None)) is not None:
            with suppress(OSError, ValueError):
                stdout.close()

    head = _git(state.worktree, context.environment, "rev-parse", "--verify", "HEAD^{commit}")
    if head.returncode != 0:
        return _context_receipt(
            context,
            DeliveryCode.GIT_FAILED,
            _git_message("cannot inspect isolated worktree HEAD", head),
            command_exit=0,
            budget=context.budget,
            budget_usage=usage,
            turns=counter.turns,
            tool_calls=counter.tool_calls,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            guard_firings=GuardFirings.from_counter(counter),
        )
    symbolic_head = _git(state.worktree, context.environment, "symbolic-ref", "--quiet", "HEAD")
    if symbolic_head.returncode not in {0, 1}:
        return _context_receipt(
            context,
            DeliveryCode.GIT_FAILED,
            _git_message("cannot inspect isolated worktree HEAD attachment", symbolic_head),
            command_exit=0,
            budget=context.budget,
            budget_usage=usage,
            turns=counter.turns,
            tool_calls=counter.tool_calls,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            guard_firings=GuardFirings.from_counter(counter),
        )
    if head.stdout.strip() != context.base_commit.encode("ascii") or symbolic_head.returncode == 0:
        return _context_receipt(
            context,
            DeliveryCode.COMMAND_CHANGED_HEAD,
            "command changed the isolated worktree HEAD",
            command_exit=0,
            budget=context.budget,
            budget_usage=usage,
            turns=counter.turns,
            tool_calls=counter.tool_calls,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            guard_firings=GuardFirings.from_counter(counter),
        )

    added = _git(
        state.worktree,
        context.environment,
        "add",
        "-A",
        "--",
        ".",
        ":(exclude,glob)**/.venv/**",
        ":(exclude,glob)**/.pytest_cache/**",
        ":(exclude,glob)**/__pycache__/**",
    )
    if added.returncode != 0:
        return _context_receipt(
            context,
            DeliveryCode.GIT_FAILED,
            _git_message("cannot stage candidate tree", added),
            command_exit=0,
            budget=context.budget,
            budget_usage=usage,
            turns=counter.turns,
            tool_calls=counter.tool_calls,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            guard_firings=GuardFirings.from_counter(counter),
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
            budget=context.budget,
            budget_usage=usage,
            turns=counter.turns,
            tool_calls=counter.tool_calls,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            guard_firings=GuardFirings.from_counter(counter),
        )
    if tree.stdout.strip() == base_tree.stdout.strip():
        # Decision (pinned): an exhausted attempt that produced no diff is
        # reported NO_CHANGES (DISCARDED), not BUDGET_EXHAUSTED. There is no
        # candidate to retain, and BUDGET_EXHAUSTED means
        # candidate-created; the budget state still records the exhaustion on
        # the receipt's `budget` field, so the spend is not lost.
        return _context_receipt(
            context,
            DeliveryCode.NO_CHANGES,
            "command produced no changes",
            changed_paths=(),
            command_exit=0,
            budget=context.budget,
            budget_usage=usage,
            turns=counter.turns,
            tool_calls=counter.tool_calls,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            guard_firings=GuardFirings.from_counter(counter),
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
            budget=context.budget,
            budget_usage=usage,
            turns=counter.turns,
            tool_calls=counter.tool_calls,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            guard_firings=GuardFirings.from_counter(counter),
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
            budget=context.budget,
            budget_usage=usage,
            turns=counter.turns,
            tool_calls=counter.tool_calls,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            guard_firings=GuardFirings.from_counter(counter),
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
            budget=context.budget,
            budget_usage=usage,
            turns=counter.turns,
            tool_calls=counter.tool_calls,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            guard_firings=GuardFirings.from_counter(counter),
        )
    if exhausted is not None:
        message = f"candidate created; whole-attempt {_budget_exhaustion_detail(exhausted, usage)}"
        return _context_receipt(
            context,
            DeliveryCode.BUDGET_EXHAUSTED,
            message,
            candidate_commit=candidate_commit,
            changed_paths=changed_paths,
            budget=context.budget,
            budget_usage=usage,
            turns=counter.turns,
            tool_calls=counter.tool_calls,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            guard_firings=GuardFirings.from_counter(counter),
        )
    return _context_receipt(
        context,
        DeliveryCode.OK,
        "candidate created",
        candidate_commit=candidate_commit,
        changed_paths=changed_paths,
        command_exit=0,
        budget=context.budget,
        budget_usage=usage,
        turns=counter.turns,
        tool_calls=counter.tool_calls,
        tokens_in=counter.tokens_in,
        tokens_out=counter.tokens_out,
        guard_firings=GuardFirings.from_counter(counter),
    )


@dataclass(frozen=True, slots=True)
class _StreamOutcome:
    """How the budget-enforced implementer stream ended."""

    exhausted: BudgetState | None
    command_timed_out: bool
    counter: TurnCounter
    seconds_used: float


def _consume_chunk(
    chunk: bytes,
    spool: BinaryIO,
    pending: bytes,
    counter: TurnCounter,
    budget: Budget,
) -> tuple[bytes, BudgetState | None]:
    """Write ``chunk`` to the spool and feed its complete lines to the counter.

    Returns ``(pending, exhausted)``. ``pending`` is the still-unterminated
    tail after every complete line has been fed; ``exhausted`` is
    ``TURN_EXHAUSTED`` when the chunk crossed the turn limit,
    ``TOKEN_EXHAUSTED`` when it crossed the token limit, else ``None``.
    """
    spool.write(chunk)
    pending += chunk
    while (newline := pending.find(b"\n")) != -1:
        line = pending[:newline].decode("utf-8", errors="replace")
        pending = pending[newline + 1 :]
        counter.feed(line)
        if budget.turn_limit is not None and counter.turns > budget.turn_limit:
            return pending, BudgetState.TURN_EXHAUSTED
        if budget.token_limit is not None and counter.tokens_out > budget.token_limit:
            return pending, BudgetState.TOKEN_EXHAUSTED
    return pending, None


def _stream_implementer(
    process: subprocess.Popen[bytes],
    spool: BinaryIO,
    budget: Budget,
    timeout: float,
) -> _StreamOutcome:
    """Stream the implementer's merged output while enforcing the budget.

    The implementer writes to a pipe (``stdout=PIPE`` with stderr merged in),
    not to the existing output file, so live enforcement needs a tee: every
    byte is written to ``spool`` (the existing destination) and each complete
    line is fed to a :class:`TurnCounter`. A turn limit trips as soon as a
    ``turn_start`` line crosses it; a deadline trips on a monotonic clock, so
    a quiet process is still stopped once its time is spent.

    When a budget deadline is declared, the effective command deadline is
    ``max(timeout, deadline_seconds)`` so a deadline beyond ``timeout`` is
    allowed to fire and retain the partial candidate instead of the command
    timeout discarding it. Before a deadline is declared, a child that has
    already exited is drained to EOF and reported by its own exit, so a
    process that finished just before the deadline is not misreported and its
    spool tail is not lost.
    """
    stdout = process.stdout
    assert stdout is not None, "budget enforcement requires a captured stdout pipe"

    counter = TurnCounter()
    started = time.monotonic()
    command_deadline = started + (
        max(timeout, budget.deadline_seconds)
        if budget.deadline_seconds is not None
        else timeout
    )
    pending = b""
    selector = selectors.DefaultSelector()
    try:
        selector.register(stdout, selectors.EVENT_READ)
        while True:
            now = time.monotonic()
            elapsed = now - started
            if budget.deadline_seconds is not None and elapsed > budget.deadline_seconds:
                if process.poll() is not None:
                    # The child finished before the deadline fired. Drain its
                    # remaining output so the spool tail survives and its own
                    # exit is reported, not a spurious DEADLINE_EXHAUSTED.
                    while chunk := stdout.read1(64 * 1024):
                        pending, exhausted = _consume_chunk(
                            chunk, spool, pending, counter, budget
                        )
                        if exhausted is not None:
                            return _StreamOutcome(
                                exhausted,
                                False,
                                counter,
                                time.monotonic() - started,
                            )
                    break
                return _StreamOutcome(
                    BudgetState.DEADLINE_EXHAUSTED, False, counter, elapsed
                )
            if now >= command_deadline:
                return _StreamOutcome(None, True, counter, elapsed)

            wait = min(_STREAM_POLL_SECONDS, max(0.0, command_deadline - now))
            if budget.deadline_seconds is not None:
                wait = min(wait, max(0.0, budget.deadline_seconds - elapsed))
            events = selector.select(timeout=wait)
            if not events:
                if process.poll() is not None:
                    break
                continue

            chunk = stdout.read1(64 * 1024)
            if not chunk:
                break
            pending, exhausted = _consume_chunk(
                chunk, spool, pending, counter, budget
            )
            if exhausted is not None:
                return _StreamOutcome(
                    exhausted, False, counter, time.monotonic() - started
                )

        if pending:
            counter.feed(pending.decode("utf-8", errors="replace"))
            if budget.turn_limit is not None and counter.turns > budget.turn_limit:
                return _StreamOutcome(
                    BudgetState.TURN_EXHAUSTED,
                    False,
                    counter,
                    time.monotonic() - started,
                )
            if budget.token_limit is not None and counter.tokens_out > budget.token_limit:
                return _StreamOutcome(
                    BudgetState.TOKEN_EXHAUSTED,
                    False,
                    counter,
                    time.monotonic() - started,
                )

        try:
            process.wait(timeout=max(0.0, command_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return _StreamOutcome(None, True, counter, time.monotonic() - started)
        return _StreamOutcome(None, False, counter, time.monotonic() - started)
    finally:
        selector.close()


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
            validation=pending.validation,
            validation_exit=pending.validation_exit,
            validation_output=pending.validation_output,
            validation_output_bytes=pending.validation_output_bytes,
            budget=pending.budget,
            budget_usage=pending.budget_usage,
            turns=pending.turns,
            tool_calls=pending.tool_calls,
            tokens_in=pending.tokens_in,
            tokens_out=pending.tokens_out,
            guard_firings=pending.guard_firings,
            carried=pending.carried,
        )
    return _context_receipt(
        context,
        DeliveryCode.GIT_FAILED,
        _git_message("cannot publish candidate ref", published),
        candidate_commit=pending.candidate_commit,
        changed_paths=pending.changed_paths,
        command_exit=pending.command_exit,
        validation=pending.validation,
        validation_exit=pending.validation_exit,
        validation_output=pending.validation_output,
        validation_output_bytes=pending.validation_output_bytes,
        budget=pending.budget,
        budget_usage=pending.budget_usage,
        turns=pending.turns,
        tool_calls=pending.tool_calls,
        tokens_in=pending.tokens_in,
        tokens_out=pending.tokens_out,
        guard_firings=pending.guard_firings,
        carried=pending.carried,
    )


@dataclass(frozen=True, slots=True)
class _TestRunResult:
    """One shell-free run of the contract's declared test command.

    Exactly one of ``returncode``, ``timed_out``, or ``unavailable`` carries
    the outcome; the default tier monkeypatches ``_run_test_command`` to
    return this instead of spawning (binding rule 3).
    """

    returncode: int | None = None
    output: bytes = b""
    timed_out: bool = False
    unavailable: str | None = None


def _run_test_command(
    command: tuple[str, ...],
    cwd: Path,
    environment: dict[str, str],
    timeout: float,
    *,
    extra_env: dict[str, str] | None = None,
) -> _TestRunResult:
    """Run the contract's own command verbatim and shell-free, once.

    The command starts in its own process group (``start_new_session=True``)
    for the same reason the implementer run does: a timing-out test command
    must not orphan descendants into the isolated worktree, which would block
    its removal. On timeout the whole group is torn down with
    ``_teardown_process_group`` before the partial output is returned.
    ``extra_env`` overlays ``environment`` (used to widen ``COLUMNS`` for a
    validation run without touching the base environment every other caller
    shares).

    This module-level function is the test seam (and therefore the extension
    seam): the default tier monkeypatches it exactly as it does ``_git``.
    The real subprocess behaviour belongs to the integration tier.
    """
    env = environment if not extra_env else {**environment, **extra_env}
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        return _TestRunResult(
            unavailable=f"cannot run test command {list(command)!r}: {exc}",
        )

    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _teardown_process_group(process)
        stdout, _ = process.communicate()
        return _TestRunResult(output=stdout, timed_out=True)
    except BaseException:
        with suppress(BaseException):
            _teardown_process_group(process)
        raise
    return _TestRunResult(returncode=process.returncode, output=stdout)


def _checkout_candidate(
    worktree: Path,
    environment: dict[str, str],
    candidate_commit: str,
) -> str | None:
    """Check the exact candidate commit out in the isolated worktree.

    Returns ``None`` on success and a message on failure, so the validator can
    distinguish "declared but not runnable" from a completed test run.
    """
    checked_out = _git(
        worktree, environment, "checkout", "--quiet", "--detach", candidate_commit
    )
    if checked_out.returncode != 0:
        return _git_message(
            "cannot check out candidate commit for validation", checked_out
        )
    return None


def _validated(
    receipt: DeliveryReceipt,
    *,
    validation: ValidationOutcome,
    validation_exit: int | None,
    validation_output: str | None,
    code: DeliveryCode | None = None,
    message: str | None = None,
) -> DeliveryReceipt:
    """Return ``receipt`` with the engine-owned validation fields set."""
    return replace(
        receipt,
        code=receipt.code if code is None else code,
        message=receipt.message if message is None else message,
        validation=validation,
        validation_exit=validation_exit,
        validation_output=validation_output,
        validation_output_bytes=(
            None if validation_output is None else len(validation_output.encode("utf-8"))
        ),
    )


def _validate_candidate(
    context: _DeliveryContext,
    state: _AttemptState,
    pending: DeliveryReceipt,
    timeout: float,
) -> DeliveryReceipt:
    """Run the contract's ``test_command`` against the exact candidate commit.

    A ``FAILED`` validation keeps the candidate and its evidence; it changes
    only the coarse ``code`` to ``TESTS_FAILED``. Every other validation
    outcome leaves ``code`` as ``OK`` and records the truth in the
    ``validation`` fields. A ``BUDGET_EXHAUSTED`` pending keeps that code
    even when validation fails or passes -- the budget state is the
    authoritative verdict and is not overwritten by V4's self-test.
    ``command_exit`` is untouched either way.

    The validation run reuses the caller's single ``deliver --timeout``
    (default 30s) rather than ``runner.run_tests``' 120s budget. That keeps
    one knob for the whole delivery operation: the engine-owned validation
    is part of the deliver call, not a separate model-invoked operation, and
    a second default would make the worst-case bound depend on which command
    happened to be declared.
    """
    test_command = context.test_command
    if not test_command:
        return _validated(
            pending,
            validation=ValidationOutcome.NOT_REQUESTED,
            validation_exit=None,
            validation_output=None,
        )

    candidate_commit = pending.candidate_commit
    assert candidate_commit is not None

    if (checkout_error := _checkout_candidate(
        state.worktree, context.environment, candidate_commit
    )) is not None:
        return _validated(
            pending,
            validation=ValidationOutcome.UNAVAILABLE,
            validation_exit=None,
            validation_output=checkout_error,
        )

    # R9 applies at validation too: the carried set (preserve, checks, and
    # tracked test infrastructure) is restored from the accepted base before
    # the suite runs, exactly as it is before every self_test. A restoration
    # that could not be trusted -- ls-tree on the base failed, or checkout of
    # a non-empty carried set failed -- must not let validation run silently
    # without it; that is reported the same way an unrunnable candidate
    # already is (UNAVAILABLE), keeping the candidate.
    try:
        carried = restore_carried_at(
            state.worktree,
            context.environment,
            context.base_commit,
            context.contract,
            pending.changed_paths or (),
        )
    except _CarriedRestoreFailed as exc:
        return _validated(
            pending,
            validation=ValidationOutcome.UNAVAILABLE,
            validation_exit=None,
            validation_output=str(exc),
        )
    pending = replace(pending, carried=carried)

    runs: list[tuple[str, ...]] = [test_command]
    if carried.preserve:
        runs.append((*test_command, *carried.preserve))
    if carried.checks:
        runs.append((*test_command, *carried.checks))

    def _failed(exit_code: int, joined_output: str) -> DeliveryReceipt:
        if pending.code is DeliveryCode.BUDGET_EXHAUSTED:
            return _validated(
                pending,
                validation=ValidationOutcome.FAILED,
                validation_exit=exit_code,
                validation_output=joined_output,
                message=(
                    "candidate created (budget exhausted); "
                    f"contract test_command failed with exit {exit_code}"
                ),
            )
        return _validated(
            pending,
            validation=ValidationOutcome.FAILED,
            validation_exit=exit_code,
            validation_output=joined_output,
            code=DeliveryCode.TESTS_FAILED,
            message=f"candidate created; contract test_command failed with exit {exit_code}",
        )

    outputs: list[str] = []
    exit_code: int | None = None
    # One shared deadline for the whole validation operation (contradicting
    # "3x the timeout" would break the "one knob" docstring above): each run
    # gets whatever is left of `timeout`, not a fresh copy of it.
    started = time.monotonic()
    for argv in runs:
        remaining = max(timeout - (time.monotonic() - started), 0.1)
        result = _run_test_command(
            argv, state.worktree, context.environment, remaining, extra_env={"COLUMNS": "500"}
        )
        if result.unavailable is not None:
            return _validated(
                pending,
                validation=ValidationOutcome.UNAVAILABLE,
                validation_exit=None,
                validation_output=result.unavailable,
            )
        output, _ = tail_output(result.output)
        outputs.append(output)
        if result.timed_out:
            # A later timeout must not hide an earlier run's known failure:
            # if an earlier run already recorded a non-zero exit, that
            # failure -- not the timeout -- is the verdict.
            if exit_code is not None:
                return _failed(exit_code, "\n".join(outputs))
            return _validated(
                pending,
                validation=ValidationOutcome.TIMED_OUT,
                validation_exit=None,
                validation_output="\n".join(outputs),
            )
        if exit_code is None and result.returncode != 0:
            exit_code = result.returncode

    joined_output = "\n".join(outputs)
    if exit_code is None:
        return _validated(
            pending,
            validation=ValidationOutcome.PASSED,
            validation_exit=0,
            validation_output=joined_output,
        )
    return _failed(exit_code, joined_output)


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
    validation: ValidationOutcome = ValidationOutcome.NOT_APPLICABLE,
    validation_exit: int | None = None,
    validation_output: str | None = None,
    validation_output_bytes: int | None = None,
    budget: Budget | None = None,
    budget_usage: BudgetUsage | None = None,
    turns: int = 0,
    tool_calls: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    guard_firings: GuardFirings = _NO_GUARD_FIRINGS,
    carried: Carried = _NO_CARRIED,
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
        validation=validation,
        validation_exit=validation_exit,
        validation_output=validation_output,
        validation_output_bytes=validation_output_bytes,
        budget=context.budget if budget is None else budget,
        budget_usage=budget_usage,
        turns=turns,
        tool_calls=tool_calls,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        guard_firings=guard_firings,
        carried=carried,
        size_refusal=context.size_refusal,
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
    validation: ValidationOutcome = ValidationOutcome.NOT_APPLICABLE,
    validation_exit: int | None = None,
    validation_output: str | None = None,
    validation_output_bytes: int | None = None,
    budget: Budget | None = None,
    budget_usage: BudgetUsage | None = None,
    turns: int = 0,
    tool_calls: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    guard_firings: GuardFirings = _NO_GUARD_FIRINGS,
    carried: Carried = _NO_CARRIED,
    size_refusal: str | None = None,
) -> DeliveryReceipt:
    declared = budget if budget is not None else Budget()
    if budget_usage is not None:
        usage = budget_usage
    elif declared.declared:
        usage = BudgetUsage(BudgetState.NOT_ENFORCED, 0, 0.0)
    else:
        usage = evaluate(declared, 0, 0.0)
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
        validation=validation,
        validation_exit=validation_exit,
        validation_output=validation_output,
        validation_output_bytes=validation_output_bytes,
        budget=declared,
        budget_usage=usage,
        turns=turns,
        tool_calls=tool_calls,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        guard_firings=guard_firings,
        carried=carried,
        size_refusal=size_refusal,
    )
