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


def _run_node(
    *arguments: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_node(), "--experimental-strip-types", *arguments],
        cwd=ROOT,
        env=environment,
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
    assert "pass 16" in completed.stdout
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


def test_fixture_without_required_first_block_is_rejected(tmp_path: Path) -> None:
    fixture = tmp_path / "missing-first-block.json"
    fixture.write_text(
        '{"name":"broken","calls":[],"expected":{"blocked":0,"entries":0}}'
    )
    completed = _run_node(str(REPLAY), str(fixture))

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "expected.firstBlock is required" in completed.stderr


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


def test_pi_installs_and_dispatches_package_extension_in_temporary_settings(
    tmp_path: Path,
) -> None:
    pi = shutil.which("pi")
    if pi is None:
        pytest.skip("Pi is required for the package-install integration test")
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

    assert completed.returncode == 0, completed.stderr
    settings_path = tmp_path / ".pi" / "settings.json"
    settings = json.loads(settings_path.read_text())
    assert len(settings["packages"]) == 1
    installed_package = (tmp_path / settings["packages"][0]).resolve()
    assert installed_package == PACKAGE.resolve()

    manifest = json.loads((installed_package / "package.json").read_text())
    assert manifest["pi"]["extensions"] == [
        "./engine.ts",
        "./orchestrator.ts",
        "./mutator.ts",
    ]

    extension_environment = environment.copy()
    extension_environment["SATYRN_EXTENSION_PATH"] = str(
        installed_package / manifest["pi"]["extensions"][0]
    )
    dispatched = _run_node(
        "--input-type=module",
        "--eval",
        """
import { pathToFileURL } from "node:url";

const extensionPath = process.env.SATYRN_EXTENSION_PATH;
if (extensionPath === undefined) throw new Error("missing extension path");
const { default: registerExtension } = await import(pathToFileURL(extensionPath));
let handler;
const entries = [];
registerExtension({
  on(event, candidate) {
    if (event === "tool_call") handler = candidate;
  },
  appendEntry(kind, data) {
    entries.push({ kind, data });
  },
});
if (typeof handler !== "function") throw new Error("tool_call handler not registered");
let decision;
for (let index = 0; index < 6; index += 1) {
  decision = await handler({ toolName: "bash", input: { command: "same" } });
}
process.stdout.write(JSON.stringify({ decision, entries }));
""",
        environment=extension_environment,
    )

    assert dispatched.returncode == 0, dispatched.stderr
    assert json.loads(dispatched.stdout) == {
        "decision": {
            "block": True,
            "reason": (
                "This exact bash call already appeared 5 times in the last 20 admitted "
                "tool calls. Running it again will not change the result. Use what you "
                "already know and take a different concrete action."
            ),
        },
        "entries": [
            {
                "kind": "loop_broken",
                "data": {"tool": "bash", "repeats": 5, "blockedSoFar": 1},
            }
        ],
    }
