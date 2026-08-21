"""E5 integration: real Git/process plus shipped TypeScript/Python mutation."""

import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from satyrn_engine.attempt import (
    ENGINE_REPO_ENV,
    PATCH_ENV,
    TRANSCRIPT_ENV,
    AttemptCode,
    AttemptResult,
    attempt,
)
from satyrn_engine.delivery import DeliveryCode, deliver

ROOT = Path(__file__).parents[1]
FAKE_PI = ROOT / "tests" / "fixtures" / "attempt" / "fake_pi.py"

pytestmark = pytest.mark.integration


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    if shutil.which("node") is None:
        pytest.skip("Node is required for the E5 integration tier")
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    target = repo / "app.py"
    target.write_text("def value():\n    return 1\n", encoding="utf-8")
    contract = repo / "contract.yaml"
    contract.write_text(
        "id: e5-integration\ntask: return two\nwritable_paths:\n  - app.py\n",
        encoding="utf-8",
    )
    assert _git(repo, "add", "app.py", "contract.yaml").returncode == 0
    committed = _git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    assert committed.returncode == 0, committed.stderr

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_pi = bin_dir / "pi"
    shutil.copyfile(FAKE_PI, fake_pi)
    fake_pi.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = str(bin_dir) + os.pathsep + environment.get("PATH", "")
    environment[ENGINE_REPO_ENV] = str(ROOT)
    return repo, contract, target, environment


def _attempt(
    repo: Path,
    contract: Path,
    environment: dict[str, str],
) -> tuple[AttemptResult, bytes, bytes]:
    stdout = io.BytesIO()
    with tempfile.TemporaryFile() as stderr:
        result = attempt(
            repo,
            contract,
            "fixture/model",
            environment=environment,
            stdout=stdout,
            stderr=stderr,
        )
        stderr.seek(0)
        error_bytes = stderr.read()
    return result, stdout.getvalue(), error_bytes


def test_attempt_uses_shipped_e4_mutator_and_exports_artifacts(tmp_path: Path) -> None:
    repo, contract, target, environment = _fixture(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    patch = artifacts / "patch.diff"
    transcript = artifacts / "transcript.jsonl"
    environment[PATCH_ENV] = str(patch)
    environment[TRANSCRIPT_ENV] = str(transcript)

    result, stdout, stderr = _attempt(repo, contract, environment)

    assert result.code is AttemptCode.OK
    assert target.read_text(encoding="utf-8") == "def value():\n    return 2\n"
    assert b"-    return 1" in patch.read_bytes()
    assert b"+    return 2" in patch.read_bytes()
    assert transcript.read_bytes() == stdout
    assert b'"code":"OK"' in stdout
    assert b"exercise_mutator:" not in stderr


@pytest.mark.parametrize(
    ("mode", "code", "patch_exists"),
    [
        ("nochange", AttemptCode.OK, False),
        ("fail", AttemptCode.ATTEMPT_FAILED, False),
        ("refuse", AttemptCode.OK, False),
    ],
)
def test_attempt_preserves_transcript_for_no_change_failure_and_refusal(
    tmp_path: Path,
    mode: str,
    code: AttemptCode,
    patch_exists: bool,
) -> None:
    repo, contract, target, environment = _fixture(tmp_path)
    transcript = tmp_path / "transcript.jsonl"
    patch = tmp_path / "patch.diff"
    environment["SATYRN_FAKE_PI_MODE"] = mode
    environment[TRANSCRIPT_ENV] = str(transcript)
    environment[PATCH_ENV] = str(patch)

    result, stdout, _ = _attempt(repo, contract, environment)

    assert result.code is code
    assert transcript.read_bytes() == stdout
    assert patch.exists() is patch_exists
    assert target.read_text(encoding="utf-8") == "def value():\n    return 1\n"


def test_attempt_rejects_artifacts_in_any_registered_worktree_and_git_admin(
    tmp_path: Path,
) -> None:
    repo, contract, _, environment = _fixture(tmp_path)
    sibling = tmp_path / "sibling-worktree"
    added = _git(repo, "worktree", "add", "--detach", str(sibling), "HEAD")
    assert added.returncode == 0, added.stderr
    git_common = Path(os.fsdecode(_git(repo, "rev-parse", "--git-common-dir").stdout.strip()))
    if not git_common.is_absolute():
        git_common = repo / git_common

    for destination in (sibling / "transcript", git_common / "transcript"):
        selected = dict(environment)
        selected[TRANSCRIPT_ENV] = str(destination)
        result, stdout, _ = _attempt(repo, contract, selected)
        assert result.code is AttemptCode.ATTEMPT_FAILED
        assert "every registered worktree" in result.message
        assert stdout == b""
        assert not destination.exists()


def test_e3_delivery_wraps_same_attempt_and_keeps_source_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, contract, target, environment = _fixture(tmp_path)
    transcript = tmp_path / "delivery-transcript.jsonl"
    environment[TRANSCRIPT_ENV] = str(transcript)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    before_head = _git(repo, "rev-parse", "HEAD").stdout

    receipt = deliver(
        repo,
        contract,
        (
            "uv",
            "run",
            "--project",
            str(ROOT),
            "satyrn-engine",
            "attempt",
            "--model",
            "fixture/model",
            "contract.yaml",
        ),
        timeout=30,
    )

    assert receipt.code is DeliveryCode.OK
    assert receipt.changed_paths == ("app.py",)
    assert receipt.candidate_ref == "refs/satyrn/candidates/e5-integration/head"
    assert transcript.is_file()
    assert target.read_text(encoding="utf-8") == "def value():\n    return 1\n"
    assert _git(repo, "rev-parse", "HEAD").stdout == before_head
    assert _git(repo, "status", "--porcelain").stdout == b""
