"""Default-tier tests for E5's pure boundaries and injected attempt seams."""

import io
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, Never, cast

import pytest

import satyrn_engine.attempt as attempt_module
import satyrn_engine.cli as cli
from satyrn_engine.attempt import (
    AttemptCode,
    AttemptResult,
    GitResult,
    build_pi_command,
    build_prompt,
)
from satyrn_engine.contract import Contract
from satyrn_engine.exits import ExitCode


class FakeGit:
    def __init__(self, repo: Path, *, diff: bytes = b"diff bytes\n") -> None:
        self.repo = repo
        self.diff = diff
        self.overrides: dict[str, GitResult | OSError] = {}
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        repo: Path,
        args: Sequence[str],
        environment: Mapping[str, str],
    ) -> GitResult:
        del environment
        assert repo == self.repo
        self.calls.append(tuple(args))
        match tuple(args[:2]):
            case ("rev-parse", "--show-toplevel"):
                key = "root"
            case ("rev-parse", "--path-format=absolute"):
                key = "git-dir" if args[-1] == "--git-dir" else "common-dir"
            case ("rev-parse", _):
                key = "head"
            case ("worktree", "list"):
                key = "worktrees"
            case ("--no-optional-locks", _):
                key = "status"
            case _:
                key = args[0]
        if (override := self.overrides.get(key)) is not None:
            if isinstance(override, OSError):
                raise override
            return override
        match key:
            case "root":
                return GitResult(0, os.fsencode(self.repo) + b"\n", b"")
            case "head":
                return GitResult(0, b"a" * 40 + b"\n", b"")
            case "status":
                return GitResult(0, b"", b"")
            case "ls-files":
                return GitResult(0, b"app.py\0notes.txt\0", b"")
            case "worktrees":
                return GitResult(
                    0,
                    b"worktree " + os.fsencode(self.repo) + b"\0HEAD " + b"a" * 40 + b"\0\0",
                    b"",
                )
            case "git-dir" | "common-dir":
                return GitResult(0, os.fsencode(self.repo / ".git") + b"\n", b"")
            case "diff":
                return GitResult(0, self.diff, b"")
            case _:
                raise AssertionError(args)


class FakePi:
    def __init__(self, *, output: bytes = b'{"type":"session_shutdown"}\n', exit_code: int = 0) -> None:
        self.output = output
        self.exit_code = exit_code
        self.command: tuple[str, ...] | None = None
        self.environment: dict[str, str] | None = None

    def run(
        self,
        command: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str],
        stdout: BinaryIO,
        stderr: BinaryIO,
    ) -> int:
        del stderr
        assert cwd.is_dir()
        self.command = tuple(command)
        self.environment = dict(environment)
        stdout.write(self.output)
        return self.exit_code


def _repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    target = repo / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    (repo / "notes.txt").write_text("notes\n", encoding="utf-8")
    contract = repo / "contract.yaml"
    contract.write_text(
        "id: attempt\ntask: Replace one with two\nwritable_paths:\n  - app.py\n",
        encoding="utf-8",
    )
    return repo, contract, target


def _run(
    tmp_path: Path,
    *,
    git: FakeGit | None = None,
    pi: FakePi | None = None,
    environment: dict[str, str] | None = None,
) -> tuple[AttemptResult, bytes, bytes, FakeGit, FakePi]:
    repo, contract, _ = _repo(tmp_path)
    selected_git = git if git is not None else FakeGit(repo)
    selected_pi = pi if pi is not None else FakePi()
    stdout = io.BytesIO()
    stderr = io.BytesIO()
    env = {attempt_module.ENGINE_REPO_ENV: str(Path(__file__).parents[1])}
    if environment:
        env.update(environment)
    result = attempt_module.attempt(
        repo,
        contract,
        "provider/model",
        environment=env,
        git_runner=selected_git,
        pi_runner=selected_pi,
        stdout=stdout,
        stderr=stderr,
    )
    return result, stdout.getvalue(), stderr.getvalue(), selected_git, selected_pi


def _artifact_destination(path: Path) -> attempt_module._ArtifactDestination:
    descriptor = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )
    return attempt_module._ArtifactDestination(path, attempt_module._identity(os.fstat(descriptor)), descriptor)


def test_prompt_and_pi_command_are_small_and_hermetic(tmp_path: Path) -> None:
    contract = Contract("one", "Fix it", ("app.py",))
    prompt = build_prompt(contract, ("app.py", "src/other.py"))
    command = build_pi_command(tmp_path, "provider/model", prompt)

    assert prompt == (
        "Implement this bounded task:\nFix it\n\nWritable files:\n"
        "- app.py\n- src/other.py\n\nYou may read files. Use the edit tool for every write. "
        "Do not create files. Stop when the task is complete."
    )
    assert command[:6] == (
        "pi",
        "--print",
        "--mode",
        "json",
        "--no-session",
        "--model=provider/model",
    )
    assert build_pi_command(tmp_path, "-provider/model", prompt)[5] == "--model=-provider/model"
    assert command[-3:] == ("--tools", "read,edit", prompt)
    assert "--no-extensions" in command
    assert "orchestrator.ts" not in " ".join(command)
    assert command.count("--extension") == 2


def test_attempt_result_has_exhaustive_stable_exit_mapping() -> None:
    expected = {
        AttemptCode.OK: ExitCode.OK,
        AttemptCode.CONTRACT_UNREADABLE: ExitCode.CONTRACT_UNREADABLE,
        AttemptCode.CONTRACT_INVALID_YAML: ExitCode.CONTRACT_INVALID_YAML,
        AttemptCode.CONTRACT_MISSING_FIELD: ExitCode.CONTRACT_MISSING_FIELD,
        AttemptCode.REPO_UNAVAILABLE: ExitCode.REPO_UNAVAILABLE,
        AttemptCode.ATTEMPT_FAILED: ExitCode.ATTEMPT_FAILED,
    }
    assert expected == attempt_module._ATTEMPT_TO_EXIT
    assert {result: AttemptCode[result.name] for result in attempt_module._CHECK_TO_ATTEMPT} == {
        code: detail for code, detail in attempt_module._CHECK_TO_ATTEMPT.items()
    }
    for code, exit_code in expected.items():
        assert AttemptResult(code).exit_code is exit_code
    with pytest.raises(TypeError, match="AttemptCode"):
        replace(AttemptResult(AttemptCode.OK), code="TYPO")  # type: ignore[arg-type]


def test_git_routing_environment_has_a_closed_sanitized_vocabulary() -> None:
    expected = {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_WORK_TREE",
    }
    assert {variable.value for variable in attempt_module._GitRoutingVariable} == expected
    cleaned = attempt_module._clean_environment(dict.fromkeys(expected, "/redirect"))
    assert expected.isdisjoint(cleaned)


