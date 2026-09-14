"""Real TypeScript -> Python evidence for E7's `bash` tool.

Mirrors `tests/test_integration_mutator.py`: `tools/exercise_runner.mjs`
spawns the real engine as a subprocess, over the same protocol the shipped
`bash` tool uses, and this drives it with real, non-trivial repos and
commands.
"""

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, cast

import pytest

ROOT = Path(__file__).parents[1]
EXERCISE = ROOT / "tools" / "exercise_runner.mjs"

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class Fixture:
    repo: Path
    contract: Path
    context: Path


class RunnerResultPayload(TypedDict):
    exit_code: int
    output: str
    truncated: bool
    timed_out: bool


class RunnerDetails(TypedDict):
    satyrn: bool
    ok: bool
    code: str
    result: RunnerResultPayload | None


class ExerciseBody(TypedDict):
    details: RunnerDetails


def _node() -> str:
    if executable := shutil.which("node"):
        return executable
    pytest.skip("Node is required for the runner integration tier")


def _fixture(tmp_path: Path, *, test_command: list[str] | None) -> Fixture:
    repo = tmp_path / "repo"
    repo.mkdir()
    contract = tmp_path / "contract.yaml"
    lines = ["id: e7-integration", "task: run the command"]
    if test_command is not None:
        rendered = "\n".join(f"  - {json.dumps(token)}" for token in test_command)
        lines.append(f"test_command:\n{rendered}")
    contract.write_text("\n".join(lines) + "\n", encoding="utf-8")
    context = tmp_path / "context.json"
    context.write_text(
        json.dumps({"version": 1, "repo": str(repo), "contract": str(contract), "revisions": {}}),
        encoding="utf-8",
    )
    return Fixture(repo, contract, context)


def _run(fixture: Fixture, command: str) -> tuple[subprocess.CompletedProcess[str], ExerciseBody]:
    completed = subprocess.run(
        [_node(), "--experimental-strip-types", str(EXERCISE), str(fixture.context), command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    body = cast(ExerciseBody, json.loads(completed.stdout))
    return completed, body


def test_shipped_tool_reports_a_passing_suite(tmp_path: Path) -> None:
    test_command = [sys.executable, "-c", "print('all good')"]
    fixture = _fixture(tmp_path, test_command=test_command)

    completed, body = _run(fixture, " ".join(test_command))

    assert completed.returncode == 0, completed.stderr
    assert body["details"]["ok"] is True
    assert body["details"]["code"] == "OK"
    assert body["details"]["result"]["exit_code"] == 0
    assert "all good" in body["details"]["result"]["output"]


def test_shipped_tool_reports_a_failing_suite_as_a_result_not_an_error(tmp_path: Path) -> None:
    test_command = [sys.executable, "-c", "assert 1 == 2, 'boom'"]
    fixture = _fixture(tmp_path, test_command=test_command)

    completed, body = _run(fixture, " ".join(test_command))

    assert completed.returncode == 0, completed.stderr
    assert body["details"]["ok"] is True
    assert body["details"]["code"] == "OK"
    assert body["details"]["result"]["exit_code"] != 0
    assert "boom" in body["details"]["result"]["output"]


def test_shipped_tool_reports_a_missing_test_command_as_a_named_refusal(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, test_command=None)

    completed, body = _run(fixture, "pytest")

    assert completed.returncode == 0, completed.stderr
    assert body["details"]["ok"] is False
    assert body["details"]["code"] == "TEST_COMMAND_UNAVAILABLE"
    assert body["details"]["result"] is None


def test_shipped_tool_refuses_a_command_that_does_not_match_the_contract(tmp_path: Path) -> None:
    test_command = [sys.executable, "-c", "print('all good')"]
    fixture = _fixture(tmp_path, test_command=test_command)

    completed, body = _run(fixture, "rm -rf /")

    assert completed.returncode == 0, completed.stderr
    assert body["details"]["ok"] is False
    assert body["details"]["code"] == "TEST_COMMAND_NOT_ALLOWED"
    assert body["details"]["result"] is None


def test_exercise_harness_has_distinct_usage_failure() -> None:
    completed = subprocess.run(
        [_node(), "--experimental-strip-types", str(EXERCISE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "usage: node --experimental-strip-types tools/exercise_runner.mjs" in completed.stderr
