"""Default-tier tests for E5's pure boundaries and injected attempt seams."""

import io
import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO

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
            case ("rev-parse", _):
                key = "head"
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
    assert command[:7] == (
        "pi",
        "--print",
        "--mode",
        "json",
        "--no-session",
        "--model",
        "provider/model",
    )
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
