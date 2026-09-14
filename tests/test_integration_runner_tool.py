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


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _fixture(
    tmp_path: Path, *, test_command: list[str] | None, preserve: list[str] | None = None
) -> Fixture:
    repo = tmp_path / "repo"
    repo.mkdir()
    contract = tmp_path / "contract.yaml"
    lines = ["id: e7-integration", "task: run the command"]
    if test_command is not None:
        rendered = "\n".join(f"  - {json.dumps(token)}" for token in test_command)
        lines.append(f"test_command:\n{rendered}")
    if preserve:
        rendered = "\n".join(f"  - {json.dumps(token)}" for token in preserve)
        lines.append(f"preserve:\n{rendered}")
    contract.write_text("\n".join(lines) + "\n", encoding="utf-8")
    context = tmp_path / "context.json"
    context.write_text(
        json.dumps({"version": 1, "repo": str(repo), "contract": str(contract), "revisions": {}}),
        encoding="utf-8",
    )
    return Fixture(repo, contract, context)


def _run(fixture: Fixture) -> tuple[subprocess.CompletedProcess[str], ExerciseBody]:
    # `self_test`'s schema is open and the model's argument is ignored
    # (Ruling 1), so `exercise_runner.mjs` takes only the context file --
    # there is no longer a command to pass through.
    completed = subprocess.run(
        [_node(), "--experimental-strip-types", str(EXERCISE), str(fixture.context)],
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

    completed, body = _run(fixture)

    assert completed.returncode == 0, completed.stderr
    assert body["details"]["ok"] is True
    assert body["details"]["code"] == "OK"
    assert body["details"]["result"]["exit_code"] == 0
    assert "all good" in body["details"]["result"]["output"]


def test_shipped_tool_reports_a_failing_suite_as_a_result_not_an_error(tmp_path: Path) -> None:
    test_command = [sys.executable, "-c", "assert 1 == 2, 'boom'"]
    fixture = _fixture(tmp_path, test_command=test_command)

    completed, body = _run(fixture)

    assert completed.returncode == 0, completed.stderr
    assert body["details"]["ok"] is True
    assert body["details"]["code"] == "OK"
    assert body["details"]["result"]["exit_code"] != 0
    assert "boom" in body["details"]["result"]["output"]


def test_shipped_tool_reports_a_missing_test_command_as_a_named_refusal(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, test_command=None)

    completed, body = _run(fixture)

    assert completed.returncode == 0, completed.stderr
    assert body["details"]["ok"] is False
    assert body["details"]["code"] == "TEST_COMMAND_UNAVAILABLE"
    assert body["details"]["result"] is None


def test_shipped_tool_restores_a_tampered_preserve_path_before_running(tmp_path: Path) -> None:
    """The carried set (steering C3/N2), driven through the real TS tool
    and the real engine child, in a real git fixture: a `preserve` path
    edited in the worktree after the base commit is restored before the
    self-test's second (preserve) invocation ever sees it. No `base_commit`
    travels through this JSON mutation context yet (Task 5), so the
    restore falls back to `HEAD` -- exactly the commit made here."""
    code = (
        "import sys, pathlib\n"
        "args = sys.argv[1:]\n"
        "print(pathlib.Path(args[0]).read_text() if args else 'NO_PRESERVE_ARG')\n"
    )
    test_command = [sys.executable, "-c", code]
    fixture = _fixture(tmp_path, test_command=test_command, preserve=["preserve.txt"])
    preserve = fixture.repo / "preserve.txt"
    preserve.write_text("original\n", encoding="utf-8")
    _git(fixture.repo, "init", "-q")
    _git(fixture.repo, "config", "user.email", "test@example.com")
    _git(fixture.repo, "config", "user.name", "Test")
    _git(fixture.repo, "add", "-A")
    _git(fixture.repo, "commit", "-q", "-m", "base")
    preserve.write_text("tampered\n", encoding="utf-8")  # uncommitted edit in the worktree

    completed, body = _run(fixture)

    assert completed.returncode == 0, completed.stderr
    assert body["details"]["ok"] is True
    assert "original" in body["details"]["result"]["output"]
    assert "tampered" not in body["details"]["result"]["output"]
    assert preserve.read_text(encoding="utf-8") == "original\n"


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
