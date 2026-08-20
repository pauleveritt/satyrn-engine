"""Integration evidence for the shipped TypeScript loop breaker."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
BEHAVIOR_TEST = ROOT / "tests" / "test_loop_breaker.mjs"
REPLAY = ROOT / "tools" / "replay_guards.mjs"
FIXTURES = ROOT / "tests" / "fixtures" / "guards"
PACKAGE = ROOT / "packages" / "engine"

pytestmark = pytest.mark.integration


def _node() -> str:
    if executable := shutil.which("node"):
        return executable
    pytest.skip("Node is required for the TypeScript integration tier")


def _run_node(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_node(), "--experimental-strip-types", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_shipped_loop_breaker_behavior_suite_passes() -> None:
    completed = _run_node(
        "--test",
        "--experimental-test-coverage",
        "--test-coverage-lines=100",
        "--test-coverage-branches=100",
        "--test-coverage-functions=100",
        f"--test-coverage-include={PACKAGE / 'engine.ts'}",
        str(BEHAVIOR_TEST),
    )

    assert completed.returncode == 0, completed.stderr
    assert "pass 14" in completed.stdout
    assert "fail 0" in completed.stdout
    assert "engine.ts | 100.00 |   100.00 |  100.00" in completed.stdout


def test_all_evidence_fixtures_replay_in_one_process() -> None:
    completed = _run_node(str(REPLAY))

    assert completed.returncode == 0, completed.stderr
    summaries = [json.loads(line) for line in completed.stdout.splitlines()]
    assert summaries == [
        {
            "name": "loop-breaker-accepted-contract-run",
            "calls": 5,
            "blocked": 0,
            "firstBlock": None,
            "entries": 0,
        },
        {
            "name": "loop-breaker-accepted-magicmock-run",
            "calls": 22,
            "blocked": 0,
            "firstBlock": None,
            "entries": 0,
        },
        {
            "name": "loop-breaker-anchor-mismatch-retry",
            "calls": 60,
            "blocked": 46,
            "firstBlock": 14,
            "entries": 46,
        },
        {
            "name": "loop-breaker-edit-schema-retry",
            "calls": 5,
            "blocked": 0,
            "firstBlock": None,
            "entries": 0,
        },
        {
            "name": "clean-accepted-excerpt",
            "calls": 6,
            "blocked": 0,
            "firstBlock": None,
            "entries": 0,
        },
        {
            "name": "cycle-4-runaway-excerpt",
            "calls": 6,
            "blocked": 1,
            "firstBlock": 6,
            "entries": 1,
        },
    ]


def test_explicit_healthy_fixture_is_accepted() -> None:
    fixture = FIXTURES / "loop-breaker-healthy.json"
    completed = _run_node(str(REPLAY), str(fixture))

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "name": "clean-accepted-excerpt",
        "calls": 6,
        "blocked": 0,
        "firstBlock": None,
        "entries": 0,
    }


def test_malformed_fixture_is_not_reported_as_evidence(tmp_path: Path) -> None:
    fixture = tmp_path / "malformed.json"
    fixture.write_text('{"name":"broken","calls":"not-an-array","expected":{}}')
    completed = _run_node(str(REPLAY), str(fixture))

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "calls must be an array" in completed.stderr


def test_mismatched_fixture_is_not_reported_as_success(tmp_path: Path) -> None:
    fixture = tmp_path / "mismatch.json"
    payload = json.loads((FIXTURES / "loop-breaker-runaway.json").read_text())
    payload["expected"]["blocked"] = 0
    fixture.write_text(json.dumps(payload))
    completed = _run_node(str(REPLAY), str(fixture))

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "blocked 1 != 0" in completed.stderr


def test_replay_usage_error_is_distinct_from_evidence_failure() -> None:
    completed = _run_node(str(REPLAY), "--unknown")

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.startswith("usage:")


def test_pi_installs_package_only_in_temporary_settings(tmp_path: Path) -> None:
    if pi := shutil.which("pi"):
        agent_dir = tmp_path / "agent"
        environment = os.environ.copy()
        environment.update({"PI_CODING_AGENT_DIR": str(agent_dir), "PI_OFFLINE": "1"})
        completed = subprocess.run(
            [pi, "install", str(PACKAGE), "--local", "--approve"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        pytest.skip("Pi is required for the package-install integration test")

    assert completed.returncode == 0, completed.stderr
    settings_path = tmp_path / ".pi" / "settings.json"
    settings = json.loads(settings_path.read_text())
    assert len(settings["packages"]) == 1
    assert (tmp_path / settings["packages"][0]).resolve() == PACKAGE.resolve()

    manifest = json.loads((PACKAGE / "package.json").read_text())
    assert manifest["pi"]["extensions"] == ["./engine.ts", "./orchestrator.ts"]
