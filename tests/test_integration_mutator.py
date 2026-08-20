"""Real TypeScript → Python evidence for E4's bounded replacement."""

import json
import shutil
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TypedDict, cast

import pytest

ROOT = Path(__file__).parents[1]
EXERCISE = ROOT / "tools" / "exercise_mutator.mjs"

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class Fixture:
    repo: Path
    contract: Path
    target: Path
    context: Path
    tool_input: Path


class ReplacementDetails(TypedDict):
    satyrn: bool
    ok: bool
    code: str
    result: dict[str, str] | None


class ExerciseBody(TypedDict):
    details: ReplacementDetails


def _node() -> str:
    if executable := shutil.which("node"):
        return executable
    pytest.skip("Node is required for the mutation integration tier")


def _fixture(
    tmp_path: Path,
    *,
    content: str = "def value():\n    return 1\n",
    path: str = "src/app.py",
    writable: str = "src/*.py",
    revision: str | None = None,
    old_text: str = "return 1",
    new_text: str = "return 2",
) -> Fixture:
    repo = tmp_path / "repo"
    target = repo / path
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    contract = tmp_path / "contract.yaml"
    contract.write_text(
        f"id: e4-integration\ntask: replace one anchor\nwritable_paths:\n  - {writable}\n",
        encoding="utf-8",
    )
    context = tmp_path / "context.json"
    context.write_text(
        json.dumps(
            {
                "version": 1,
                "repo": str(repo),
                "contract": str(contract),
                "revisions": {
                    path: revision or sha256(target.read_bytes()).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    tool_input = tmp_path / "input.json"
    tool_input.write_text(
        json.dumps(
            {
                "path": path,
                "edits": [{"oldText": old_text, "newText": new_text}],
            }
        ),
        encoding="utf-8",
    )
    return Fixture(repo, contract, target, context, tool_input)


def _run(fixture: Fixture) -> tuple[subprocess.CompletedProcess[str], ExerciseBody]:
    completed = subprocess.run(
        [
            _node(),
            "--experimental-strip-types",
            str(EXERCISE),
            str(fixture.context),
            str(fixture.tool_input),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    body = cast(ExerciseBody, json.loads(completed.stdout))
    return completed, body


def test_shipped_adapter_replaces_one_anchor_through_real_engine(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    completed, body = _run(fixture)

    assert completed.returncode == 0, completed.stderr
    assert body["details"] == {
        "satyrn": True,
        "ok": True,
        "code": "OK",
        "result": {
            "path": "src/app.py",
            "sha256": sha256(b"def value():\n    return 2\n").hexdigest(),
        },
    }
    assert fixture.target.read_bytes() == b"def value():\n    return 2\n"


@pytest.mark.parametrize(
    ("expected_code", "fixture_options"),
    [
        ("REVISION_STALE", {"revision": "0" * 64}),
        ("PATH_UNDECLARED", {"path": "app.py"}),
        ("ANCHOR_MISSING", {"old_text": "return 3"}),
        ("ANCHOR_AMBIGUOUS", {"content": "return 1\nreturn 1\n"}),
    ],
)
def test_shipped_adapter_returns_named_refusal_without_mutation(
    tmp_path: Path,
    expected_code: str,
    fixture_options: dict[str, str],
) -> None:
    fixture = _fixture(tmp_path, **fixture_options)
    before = fixture.target.read_bytes()

    completed, body = _run(fixture)

    assert completed.returncode == 0, completed.stderr
    assert body["details"]["ok"] is False
    assert body["details"]["code"] == expected_code
    assert body["details"]["result"] is None
    assert fixture.target.read_bytes() == before


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
    assert "usage: node --experimental-strip-types tools/exercise_mutator.mjs" in completed.stderr
