"""Run one real Pi attempt inside the caller-owned disposable worktree."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch
from pathlib import Path
from typing import BinaryIO, Protocol

from .check import check
from .contract import Contract, ContractError, load_contract
from .exits import ExitCode
from .mutation import file_sha256, normalize_relative_path

MODEL_ENV = "SATYRN_MODEL"
PATCH_ENV = "SATYRN_ATTEMPT_PATCH"
TRANSCRIPT_ENV = "SATYRN_ATTEMPT_TRANSCRIPT"
MUTATION_CONTEXT_ENV = "SATYRN_MUTATION_CONTEXT"
ENGINE_REPO_ENV = "SATYRN_ENGINE_REPO"


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

    def __post_init__(self) -> None:
        if not isinstance(self.code, AttemptCode):
            raise TypeError("code must be an AttemptCode")

    @property
    def exit_code(self) -> ExitCode:
        """Return the stable process exit for this detailed result."""
        return _ATTEMPT_TO_EXIT[self.code]


@dataclass(frozen=True, slots=True)
class AttemptArtifacts:
    """Optional caller-owned artifact destinations outside the repository."""

    patch: Path | None
    transcript: Path | None


@dataclass(frozen=True, slots=True)
class AttemptContext:
    """Immutable inputs captured before Pi starts."""

    repo: Path
    contract: Contract
    frozen_contract: Path
    base_commit: str
    revisions: Mapping[str, str]
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
    """The production and test seam for the one Pi child."""

    def run(
        self,
        command: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str],
        stdout: BinaryIO,
        stderr: BinaryIO,
    ) -> int: ...


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

    def run(
        self,
        command: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str],
        stdout: BinaryIO,
        stderr: BinaryIO,
    ) -> int:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
        return completed.returncode


def build_prompt(contract: Contract, writable_paths: Sequence[str]) -> str:
    """Build the intentionally small E5 handoff prompt."""
    paths = "\n".join(f"- {path}" for path in writable_paths)
    return (
        "Implement this bounded task:\n"
        f"{contract.task}\n\n"
        "Writable files:\n"
        f"{paths}\n\n"
        "You may read files. Use the edit tool for every write. "
        "Do not create files. Stop when the task is complete."
    )


def build_pi_command(engine_repo: Path, model: str, prompt: str) -> tuple[str, ...]:
    """Return the exact hermetic Pi child argv."""
    package = engine_repo / "packages" / "engine"
    return (
        "pi",
        "--print",
        "--mode",
        "json",
        "--no-session",
        "--model",
        model,
        "--no-extensions",
        "--extension",
        os.fspath(package / "engine.ts"),
        "--extension",
        os.fspath(package / "mutator.ts"),
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        "--no-approve",
        "--tools",
        "read,edit",
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

    prepared = _prepare(root_candidate, checked.contract, model, env, git)
    if isinstance(prepared, AttemptResult):
        return prepared
    root, base_commit, revisions, artifacts, engine_repo = prepared

    try:
        temporary_parent = Path(tempfile.mkdtemp(prefix=".satyrn-attempt-", dir=root.parent))
    except OSError as exc:
        return _failed(model, f"cannot create attempt temporary directory: {exc}")
    try:
        frozen_contract = temporary_parent / "contract.yaml"
        try:
            _freeze_contract(contract_candidate, frozen_contract)
            frozen_value = load_contract(frozen_contract)
        except (ContractError, OSError) as exc:
            return _failed(model, f"cannot freeze contract: {exc}")
        if frozen_value != checked.contract:
            return _failed(model, "contract changed while the attempt was being prepared")

        context = AttemptContext(
            repo=root,
            contract=checked.contract,
            frozen_contract=frozen_contract,
            base_commit=base_commit,
            revisions=revisions,
            model=model,
            engine_repo=engine_repo,
        )
        return _run(context, artifacts, env, git, pi, output, errors, temporary_parent)
    finally:
        shutil.rmtree(temporary_parent, ignore_errors=True)


def _prepare(
    repo: Path,
    contract: Contract,
    model: str,
    environment: Mapping[str, str],
    git: GitRunner,
) -> tuple[Path, str, dict[str, str], AttemptArtifacts, Path] | AttemptResult:
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

    revisions: dict[str, str] = {}
    for raw_path in listed.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = os.fsdecode(raw_path)
        try:
            normalized = normalize_relative_path(path)
        except ValueError:
            continue
        if not any(fnmatch(normalized, pattern) for pattern in contract.writable_paths):
            continue
        target = root.joinpath(*normalized.split("/"))
        try:
            content = target.read_bytes()
        except OSError:
            continue
        if target.is_file():
            revisions[normalized] = file_sha256(content)
    if not revisions:
        return _failed(model, "contract matches no existing tracked writable file")

    artifacts = _artifact_destinations(root, environment)
    if isinstance(artifacts, str):
        return _failed(model, artifacts)

    engine_repo_text = environment.get(ENGINE_REPO_ENV)
    engine_repo = (
        Path(os.path.abspath(engine_repo_text))
        if engine_repo_text
        else Path(__file__).resolve().parents[2]
    )
    package = engine_repo / "packages" / "engine"
    if not all((package / name).is_file() for name in ("engine.ts", "mutator.ts")):
        return _failed(model, f"engine package is unavailable under {engine_repo}")

    return root, head.stdout.strip().decode("ascii"), revisions, artifacts, engine_repo


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
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    child_environment = dict(environment)
    child_environment[ENGINE_REPO_ENV] = os.fspath(context.engine_repo)
    child_environment[MUTATION_CONTEXT_ENV] = mutation_context
    prompt = build_prompt(context.contract, tuple(sorted(context.revisions)))
    command = build_pi_command(context.engine_repo, context.model, prompt)
    transcript_spool = temporary_parent / "transcript.jsonl"

    try:
        with transcript_spool.open("xb") as transcript_output:
            command_exit = pi.run(
                command,
                context.repo,
                child_environment,
                transcript_output,
                stderr,
            )
            transcript_output.flush()
            os.fsync(transcript_output.fileno())
    except OSError as exc:
        return _failed(context.model, f"cannot run Pi: {exc}")

    try:
        if artifacts.transcript is not None:
            _publish_file(transcript_spool, artifacts.transcript)
        _copy_file(transcript_spool, stdout)
    except (OSError, ValueError) as exc:
        return _failed(
            context.model,
            f"cannot publish transcript: {exc}",
            command_exit=command_exit,
        )

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
        return _failed(context.model, f"cannot start Git diff: {exc}", command_exit=command_exit)
    if patch.returncode != 0:
        return _failed(
            context.model,
            _git_message("cannot produce attempt patch", patch),
            command_exit=command_exit,
        )
    if patch.stdout and artifacts.patch is not None:
        try:
            _publish_bytes(patch.stdout, artifacts.patch)
        except (OSError, ValueError) as exc:
            return _failed(
                context.model,
                f"cannot publish patch: {exc}",
                command_exit=command_exit,
            )

    if command_exit != 0:
        return _failed(
            context.model,
            f"Pi exited with status {command_exit}",
            command_exit=command_exit,
        )
    return AttemptResult(AttemptCode.OK, model=context.model, command_exit=0)


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


def _artifact_destinations(
    repo: Path,
    environment: Mapping[str, str],
) -> AttemptArtifacts | str:
    patch = _artifact_path(environment.get(PATCH_ENV))
    transcript = _artifact_path(environment.get(TRANSCRIPT_ENV))
    if patch is not None and transcript is not None and patch == transcript:
        return "patch and transcript artifact paths must be different"
    for label, candidate in (("patch", patch), ("transcript", transcript)):
        if candidate is None:
            continue
        parent = candidate.parent
        if not parent.is_dir() or parent.is_symlink():
            return f"{label} artifact parent must be a real directory: {parent}"
        if os.path.lexists(candidate):
            return f"{label} artifact already exists: {candidate}"
        if _contains(repo, candidate):
            return f"{label} artifact must be outside the repository: {candidate}"
    return AttemptArtifacts(patch=patch, transcript=transcript)


def _artifact_path(value: str | None) -> Path | None:
    return None if value is None else Path(os.path.abspath(value))


def _contains(root: Path, candidate: Path) -> bool:
    try:
        canonical_root = root.resolve(strict=True)
        canonical_candidate = candidate.parent.resolve(strict=True) / candidate.name
        canonical_candidate.relative_to(canonical_root)
    except (OSError, ValueError):
        return False
    return True


def _publish_file(source: Path, destination: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.satyrn-",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output, length=64 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def _freeze_contract(source: Path, destination: Path) -> None:
    destination.write_bytes(source.read_bytes())


def _publish_bytes(content: bytes, destination: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.satyrn-",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_file(source: Path, output: BinaryIO) -> None:
    with source.open("rb") as input_file:
        while chunk := input_file.read(64 * 1024):
            output.write(chunk)
    output.flush()


def _failed(model: str, message: str, *, command_exit: int | None = None) -> AttemptResult:
    return AttemptResult(
        AttemptCode.ATTEMPT_FAILED,
        message=message,
        model=model,
        command_exit=command_exit,
    )


def _git_message(prefix: str, result: GitResult) -> str:
    detail = result.stderr.strip().decode("utf-8", errors="replace")
    return f"{prefix}: {detail}" if detail else prefix
