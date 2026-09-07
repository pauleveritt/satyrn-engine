"""E5 integration: real Git/process plus shipped TypeScript/Python mutation."""

import io
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
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


def _wait_for(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"timed out waiting for {path}"
        time.sleep(0.01)


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


def test_attempt_excludes_real_tracked_symlink_before_pi(tmp_path: Path) -> None:
    repo, contract, target, environment = _fixture(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("outside = True\n", encoding="utf-8")
    target.unlink()
    target.symlink_to(outside)
    assert _git(repo, "add", "app.py").returncode == 0
    committed = _git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "track symlink",
    )
    assert committed.returncode == 0, committed.stderr

    result, stdout, stderr = _attempt(repo, contract, environment)

    assert result.code is AttemptCode.ATTEMPT_FAILED
    assert "no existing tracked writable file" in result.message
    assert stdout == b""
    assert stderr == b""
    assert outside.read_text(encoding="utf-8") == "outside = True\n"


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
            "--model=fixture/model",
            "--",
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


def test_group_sigterm_during_delivery_preserves_attempt_spool_before_cleanup(tmp_path: Path) -> None:
    repo, contract, _, environment = _fixture(tmp_path)
    ready = tmp_path / "pi-ready"
    marker = tmp_path / "late-write"
    transcript = tmp_path / "interrupted-transcript.jsonl"
    environment.update(
        {
            "SATYRN_FAKE_PI_MODE": "delay",
            "SATYRN_FAKE_PI_READY": str(ready),
            "SATYRN_FAKE_PI_MARKER": str(marker),
            TRANSCRIPT_ENV: str(transcript),
        }
    )
    engine = Path(os.sys.executable).with_name("satyrn-engine")
    process = subprocess.Popen(
        [
            str(engine),
            "deliver",
            "--repo",
            str(repo),
            str(contract),
            "--",
            str(engine),
            "attempt",
            "--model",
            "fixture/model",
            "--",
            "contract.yaml",
        ],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    _wait_for(ready)
    os.killpg(process.pid, signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == 128 + signal.SIGTERM
    assert stdout == b""
    assert b'"type": "agent_start"' in transcript.read_bytes()
    assert b"Pi exited with status" in stderr
    time.sleep(0.75)
    assert not marker.exists()
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)


@pytest.mark.skipif(os.name != "posix", reason="signal forwarding proof is POSIX-only")
@pytest.mark.parametrize("termination_signal", (signal.SIGTERM, signal.SIGHUP))
def test_direct_termination_forwards_to_pi_and_preserves_attempt_spool(
    tmp_path: Path,
    termination_signal: signal.Signals,
) -> None:
    repo, contract, _, environment = _fixture(tmp_path)
    ready = tmp_path / "pi-ready"
    marker = tmp_path / "late-write"
    transcript = tmp_path / "interrupted-transcript.jsonl"
    environment.update(
        {
            "SATYRN_FAKE_PI_MODE": "delay",
            "SATYRN_FAKE_PI_READY": str(ready),
            "SATYRN_FAKE_PI_MARKER": str(marker),
            TRANSCRIPT_ENV: str(transcript),
        }
    )
    engine = Path(os.sys.executable).with_name("satyrn-engine")
    process = subprocess.Popen(
        [str(engine), "attempt", "--model", "fixture/model", str(contract)],
        cwd=repo,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    _wait_for(ready)
    os.kill(process.pid, termination_signal)
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == 10
    assert transcript.read_bytes() == stdout
    assert b'"type": "agent_start"' in transcript.read_bytes()
    assert b"Pi exited with status" in stderr
    time.sleep(0.75)
    assert not marker.exists()
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)


def test_transcript_is_readable_and_streaming_while_pi_still_runs(tmp_path: Path) -> None:
    """E10's whole point (spec section 1): the transcript destination is
    the real file Pi writes into, complete-as-of-now at every moment --
    not a spool copied over only after Pi exits. A test that only checked
    the final content would already pass against the pre-E10 engine and
    prove nothing, so this reads the destination while the child is still
    blocked mid-run, using the existing delay-mode fixture and its
    ready/marker idiom.
    """
    repo, contract, _, environment = _fixture(tmp_path)
    ready = tmp_path / "pi-ready"
    marker = tmp_path / "late-write"
    transcript = tmp_path / "streaming-transcript.jsonl"
    environment.update(
        {
            "SATYRN_FAKE_PI_MODE": "delay",
            "SATYRN_FAKE_PI_READY": str(ready),
            "SATYRN_FAKE_PI_MARKER": str(marker),
            TRANSCRIPT_ENV: str(transcript),
        }
    )
    engine = Path(os.sys.executable).with_name("satyrn-engine")
    process = subprocess.Popen(
        [str(engine), "attempt", "--model", "fixture/model", str(contract)],
        cwd=repo,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _wait_for(ready)
        # fake_pi.py writes its first transcript line, then the marker,
        # then sleeps for 30s -- waiting for the marker too proves the
        # child is past its first write and still alive when we read.
        _wait_for(marker)
        assert process.poll() is None, "Pi should still be blocked in its 30s sleep"
        assert b'"type": "agent_start"' in transcript.read_bytes()
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=10)


def test_dispatcher_timeout_waits_for_delivery_cleanup(tmp_path: Path) -> None:
    repo, contract, _, environment = _fixture(tmp_path)
    marker = tmp_path / "late-write"
    environment.update(
        {
            "SATYRN_FAKE_PI_MODE": "delay",
            "SATYRN_FAKE_PI_MARKER": str(marker),
        }
    )
    runner = tmp_path / "run-delivery.mjs"
    runner.write_text(
        f"""
import {{ spawn }} from "node:child_process";
import {{ AdapterRefusal, buildDeliveryInvocation, runDelivery }} from {json.dumps((ROOT / "packages" / "engine" / "orchestrator.ts").as_uri())};

const invocation = buildDeliveryInvocation(
  {json.dumps(str(repo))},
  {json.dumps(str(contract))},
  "fixture/model",
  {json.dumps(str(ROOT))},
  30,
);
try {{
  await runDelivery(spawn, invocation, 100, () => {{}}, 8_000);
  throw new Error("delivery unexpectedly completed");
}} catch (error) {{
  if (!(error instanceof AdapterRefusal) || error.code !== "ENGINE_TIMEOUT") throw error;
}}
""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", "--experimental-strip-types", str(runner)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    time.sleep(0.75)
    assert not marker.exists()
    assert _git(repo, "status", "--porcelain").stdout == b""
    worktrees = _git(repo, "worktree", "list", "--porcelain").stdout
    assert worktrees.count(b"worktree ") == 1
