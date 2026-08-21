"""Default-tier tests for E5's pure boundaries and injected attempt seams."""

import io
import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, cast

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
) -> tuple[AttemptResult, bytes]:
    output = io.BytesIO()
    result = attempt_module.attempt(
        repo,
        contract,
        "model",
        environment={attempt_module.ENGINE_REPO_ENV: str(Path(__file__).parents[1])} | (environment or {}),
        git_runner=git,
        pi_runner=FakePi(),
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
    monkeypatch.setattr(attempt_module.tempfile, "mkdtemp", lambda **kwargs: (_ for _ in ()).throw(OSError("full")))
    result, _ = _run_existing(repo, contract, FakeGit(repo))
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "temporary directory" in result.message


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


def test_non_file_tracked_path_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, contract, _ = _repo(tmp_path)
    original = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: False if path.name == "app.py" else original(path))
    result, _ = _run_existing(repo, contract, FakeGit(repo))
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "no existing tracked" in result.message


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


def test_artifact_parent_swap_is_refused_by_identity_recheck(tmp_path: Path) -> None:
    repo, contract, _ = _repo(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    moved = tmp_path / "moved-artifacts"

    class SwappingPi(FakePi):
        def run(self, *args: object, **kwargs: object) -> int:
            result = super().run(*args, **kwargs)  # type: ignore[arg-type]
            artifacts.rename(moved)
            artifacts.mkdir()
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
    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "parent changed" in result.message
    assert not (artifacts / "transcript").exists()
    assert not (moved / "transcript").exists()


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
    target = attempt_module._ArtifactDestination(destination, attempt_module._identity(parent.stat()))
    with pytest.raises(OSError) as excinfo:
        attempt_module._publish_bytes(b"complete", target)
    assert destination.read_bytes() == b"complete"
    assert "unlink failed" in str(excinfo.value)
    assert "retained path" in str(excinfo.value)


def test_artifact_parent_close_failure_becomes_named_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    real_close = attempt_module.os.close

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        raise OSError("parent close failed")

    monkeypatch.setattr(attempt_module.os, "close", close_then_fail)
    target = attempt_module._ArtifactDestination(parent / "first", attempt_module._identity(parent.stat()))
    with pytest.raises(OSError, match="retained path") as excinfo:
        attempt_module._publish_bytes(b"complete", target)
    assert "parent close failed" in str(excinfo.value)
    assert target.path.read_bytes() == b"complete"


def test_artifact_parent_close_failure_is_a_note_on_existing_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    target = attempt_module._ArtifactDestination(parent / "target", attempt_module._identity(parent.stat()))
    primary = OSError("link failed")
    monkeypatch.setattr(attempt_module.os, "link", lambda *args, **kwargs: (_ for _ in ()).throw(primary))
    real_close = attempt_module.os.close

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        raise OSError("parent close failed")

    monkeypatch.setattr(attempt_module.os, "close", close_then_fail)
    with pytest.raises(OSError) as excinfo:
        attempt_module._publish_bytes(b"complete", target)
    assert excinfo.value is primary
    assert any("parent close failed" in note for note in primary.__notes__)


def test_unexpected_artifact_cleanup_failure_preserves_its_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    target = attempt_module._ArtifactDestination(parent / "target", attempt_module._identity(parent.stat()))
    primary = KeyboardInterrupt("close interrupted")
    real_close = attempt_module.os.close

    def close_then_interrupt(descriptor: int) -> None:
        real_close(descriptor)
        raise primary

    monkeypatch.setattr(attempt_module.os, "close", close_then_interrupt)
    with pytest.raises(KeyboardInterrupt) as excinfo:
        attempt_module._publish_bytes(b"complete", target)
    assert excinfo.value is primary
    assert target.path.read_bytes() == b"complete"
    assert any("retained path:" in note for note in primary.__notes__)


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
