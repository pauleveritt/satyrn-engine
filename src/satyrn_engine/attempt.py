"""Run one real Pi attempt inside the caller-owned disposable worktree."""

import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch
from pathlib import Path
from typing import BinaryIO, Never, Protocol

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

    patch: _ArtifactDestination | None
    transcript: _ArtifactDestination | None


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    """Filesystem identity used when spelling and case are not authoritative."""

    device: int
    inode: int


@dataclass(slots=True)
class _ArtifactDestination:
    """An absent artifact path pinned to its already-validated parent."""

    path: Path
    parent_identity: _FileIdentity
    parent_descriptor: int | None

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
        f"--model={model}",
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

    artifact_owner: list[_ArtifactDestination] = []
    temporary_parent: Path | None = None
    pending: AttemptResult | None = None
    active_exception: BaseException | None = None
    try:
        prepared = _prepare(root_candidate, checked.contract, model, env, git, artifact_owner)
        if isinstance(prepared, AttemptResult):
            pending = prepared
        else:
            root, base_commit, revisions, artifacts, engine_repo = prepared
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
        try:
            content = _read_tracked_regular(root, normalized)
        except OSError as exc:
            return _failed(model, f"cannot inspect tracked writable file {normalized}: {exc}")
        if content is not None:
            revisions[normalized] = file_sha256(content)
    if not revisions:
        return _failed(model, "contract matches no existing tracked writable file")

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
    if not all((package / name).is_file() for name in ("engine.ts", "mutator.ts")):
        return _failed(model, f"engine package is unavailable under {engine_repo}")

    artifacts = _artifact_destinations(forbidden_roots, environment, artifact_owner)
    if isinstance(artifacts, str):
        return _failed(model, artifacts)

    return root, head.stdout.strip().decode("ascii"), revisions, artifacts, engine_repo


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
        transcript_output = transcript_spool.open("xb")
        active_exception: BaseException | None = None
        try:
            command_exit = pi.run(
                command,
                context.repo,
                child_environment,
                transcript_output,
                stderr,
            )
            transcript_output.flush()
            os.fsync(transcript_output.fileno())
        except BaseException as exc:
            active_exception = exc
            raise
        finally:
            try:
                transcript_output.close()
            except BaseException as cleanup_error:
                detail = f"cannot close transcript spool {transcript_spool}: {cleanup_error}"
                if active_exception is not None:
                    active_exception.add_note(f"secondary cleanup failure: {detail}")
                else:
                    _raise_cleanup_failure(cleanup_error, detail)
    except OSError as exc:
        return _failed(context.model, f"cannot run Pi: {_exception_detail(exc)}")

    try:
        if artifacts.transcript is not None:
            _publish_file(transcript_spool, artifacts.transcript)
        _copy_file(transcript_spool, stdout)
    except (OSError, ValueError) as exc:
        return _failed(
            context.model,
            f"cannot publish transcript: {_exception_detail(exc)}",
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
                f"cannot publish patch: {_exception_detail(exc)}",
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
        error.add_note(detail)
        cleanup_exception = error
    return pending, cleanup_exception


def _publish_file(source: Path, destination: _ArtifactDestination) -> None:
    def write(output: BinaryIO) -> None:
        with source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output, length=64 * 1024)

    _publish(destination, write)


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


def _copy_file(source: Path, output: BinaryIO) -> None:
    with source.open("rb") as input_file:
        while chunk := input_file.read(64 * 1024):
            output.write(chunk)
    output.flush()


def _exception_detail(error: BaseException) -> str:
    notes = getattr(error, "__notes__", ())
    return "; ".join((str(error), *notes))


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