def test_attempt_success_exports_exact_artifacts_and_context(tmp_path: Path) -> None:
    output = tmp_path / "artifacts"
    output.mkdir()
    patch = output / "patch.diff"
    transcript = output / "transcript.jsonl"
    result, stdout, _, git, pi = _run(
        tmp_path,
        environment={
            attempt_module.PATCH_ENV: str(patch),
            attempt_module.TRANSCRIPT_ENV: str(transcript),
            "VIRTUAL_ENV": "/bad/venv",
            "PATH": "/bad/venv/bin:/usr/bin",
            "SSH_AUTH_SOCK": "/bad/socket",
            "GIT_DIR": "/bad/git",
        },
    )

    assert result == AttemptResult(AttemptCode.OK, model="provider/model", command_exit=0)
    assert stdout == transcript.read_bytes() == b'{"type":"session_shutdown"}\n'
    assert patch.read_bytes() == b"diff bytes\n"
    assert pi.environment is not None
    assert "VIRTUAL_ENV" not in pi.environment
    assert pi.environment["PATH"] == "/usr/bin"
    assert "SSH_AUTH_SOCK" not in pi.environment
    assert "GIT_DIR" not in pi.environment
    context = pi.environment[attempt_module.MUTATION_CONTEXT_ENV]
    assert '"revisions":{"app.py":"' in context
    assert '"contract":"' in context
    assert any(call[0] == "diff" for call in git.calls)


def test_attempt_without_artifact_env_still_forwards_transcript(tmp_path: Path) -> None:
    git = FakeGit(tmp_path / "repo", diff=b"")
    result, stdout, _, _, _ = _run(tmp_path, git=git)
    assert result.code is AttemptCode.OK
    assert stdout == b'{"type":"session_shutdown"}\n'


@pytest.mark.parametrize(
    ("fixture", "code"),
    [
        ("missing.yaml", AttemptCode.CONTRACT_UNREADABLE),
        ("invalid.yaml", AttemptCode.CONTRACT_INVALID_YAML),
        ("missing-field.yaml", AttemptCode.CONTRACT_MISSING_FIELD),
    ],
)
def test_contract_refusals_happen_before_git(tmp_path: Path, fixture: str, code: AttemptCode) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    contract = tmp_path / fixture
    if fixture == "invalid.yaml":
        contract.write_text("[", encoding="utf-8")
    elif fixture == "missing-field.yaml":
        contract.write_text("id: only\n", encoding="utf-8")
    result = attempt_module.attempt(repo, contract, "model", environment={}, git_runner=FakeGit(repo))
    assert result.code is code


def test_repo_refusal_happens_before_git(tmp_path: Path) -> None:
    contract = tmp_path / "contract.yaml"
    contract.write_text("id: x\ntask: y\n", encoding="utf-8")
    repo = tmp_path / "missing"
    result = attempt_module.attempt(repo, contract, "model", environment={}, git_runner=FakeGit(repo))
    assert result.code is AttemptCode.REPO_UNAVAILABLE


@pytest.mark.parametrize(
    ("key", "result", "message"),
    [
        ("root", GitResult(1, b"", b"not git"), "cannot resolve repository root"),
        ("status", GitResult(1, b"", b"status broke"), "cannot inspect repository status"),
        ("status", GitResult(0, b" M app.py\0", b""), "requires a clean"),
        ("ls-files", GitResult(1, b"", b"list broke"), "cannot enumerate tracked"),
    ],
)
def test_git_preparation_refusals(
    tmp_path: Path,
    key: str,
    result: GitResult,
    message: str,
) -> None:
    repo = tmp_path / "repo"
    git = FakeGit(repo)
    git.overrides[key] = result
    attempt_result, *_ = _run(tmp_path, git=git)
    assert attempt_result.code is AttemptCode.ATTEMPT_FAILED
    assert message in attempt_result.message


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("worktrees", "cannot enumerate registered worktrees"),
        ("git-dir", "cannot resolve Git administrative directories"),
        ("common-dir", "cannot resolve Git administrative directories"),
    ],
)
def test_protected_git_path_queries_must_succeed(tmp_path: Path, key: str, message: str) -> None:
    repo = tmp_path / "repo"
    git = FakeGit(repo)
    git.overrides[key] = GitResult(1, b"", b"query failed")
    result, *_ = _run(tmp_path, git=git)
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert message in result.message


def test_protected_git_path_metadata_must_be_usable(tmp_path: Path) -> None:
    first = tmp_path / "first"
    first.mkdir()
    repo = first / "repo"
    no_worktree = FakeGit(repo)
    no_worktree.overrides["worktrees"] = GitResult(0, b"HEAD " + b"a" * 40 + b"\0", b"")
    result, *_ = _run(first, git=no_worktree)
    assert result.message == "Git reported no registered worktrees"

    second = tmp_path / "second"
    second.mkdir()
    repo = second / "repo"
    bad_git_dir = FakeGit(repo)
    bad_git_dir.overrides["git-dir"] = GitResult(0, b"\0\n", b"")
    result, *_ = _run(second, git=bad_git_dir)
    assert result.message == "Git directory has an invalid path"

    third = tmp_path / "third"
    third.mkdir()
    repo = third / "repo"
    missing_common = FakeGit(repo)
    missing_common.overrides["common-dir"] = GitResult(0, os.fsencode(tmp_path / "missing") + b"\n", b"")
    result, *_ = _run(third, git=missing_common)
    assert "cannot inspect protected Git path" in result.message


@pytest.mark.parametrize("key", ["root", "status", "diff"])
def test_git_spawn_refusals(tmp_path: Path, key: str) -> None:
    repo = tmp_path / "repo"
    git = FakeGit(repo)
    git.overrides[key] = OSError("cannot spawn")
    result, *_ = _run(tmp_path, git=git)
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "cannot" in result.message


def test_repository_must_be_exact_root(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    git = FakeGit(other)
    git.overrides["root"] = GitResult(0, os.fsencode(repo) + b"\n", b"")
    result = attempt_module.attempt(other, contract, "model", environment={}, git_runner=git)
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "working-tree root" in result.message


def test_no_writable_file_is_refused_before_pi(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    contract.write_text("id: x\ntask: y\nwritable_paths:\n  - missing.py\n", encoding="utf-8")
    result, *_ = _run_existing(repo, contract, FakeGit(repo))
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "no existing tracked" in result.message


def _run_existing(
    repo: Path,
    contract: Path,
    git: FakeGit,
    *,
    environment: dict[str, str] | None = None,
    pi: FakePi | None = None,
) -> tuple[AttemptResult, bytes]:
    output = io.BytesIO()
    result = attempt_module.attempt(
        repo,
        contract,
        "model",
        environment={attempt_module.ENGINE_REPO_ENV: str(Path(__file__).parents[1])} | (environment or {}),
        git_runner=git,
        pi_runner=pi if pi is not None else FakePi(),
        stdout=output,
        stderr=io.BytesIO(),
    )
    return result, output.getvalue()


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({attempt_module.PATCH_ENV: "same", attempt_module.TRANSCRIPT_ENV: "same"}, "must be different"),
        ({attempt_module.PATCH_ENV: "repo/inside.diff"}, "outside the repository"),
    ],
)
def test_artifact_path_policy(tmp_path: Path, environment: dict[str, str], message: str) -> None:
    repo, contract, _ = _repo(tmp_path)
    env = {key: str(tmp_path / value) for key, value in environment.items()}
    result, _ = _run_existing(repo, contract, FakeGit(repo), environment=env)
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert message in result.message


def test_artifacts_cannot_target_another_registered_worktree_or_git_admin_dir(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    git_admin = tmp_path / "admin"
    git_admin.mkdir()
    git = FakeGit(repo)
    git.overrides["worktrees"] = GitResult(
        0,
        b"worktree " + os.fsencode(repo) + b"\0\0worktree " + os.fsencode(source) + b"\0\0",
        b"",
    )
    git.overrides["common-dir"] = GitResult(0, os.fsencode(git_admin) + b"\n", b"")

    for destination in (source / "transcript", git_admin / "transcript"):
        result, _ = _run_existing(
            repo,
            contract,
            git,
            environment={attempt_module.TRANSCRIPT_ENV: str(destination)},
        )
        assert result.code is AttemptCode.ATTEMPT_FAILED
        assert "every registered worktree" in result.message


def test_artifact_identity_checks_case_aliases_when_filesystem_supports_them(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    differently_cased = Path(str(repo).swapcase())
    same_directory = False
    try:
        same_directory = os.path.samefile(repo, differently_cased)
    except OSError:
        pytest.skip("filesystem is case-sensitive")
    assert same_directory
    result, _ = _run_existing(
        repo,
        contract,
        FakeGit(repo),
        environment={attempt_module.PATCH_ENV: str(differently_cased / "artifact")},
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "outside the repository" in result.message


def test_artifact_names_are_compared_case_insensitively_in_the_same_parent(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    result, _ = _run_existing(
        repo,
        contract,
        FakeGit(repo),
        environment={
            attempt_module.PATCH_ENV: str(tmp_path / "Result"),
            attempt_module.TRANSCRIPT_ENV: str(tmp_path / "result"),
        },
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "must be different" in result.message


def test_artifact_parent_identity_is_rechecked_after_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    descriptor: int | None = None
    original_open = attempt_module.os.open
    original_fstat = attempt_module.os.fstat

    def capture_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal descriptor
        opened = original_open(path, flags, mode, dir_fd=dir_fd)
        if Path(os.fsdecode(path)) == parent:
            descriptor = opened
        return opened

    def changed_fstat(selected: int) -> os.stat_result:
        actual = original_fstat(selected)
        if selected != descriptor:
            return actual
        values = list(actual)
        values[1] += 1
        return os.stat_result(values)

    monkeypatch.setattr(attempt_module.os, "open", capture_open)
    monkeypatch.setattr(attempt_module.os, "fstat", changed_fstat)
    result = attempt_module._artifact_destinations(
        (),
        {attempt_module.TRANSCRIPT_ENV: str(parent / "transcript")},
    )
    assert isinstance(result, str)
    assert "parent changed during preparation" in result


def test_artifact_descriptor_handoff_failure_closes_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    descriptor: int | None = None
    primary = MemoryError("cannot construct destination")
    original_open = attempt_module.os.open
    original_close = attempt_module.os.close
    closed: list[int] = []

    def capture_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal descriptor
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        return descriptor

    monkeypatch.setattr(attempt_module.os, "open", capture_open)

    def close_then_fail(selected: int) -> None:
        closed.append(selected)
        original_close(selected)
        raise OSError("handoff close failed")

    monkeypatch.setattr(attempt_module.os, "close", close_then_fail)
    monkeypatch.setattr(
        attempt_module,
        "_ArtifactDestination",
        lambda *args: (_ for _ in ()).throw(primary),
    )
    with pytest.raises(MemoryError) as excinfo:
        attempt_module._artifact_destinations(
            (),
            {attempt_module.TRANSCRIPT_ENV: str(parent / "transcript")},
        )
    assert excinfo.value is primary
    assert descriptor is not None
    assert closed == [descriptor]
    assert primary.__notes__ == ["secondary descriptor cleanup failure: handoff close failed"]
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_prepare_handoff_interrupt_closes_artifact_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, contract, _ = _repo(tmp_path)
    artifact_parent = tmp_path / "artifacts"
    artifact_parent.mkdir()
    primary = KeyboardInterrupt("after prepare")
    original_prepare = attempt_module._prepare
    descriptor: int | None = None

    def interrupt_after_prepare(
        selected_repo: Path,
        selected_contract: Contract,
        model: str,
        environment: Mapping[str, str],
        git: attempt_module.GitRunner,
        owner: list[attempt_module._ArtifactDestination],
    ) -> Never:
        nonlocal descriptor
        prepared = original_prepare(selected_repo, selected_contract, model, environment, git, owner)
        assert not isinstance(prepared, AttemptResult)
        descriptor = owner[0].descriptor()
        raise primary

    monkeypatch.setattr(attempt_module, "_prepare", interrupt_after_prepare)
    with pytest.raises(KeyboardInterrupt) as excinfo:
        attempt_module.attempt(
            repo,
            contract,
            "model",
            environment={
                attempt_module.ENGINE_REPO_ENV: str(Path(__file__).parents[1]),
                attempt_module.TRANSCRIPT_ENV: str(artifact_parent / "transcript"),
            },
            git_runner=FakeGit(repo),
            pi_runner=FakePi(),
            stdout=io.BytesIO(),
            stderr=io.BytesIO(),
        )
    assert excinfo.value is primary
    assert descriptor is not None
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_partial_artifact_descriptor_acquisition_closes_the_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_parent = tmp_path / "patches"
    transcript_parent = tmp_path / "transcripts"
    patch_parent.mkdir()
    transcript_parent.mkdir()
    opened: list[int] = []
    closed: list[int] = []
    original_open = attempt_module.os.open
    original_close = attempt_module.os.close

    def fail_second_parent(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if Path(os.fsdecode(path)) == transcript_parent:
            raise OSError("second parent unavailable")
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if Path(os.fsdecode(path)) == patch_parent:
            opened.append(descriptor)
        return descriptor

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(attempt_module.os, "open", fail_second_parent)
    monkeypatch.setattr(attempt_module.os, "close", record_close)
    result = attempt_module._artifact_destinations(
        (),
        {
            attempt_module.PATCH_ENV: str(patch_parent / "patch"),
            attempt_module.TRANSCRIPT_ENV: str(transcript_parent / "transcript"),
        },
    )
    assert isinstance(result, str)
    assert "second parent unavailable" in result
    assert opened and closed.count(opened[0]) == 1


def test_existing_artifact_is_not_overwritten(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    destination = tmp_path / "existing"
    destination.write_text("keep", encoding="utf-8")
    result, _ = _run_existing(
        repo,
        contract,
        FakeGit(repo),
        environment={attempt_module.TRANSCRIPT_ENV: str(destination)},
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert destination.read_text() == "keep"


def test_artifact_parent_must_be_a_real_directory(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    parent = tmp_path / "missing"
    result, _ = _run_existing(
        repo,
        contract,
        FakeGit(repo),
        environment={attempt_module.PATCH_ENV: str(parent / "patch")},
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "real directory" in result.message

    parent_file = tmp_path / "parent-file"
    parent_file.write_text("not a directory", encoding="utf-8")
    result, _ = _run_existing(
        repo,
        contract,
        FakeGit(repo),
        environment={attempt_module.PATCH_ENV: str(parent_file / "patch")},
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "real directory" in result.message


def test_symlinked_artifact_ancestor_cannot_alias_repository(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    (repo / "sub").mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    result, _ = _run_existing(
        repo,
        contract,
        FakeGit(repo),
        environment={attempt_module.PATCH_ENV: str(alias / "sub" / "patch")},
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "outside the repository" in result.message


def test_temporary_parent_failure_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, contract, _ = _repo(tmp_path)
    artifacts: list[attempt_module.AttemptArtifacts] = []
    original_destinations = attempt_module._artifact_destinations

    def capture_destinations(*args: object, **kwargs: object) -> attempt_module.AttemptArtifacts | str:
        prepared = original_destinations(*args, **kwargs)  # type: ignore[arg-type]
        if isinstance(prepared, attempt_module.AttemptArtifacts):
            artifacts.append(prepared)
        return prepared

    monkeypatch.setattr(attempt_module, "_artifact_destinations", capture_destinations)
    monkeypatch.setattr(attempt_module.tempfile, "mkdtemp", lambda **kwargs: (_ for _ in ()).throw(OSError("full")))
    result, _ = _run_existing(
        repo,
        contract,
        FakeGit(repo),
        environment={attempt_module.TRANSCRIPT_ENV: str(tmp_path / "transcript")},
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "temporary directory" in result.message
    assert artifacts[0].transcript is not None
    assert artifacts[0].transcript.parent_descriptor is None


def test_artifact_descriptor_close_failure_supersedes_pending_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, contract, _ = _repo(tmp_path)
    descriptor: int | None = None
    original_destinations = attempt_module._artifact_destinations
    original_close = attempt_module.os.close

    def capture_destinations(*args: object, **kwargs: object) -> attempt_module.AttemptArtifacts | str:
        nonlocal descriptor
        prepared = original_destinations(*args, **kwargs)  # type: ignore[arg-type]
        if isinstance(prepared, attempt_module.AttemptArtifacts) and prepared.transcript is not None:
            descriptor = prepared.transcript.parent_descriptor
        return prepared

    def close_then_fail(selected: int) -> None:
        original_close(selected)
        if selected == descriptor:
            raise OSError("artifact descriptor close failed")

    monkeypatch.setattr(attempt_module, "_artifact_destinations", capture_destinations)
    monkeypatch.setattr(attempt_module.os, "close", close_then_fail)
    result, _ = _run_existing(
        repo,
        contract,
        FakeGit(repo),
        environment={attempt_module.TRANSCRIPT_ENV: str(tmp_path / "transcript")},
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "artifact descriptor close failed" in result.message


def test_all_cleanup_failures_remain_visible_in_the_final_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, contract, _ = _repo(tmp_path)
    descriptor: int | None = None
    original_destinations = attempt_module._artifact_destinations
    original_close = attempt_module.os.close

    def capture_destinations(*args: object, **kwargs: object) -> attempt_module.AttemptArtifacts | str:
        nonlocal descriptor
        prepared = original_destinations(*args, **kwargs)  # type: ignore[arg-type]
        if isinstance(prepared, attempt_module.AttemptArtifacts) and prepared.transcript is not None:
            descriptor = prepared.transcript.parent_descriptor
        return prepared

    def fail_temporary_cleanup(path: Path) -> None:
        raise OSError(f"temporary cleanup failed; retained path: {path}")

    def close_then_fail(selected: int) -> None:
        original_close(selected)
        if selected == descriptor:
            raise OSError("artifact descriptor close failed")

    monkeypatch.setattr(attempt_module, "_artifact_destinations", capture_destinations)
    monkeypatch.setattr(attempt_module.shutil, "rmtree", fail_temporary_cleanup)
    monkeypatch.setattr(attempt_module.os, "close", close_then_fail)
    result, _ = _run_existing(
        repo,
        contract,
        FakeGit(repo),
        environment={attempt_module.TRANSCRIPT_ENV: str(tmp_path / "transcript")},
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "artifact descriptor close failed" in result.message
    assert "temporary cleanup failed" in result.message
    assert "retained path:" in result.message


def test_mixed_cleanup_failures_preserve_retained_path_on_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, contract, _ = _repo(tmp_path)
    descriptor: int | None = None
    primary = MemoryError("artifact descriptor close failed")
    original_destinations = attempt_module._artifact_destinations
    original_close = attempt_module.os.close

    def capture_destinations(*args: object, **kwargs: object) -> attempt_module.AttemptArtifacts | str:
        nonlocal descriptor
        prepared = original_destinations(*args, **kwargs)  # type: ignore[arg-type]
        if isinstance(prepared, attempt_module.AttemptArtifacts) and prepared.transcript is not None:
            descriptor = prepared.transcript.parent_descriptor
        return prepared

    def fail_temporary_cleanup(path: Path) -> None:
        raise OSError(f"temporary cleanup failed; retained path: {path}")

    def close_then_interrupt(selected: int) -> None:
        original_close(selected)
        if selected == descriptor:
            raise primary

    monkeypatch.setattr(attempt_module, "_artifact_destinations", capture_destinations)
    monkeypatch.setattr(attempt_module.shutil, "rmtree", fail_temporary_cleanup)
    monkeypatch.setattr(attempt_module.os, "close", close_then_interrupt)
    with pytest.raises(MemoryError) as excinfo:
        _run_existing(
            repo,
            contract,
            FakeGit(repo),
            environment={attempt_module.TRANSCRIPT_ENV: str(tmp_path / "transcript")},
        )

    assert excinfo.value is primary
    notes = "\n".join(primary.__notes__)
    assert "temporary cleanup failed" in notes
    assert "retained path:" in notes
    assert "artifact descriptor close failed" in notes


def test_artifact_created_during_parent_open_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, contract, _ = _repo(tmp_path)
    destination = tmp_path / "raced"
    destination.write_bytes(b"caller")
    original_lexists = attempt_module.os.path.lexists
    monkeypatch.setattr(
        attempt_module.os.path,
        "lexists",
        lambda path: False if Path(path) == destination else original_lexists(path),
    )
    result, _ = _run_existing(
        repo,
        contract,
        FakeGit(repo),
        environment={attempt_module.TRANSCRIPT_ENV: str(destination)},
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "already exists" in result.message
    assert destination.read_bytes() == b"caller"


def test_frozen_contract_failure_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, contract, _ = _repo(tmp_path)
    monkeypatch.setattr(attempt_module, "_freeze_contract", lambda *args: (_ for _ in ()).throw(OSError("copy")))
    result, _ = _run_existing(repo, contract, FakeGit(repo))
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "freeze contract" in result.message


def test_contract_change_during_freeze_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, contract, _ = _repo(tmp_path)

    def change(source: Path, destination: Path) -> None:
        del source
        destination.write_text("id: changed\ntask: changed\nwritable_paths:\n  - app.py\n", encoding="utf-8")

    monkeypatch.setattr(attempt_module, "_freeze_contract", change)
    result, _ = _run_existing(repo, contract, FakeGit(repo))
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "contract changed" in result.message


def test_samefile_failure_refuses_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, contract, _ = _repo(tmp_path)
    monkeypatch.setattr(attempt_module.os.path, "samefile", lambda *args: (_ for _ in ()).throw(OSError("gone")))
    result, _ = _run_existing(repo, contract, FakeGit(repo))
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "working-tree root" in result.message


def test_head_failure_and_unsafe_or_unreadable_paths_are_refused(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    head_failed = FakeGit(repo)
    head_failed.overrides["head"] = GitResult(1, b"", b"no head")
    result, _ = _run_existing(repo, contract, head_failed)
    assert "no commit" in result.message

    contract.write_text("id: x\ntask: y\nwritable_paths:\n  - '*.py'\n", encoding="utf-8")
    unsafe = FakeGit(repo)
    unsafe.overrides["ls-files"] = GitResult(0, b"../app.py\0app.py\0missing.py\0", b"")
    result, _ = _run_existing(repo, contract, unsafe)
    assert result.code is AttemptCode.OK


def test_non_file_tracked_path_is_skipped(tmp_path: Path) -> None:
    repo, contract, target = _repo(tmp_path)
    target.unlink()
    target.mkdir()
    result, _ = _run_existing(repo, contract, FakeGit(repo))
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "no existing tracked" in result.message


@pytest.mark.parametrize("symlink_kind", ["leaf", "ancestor"])
def test_tracked_symlinks_are_excluded_while_regular_sibling_remains_writable(
    tmp_path: Path,
    symlink_kind: str,
) -> None:
    repo, contract, target = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "app.py").write_text("outside = True\n", encoding="utf-8")
    if symlink_kind == "leaf":
        target.unlink()
        target.symlink_to(outside / "app.py")
        unsafe_path = b"app.py"
    else:
        linked = repo / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        unsafe_path = b"linked/app.py"

    contract.write_text("id: x\ntask: y\nwritable_paths:\n  - '*.py'\n", encoding="utf-8")
    only_symlink = FakeGit(repo)
    only_symlink.overrides["ls-files"] = GitResult(0, unsafe_path + b"\0", b"")
    refused, _ = _run_existing(repo, contract, only_symlink)
    assert refused.code is AttemptCode.ATTEMPT_FAILED
    assert "no existing tracked" in refused.message

    if symlink_kind == "ancestor":
        sibling = repo / "regular" / "sibling.py"
        sibling.parent.mkdir()
        sibling_path = b"regular/sibling.py"
    else:
        sibling = repo / "sibling.py"
        sibling_path = b"sibling.py"
    sibling.write_text("sibling = True\n", encoding="utf-8")
    with_sibling = FakeGit(repo)
    with_sibling.overrides["ls-files"] = GitResult(
        0,
        unsafe_path + b"\0" + sibling_path + b"\0",
        b"",
    )
    pi = FakePi()
    accepted, _ = _run_existing(repo, contract, with_sibling, pi=pi)
    assert accepted.code is AttemptCode.OK
    assert pi.environment is not None
    context = json.loads(pi.environment[attempt_module.MUTATION_CONTEXT_ENV])
    assert context["revisions"] == {
        os.fsdecode(sibling_path): attempt_module.file_sha256(sibling.read_bytes())
    }


def test_tracked_file_read_preserves_unexpected_exception_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, contract, _ = _repo(tmp_path)
    failure = KeyboardInterrupt("stop")
    original_close = attempt_module.os.close
    close_failed = False

    def fail_one_close(descriptor: int) -> None:
        nonlocal close_failed
        original_close(descriptor)
        if not close_failed:
            close_failed = True
            raise OSError("close failed")

    monkeypatch.setattr(attempt_module.os, "read", lambda *args: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(attempt_module.os, "close", fail_one_close)

    with pytest.raises(KeyboardInterrupt) as raised:
        _run_existing(repo, contract, FakeGit(repo))

    assert raised.value is failure
    assert failure.__notes__ == ["secondary descriptor cleanup failure: close failed"]


def test_tracked_descriptor_io_cleanup_failure_is_named_by_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, contract, _ = _repo(tmp_path)
    monkeypatch.setattr(
        attempt_module,
        "_read_tracked_regular",
        lambda *args: (_ for _ in ()).throw(OSError("tracked descriptor close failed")),
    )
    result, _ = _run_existing(repo, contract, FakeGit(repo))
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "cannot inspect tracked writable file app.py" in result.message
    assert "tracked descriptor close failed" in result.message


def test_tracked_descriptor_cleanup_preserves_first_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _ = _repo(tmp_path)
    original_close = attempt_module.os.close
    first = OSError("first close")
    second = OSError("second close")
    failures = iter((first, second))

    def fail_close(descriptor: int) -> None:
        original_close(descriptor)
        raise next(failures)

    monkeypatch.setattr(attempt_module.os, "close", fail_close)

    with pytest.raises(OSError) as raised:
        attempt_module._read_tracked_regular(repo, "app.py")

    assert raised.value is first
    assert first.__notes__ == ["secondary descriptor cleanup failure: second close"]


def test_tracked_descriptor_handoff_failure_closes_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = MemoryError("cannot record descriptor")
    original_close = attempt_module.os.close
    descriptor: int | None = None

    class RefusingOwner(list[int]):
        def append(self, value: int) -> None:
            del value
            raise primary

    def close_then_fail(selected: int) -> None:
        nonlocal descriptor
        descriptor = selected
        original_close(selected)
        raise OSError("tracked handoff close failed")

    monkeypatch.setattr(attempt_module.os, "close", close_then_fail)
    with pytest.raises(MemoryError) as excinfo:
        attempt_module._open_owned_descriptor(
            RefusingOwner(),
            tmp_path,
            os.O_RDONLY | os.O_DIRECTORY,
        )
    assert excinfo.value is primary
    assert descriptor is not None
    assert primary.__notes__ == ["secondary descriptor cleanup failure: tracked handoff close failed"]
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_engine_package_must_exist(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    result, _ = _run_existing(
        repo,
        contract,
        FakeGit(repo),
        environment={attempt_module.ENGINE_REPO_ENV: str(tmp_path / "missing-engine")},
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "engine package" in result.message


def test_relative_engine_repo_is_resolved_from_attempt_repo(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    (repo / "engine").symlink_to(Path(__file__).parents[1], target_is_directory=True)
    pi = FakePi()
    output = io.BytesIO()
    result = attempt_module.attempt(
        repo,
        contract,
        "model",
        environment={attempt_module.ENGINE_REPO_ENV: "engine"},
        git_runner=FakeGit(repo),
        pi_runner=pi,
        stdout=output,
        stderr=io.BytesIO(),
    )
    assert result.code is AttemptCode.OK
    assert pi.environment is not None
    assert pi.environment[attempt_module.ENGINE_REPO_ENV] == str(Path(__file__).parents[1])


def test_engine_repo_defaults_to_the_module_checkout(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    pi = FakePi()
    result = attempt_module.attempt(
        repo,
        contract,
        "model",
        environment={},
        git_runner=FakeGit(repo),
        pi_runner=pi,
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
    )
    assert result.code is AttemptCode.OK
    assert pi.environment is not None
    assert pi.environment[attempt_module.ENGINE_REPO_ENV] == str(Path(__file__).parents[1])


def test_unresolvable_relative_engine_repo_is_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, contract, _ = _repo(tmp_path)
    configured = repo / "engine"
    original_resolve = Path.resolve

    def fail_configured(path: Path, strict: bool = False) -> Path:
        if path == configured:
            raise OSError("cannot resolve")
        return original_resolve(path, strict)

    monkeypatch.setattr(Path, "resolve", fail_configured)
    result, _ = _run_existing(
        repo,
        contract,
        FakeGit(repo),
        environment={attempt_module.ENGINE_REPO_ENV: "engine"},
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "cannot resolve engine repository" in result.message


def test_artifact_parent_is_pinned_before_path_is_redirected(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    moved = tmp_path / "moved-artifacts"

    class SwappingPi(FakePi):
        def run(self, *args: object, **kwargs: object) -> int:
            result = super().run(*args, **kwargs)  # type: ignore[arg-type]
            artifacts.rename(moved)
            artifacts.symlink_to(repo, target_is_directory=True)
            return result

    output = io.BytesIO()
    result = attempt_module.attempt(
        repo,
        contract,
        "model",
        environment={
            attempt_module.ENGINE_REPO_ENV: str(Path(__file__).parents[1]),
            attempt_module.TRANSCRIPT_ENV: str(artifacts / "transcript"),
        },
        git_runner=FakeGit(repo),
        pi_runner=SwappingPi(),
        stdout=output,
        stderr=io.BytesIO(),
    )
    assert result.code is AttemptCode.OK
    assert not (artifacts / "transcript").exists()
    assert (moved / "transcript").read_bytes() == output.getvalue()
    assert not (repo / "transcript").exists()


def test_destination_created_after_preparation_is_never_overwritten(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    destination = tmp_path / "transcript"

    class RacingPi(FakePi):
        def run(self, *args: object, **kwargs: object) -> int:
            result = super().run(*args, **kwargs)  # type: ignore[arg-type]
            destination.write_bytes(b"caller")
            return result

    result = attempt_module.attempt(
        repo,
        contract,
        "model",
        environment={
            attempt_module.ENGINE_REPO_ENV: str(Path(__file__).parents[1]),
            attempt_module.TRANSCRIPT_ENV: str(destination),
        },
        git_runner=FakeGit(repo),
        pi_runner=RacingPi(),
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert destination.read_bytes() == b"caller"


def test_transcript_patch_and_git_diff_publication_failures_are_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    original_publish_file = attempt_module._publish_file
    monkeypatch.setattr(attempt_module, "_publish_file", lambda *args: (_ for _ in ()).throw(OSError("transcript")))
    result, *_ = _run(
        tmp_path,
        environment={attempt_module.TRANSCRIPT_ENV: str(output / "transcript")},
    )
    assert "publish transcript" in result.message

    monkeypatch.setattr(attempt_module, "_publish_file", original_publish_file)
    repo = tmp_path / "second" / "repo"
    repo.parent.mkdir()
    git = FakeGit(repo)
    git.overrides["diff"] = GitResult(1, b"", b"diff failed")
    result, *_ = _run(tmp_path / "second", git=git)
    assert "produce attempt patch" in result.message

    monkeypatch.setattr(attempt_module, "_publish_bytes", lambda *args: (_ for _ in ()).throw(ValueError("patch")))
    (tmp_path / "third").mkdir()
    result, *_ = _run(
        tmp_path / "third",
        environment={attempt_module.PATCH_ENV: str(tmp_path / "patch")},
    )
    assert "publish patch" in result.message


def test_pi_nonzero_preserves_artifacts_then_refuses(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    patch = output / "patch"
    transcript = output / "transcript"
    result, stdout, _, _, _ = _run(
        tmp_path,
        pi=FakePi(exit_code=17),
        environment={attempt_module.PATCH_ENV: str(patch), attempt_module.TRANSCRIPT_ENV: str(transcript)},
    )
    assert result == AttemptResult(
        AttemptCode.ATTEMPT_FAILED,
        "Pi exited with status 17",
        model="provider/model",
        command_exit=17,
    )
    assert stdout == transcript.read_bytes()
    assert patch.exists()


def test_pi_start_failure_is_named(tmp_path: Path) -> None:
    class BrokenPi(FakePi):
        def run(self, *args: object, **kwargs: object) -> int:
            raise OSError("pi missing")

    result, *_ = _run(tmp_path, pi=BrokenPi())
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "pi missing" in result.message


@pytest.mark.parametrize("pi_exit", [0, 17])
def test_attempt_temporary_cleanup_failure_overrides_pending_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pi_exit: int,
) -> None:
    def fail_cleanup(path: Path) -> None:
        raise OSError(f"cannot remove {path.name}")

    monkeypatch.setattr(attempt_module.shutil, "rmtree", fail_cleanup)
    result, *_ = _run(tmp_path, pi=FakePi(exit_code=pi_exit))
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert result.command_exit == pi_exit
    assert "cannot remove attempt temporary directory" in result.message
    assert "retained path:" in result.message


def test_unexpected_exception_identity_survives_secondary_attempt_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = KeyboardInterrupt("stop")

    class InterruptingPi(FakePi):
        def run(self, *args: object, **kwargs: object) -> int:
            raise primary

    monkeypatch.setattr(
        attempt_module.shutil,
        "rmtree",
        lambda path: (_ for _ in ()).throw(OSError(f"cannot remove {path.name}")),
    )
    with pytest.raises(KeyboardInterrupt) as excinfo:
        _run(tmp_path, pi=InterruptingPi())
    assert excinfo.value is primary
    assert any("secondary cleanup failure" in note and "retained path" in note for note in primary.__notes__)


def test_unexpected_attempt_cleanup_failure_preserves_its_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = MemoryError("cleanup exhausted")
    monkeypatch.setattr(attempt_module.shutil, "rmtree", lambda path: (_ for _ in ()).throw(primary))
    with pytest.raises(MemoryError) as excinfo:
        _run(tmp_path)
    assert excinfo.value is primary
    assert any("retained path:" in note for note in primary.__notes__)


def test_artifact_primary_failure_keeps_cleanup_detail_and_retained_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "out"
    output.mkdir()

    monkeypatch.setattr(
        attempt_module.shutil,
        "copyfileobj",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("write failed")),
    )
    real_unlink = attempt_module.os.unlink

    def fail_artifact_unlink(path: str, *, dir_fd: int | None = None) -> None:
        if path.startswith(".satyrn-attempt-"):
            raise OSError("unlink failed")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(attempt_module.os, "unlink", fail_artifact_unlink)
    result, *_ = _run(
        tmp_path,
        environment={attempt_module.TRANSCRIPT_ENV: str(output / "transcript")},
    )
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "write failed" in result.message
    assert "unlink failed" in result.message
    assert "retained path:" in result.message


def test_artifact_unexpected_exception_identity_survives_secondary_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    primary = MemoryError("write exhausted")
    monkeypatch.setattr(attempt_module.shutil, "copyfileobj", lambda *args, **kwargs: (_ for _ in ()).throw(primary))
    real_unlink = attempt_module.os.unlink

    def fail_artifact_unlink(path: str, *, dir_fd: int | None = None) -> None:
        if path.startswith(".satyrn-attempt-"):
            raise OSError("unlink failed")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(attempt_module.os, "unlink", fail_artifact_unlink)
    with pytest.raises(MemoryError) as excinfo:
        _run(
            tmp_path,
            environment={attempt_module.TRANSCRIPT_ENV: str(output / "transcript")},
        )
    assert excinfo.value is primary
    assert any("secondary cleanup failure" in note and "retained path" in note for note in primary.__notes__)


def test_transcript_spool_close_failure_has_result_or_primary_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = Path.open

    class CloseFailingOutput:
        def __init__(self, wrapped: BinaryIO) -> None:
            self.wrapped = wrapped

        def write(self, content: bytes) -> int:
            return self.wrapped.write(content)

        def flush(self) -> None:
            self.wrapped.flush()

        def fileno(self) -> int:
            return self.wrapped.fileno()

        def close(self) -> None:
            self.wrapped.close()
            raise OSError("spool close failed")

    def failing_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> object:
        opened = original_open(path, mode, buffering, encoding, errors, newline)
        return CloseFailingOutput(cast(BinaryIO, opened)) if path.name == "transcript.jsonl" else opened

    monkeypatch.setattr(Path, "open", failing_open)
    result, *_ = _run(tmp_path)
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "spool close failed" in result.message

    second = tmp_path / "second"
    second.mkdir()
    primary = MemoryError("Pi exhausted")

    class ExplodingPi(FakePi):
        def run(self, *args: object, **kwargs: object) -> int:
            raise primary

    with pytest.raises(MemoryError) as excinfo:
        _run(second, pi=ExplodingPi())
    assert excinfo.value is primary
    assert any("spool close failed" in note for note in primary.__notes__)


def test_protected_root_resolution_failure_is_conservatively_inside(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    original_resolve = Path.resolve

    def fail_selected(path: Path, strict: bool = False) -> Path:
        if path == parent:
            raise OSError("changed")
        return original_resolve(path, strict)

    monkeypatch.setattr(Path, "resolve", fail_selected)
    assert attempt_module._inside_protected_root(
        parent,
        ((root, attempt_module._identity(root.stat())),),
    )


def test_successful_artifact_cleanup_failure_is_named_and_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    destination = parent / "transcript"
    real_unlink = attempt_module.os.unlink

    def fail_artifact_unlink(path: str, *, dir_fd: int | None = None) -> None:
        if path.startswith(".satyrn-attempt-"):
            raise OSError("unlink failed")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(attempt_module.os, "unlink", fail_artifact_unlink)
    target = _artifact_destination(destination)
    try:
        with pytest.raises(OSError) as excinfo:
            attempt_module._publish_bytes(b"complete", target)
    finally:
        attempt_module._close_destinations((target,))
    assert destination.read_bytes() == b"complete"
    assert "unlink failed" in str(excinfo.value)
    assert "retained path" in str(excinfo.value)


def test_artifact_parent_close_failure_becomes_named_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    target = _artifact_destination(parent / "first")
    real_close = attempt_module.os.close

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        raise OSError("parent close failed")

    monkeypatch.setattr(attempt_module.os, "close", close_then_fail)
    try:
        with pytest.raises(OSError, match="parent close failed") as excinfo:
            attempt_module._publish_bytes(b"complete", target)
            attempt_module._close_destinations((target,))
    finally:
        monkeypatch.setattr(attempt_module.os, "close", real_close)
        attempt_module._close_destinations((target,))
    assert "parent close failed" in str(excinfo.value)
    assert any("descriptor ownership released" in note for note in excinfo.value.__notes__)
    assert target.path.read_bytes() == b"complete"


def test_artifact_parent_close_failure_is_a_note_on_existing_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    first = _artifact_destination(parent / "first")
    second = _artifact_destination(parent / "second")
    real_close = attempt_module.os.close
    failures = iter((OSError("second close failed"), OSError("first close failed")))

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        raise next(failures)

    monkeypatch.setattr(attempt_module.os, "close", close_then_fail)
    with pytest.raises(OSError) as excinfo:
        attempt_module._close_destinations((first, second))
    assert str(excinfo.value) == "second close failed"
    assert any("secondary cleanup failure" in note and "first close failed" in note for note in excinfo.value.__notes__)
    assert first.parent_descriptor is second.parent_descriptor is None


def test_unexpected_artifact_cleanup_failure_preserves_its_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    target = _artifact_destination(parent / "target")
    primary = KeyboardInterrupt("close interrupted")
    real_close = attempt_module.os.close

    def close_then_interrupt(descriptor: int) -> None:
        real_close(descriptor)
        raise primary

    monkeypatch.setattr(attempt_module.os, "close", close_then_interrupt)
    with pytest.raises(KeyboardInterrupt) as excinfo:
        attempt_module._close_destinations((target,))
    assert excinfo.value is primary
    assert target.parent_descriptor is None
    assert any("descriptor ownership released" in note for note in primary.__notes__)


@pytest.mark.parametrize(
    ("preparation_error", "cleanup_error", "returned"),
    [
        (OSError("open failed"), OSError("close failed"), True),
        (OSError("open failed"), MemoryError("close failed"), False),
        (MemoryError("open failed"), OSError("close failed"), False),
    ],
)
def test_artifact_preparation_preserves_cleanup_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preparation_error: BaseException,
    cleanup_error: BaseException,
    returned: bool,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    monkeypatch.setattr(
        attempt_module.os,
        "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(preparation_error),
    )
    monkeypatch.setattr(
        attempt_module,
        "_close_destinations",
        lambda *args: (_ for _ in ()).throw(cleanup_error),
    )
    if returned:
        result = attempt_module._artifact_destinations(
            (),
            {attempt_module.TRANSCRIPT_ENV: str(parent / "transcript")},
        )
        assert isinstance(result, str)
        assert "open failed" in result and "close failed" in result
    else:
        expected = cleanup_error if isinstance(preparation_error, OSError) else preparation_error
        with pytest.raises(type(expected)) as excinfo:
            attempt_module._artifact_destinations(
                (),
                {attempt_module.TRANSCRIPT_ENV: str(parent / "transcript")},
            )
        assert excinfo.value is expected
        assert any("failed" in note for note in expected.__notes__)


def test_artifact_descriptor_ownership_and_cleanup_precedence_are_single_use(tmp_path: Path) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    target = _artifact_destination(parent / "target")
    attempt_module._close_destinations((target,))
    attempt_module._close_destinations((target,))
    with pytest.raises(RuntimeError, match="already closed"):
        target.descriptor()

    primary = MemoryError("first cleanup")
    pending, selected = attempt_module._merge_attempt_cleanup(
        "model",
        AttemptResult(AttemptCode.OK, model="model"),
        None,
        primary,
        OSError("second cleanup"),
        "second detail",
    )
    assert pending is not None and pending.code is AttemptCode.OK
    assert selected is primary
    assert any("second detail" in note for note in primary.__notes__)


def test_non_io_cleanup_failure_keeps_exact_identity() -> None:
    primary = KeyboardInterrupt("cleanup interrupted")
    with pytest.raises(KeyboardInterrupt) as excinfo:
        attempt_module._raise_cleanup_failure(primary, "cleanup detail")
    assert excinfo.value is primary
    assert primary.__notes__ == ["cleanup detail"]


def test_artifact_fdopen_failure_preserves_close_failure_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = os.open(tmp_path / "temporary", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    primary = MemoryError("fdopen failed")
    monkeypatch.setattr(attempt_module.os, "fdopen", lambda *args, **kwargs: (_ for _ in ()).throw(primary))
    real_close = attempt_module.os.close
    monkeypatch.setattr(
        attempt_module.os,
        "close",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("raw close failed")),
    )
    try:
        with pytest.raises(MemoryError) as excinfo:
            attempt_module._write_artifact_temporary(descriptor, lambda output: None)
        assert excinfo.value is primary
        assert any("raw close failed" in note for note in primary.__notes__)
    finally:
        real_close(descriptor)


@pytest.mark.parametrize("primary", [None, MemoryError("write failed")])
def test_artifact_stream_close_failure_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primary: MemoryError | None,
) -> None:
    descriptor = os.open(tmp_path / "temporary", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    original_fdopen = attempt_module.os.fdopen

    class CloseFailingOutput:
        def __init__(self, wrapped: BinaryIO) -> None:
            self.wrapped = wrapped

        def write(self, content: bytes) -> int:
            return self.wrapped.write(content)

        def flush(self) -> None:
            self.wrapped.flush()

        def fileno(self) -> int:
            return self.wrapped.fileno()

        def close(self) -> None:
            self.wrapped.close()
            raise OSError("stream close failed")

    monkeypatch.setattr(
        attempt_module.os,
        "fdopen",
        lambda *args, **kwargs: CloseFailingOutput(cast(BinaryIO, original_fdopen(*args, **kwargs))),
    )

    def write(output: BinaryIO) -> None:
        if primary is not None:
            raise primary
        output.write(b"complete")

    if primary is None:
        with pytest.raises(OSError, match="stream close failed"):
            attempt_module._write_artifact_temporary(descriptor, write)
    else:
        with pytest.raises(MemoryError) as excinfo:
            attempt_module._write_artifact_temporary(descriptor, write)
        assert excinfo.value is primary
        assert any("stream close failed" in note for note in primary.__notes__)


def test_artifact_temporary_name_exhaustion_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        attempt_module.os,
        "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileExistsError("collision")),
    )
    with pytest.raises(FileExistsError, match="cannot allocate"):
        attempt_module._create_artifact_temporary(1)


def test_cli_model_flag_and_environment_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_attempt(repo: Path, contract: Path, model: str) -> AttemptResult:
        del repo, contract
        seen.append(model)
        return AttemptResult(AttemptCode.OK, model=model, command_exit=0)

    monkeypatch.setattr(cli, "attempt", fake_attempt)
    monkeypatch.setenv(attempt_module.MODEL_ENV, "env/model")
    assert cli.main(["attempt", "--model", "flag/model", "contract.yaml"]) == 0
    assert cli.main(["attempt", "contract.yaml"]) == 0
    assert seen == ["flag/model", "env/model"]


def test_cli_accepts_dash_leading_model_and_contract_after_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[Path, str]] = []

    def fake_attempt(repo: Path, contract: Path, model: str) -> AttemptResult:
        del repo
        seen.append((contract, model))
        return AttemptResult(AttemptCode.OK, model=model, command_exit=0)

    monkeypatch.setattr(cli, "attempt", fake_attempt)
    assert cli.main(["attempt", "--model=-provider/model", "--", "-contract.yaml"]) == 0
    assert seen == [(Path("-contract.yaml"), "-provider/model")]


def test_cli_missing_model_is_usage(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv(attempt_module.MODEL_ENV, raising=False)
    assert cli.main(["attempt", "contract.yaml"]) == ExitCode.USAGE
    assert "USAGE" in capsys.readouterr().err


def test_cli_prints_named_attempt_refusal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "attempt",
        lambda *args: AttemptResult(AttemptCode.ATTEMPT_FAILED, "no Pi", model="m"),
    )
    assert cli.main(["attempt", "--model", "m", "contract.yaml"]) == ExitCode.ATTEMPT_FAILED
    assert "ATTEMPT_FAILED: no Pi" in capsys.readouterr().err


def test_cli_reserves_exit_one_for_broken_transcript_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object) -> AttemptResult:
        del args
        raise BrokenPipeError

    monkeypatch.setattr(cli, "attempt", broken)
    monkeypatch.setattr(cli, "_silence_broken_stdout", lambda: None)
    assert cli.main(["attempt", "--model", "m", "contract.yaml"]) == 1
