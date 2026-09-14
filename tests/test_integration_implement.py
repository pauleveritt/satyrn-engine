"""Phase 1 Task 11: a fake model completes ``/implement`` end to end.

Derive -> the real ``attempt`` with a fake ``pi`` on PATH -> deliver ->
receipt. Budgets, carried tests and guard firings all land in the receipt.
Integration tier only; no real model is involved (Ruling: no inference)."""

import json
import sys
from pathlib import Path

import pytest
import yaml
from test_integration_attempt import (  # bare name: tests/ has no __init__.py and pytest puts tests/ on sys.path (N4)
    ROOT,
    _fixture,
    _git,
)

from satyrn_engine import cli
from satyrn_engine.delivery import deliver

pytestmark = pytest.mark.integration


def _implement_fixture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, token_budget: int | None = None
) -> tuple[Path, Path, dict[str, str]]:
    repo, _contract, _target, environment = _fixture(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_value.py").write_text("from app import value\n\n\ndef test_value():\n    assert value() == 2\n")
    (repo / "pyproject.toml").write_text('[project]\nname = "fx"\nversion = "0"\n')
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "-c", "user.name=F", "-c", "user.email=f@x.invalid", "commit", "-qm", "fixture").returncode == 0
    assert cli.main(["derive", "--repo", str(repo), "--", "Make value() return 2 in app.py"]) == 0
    contract_path = Path(capsys.readouterr().err.split("contract ", 1)[1].strip())
    body = yaml.safe_load(contract_path.read_text())
    # Task 3's rule: app.py, then its test tests/test_app.py, which is untracked (so not preserved) and therefore writable (N5)
    assert body["writable_paths"] == ["app.py", "tests/test_app.py"] and body["preserve"] == ["tests/test_value.py"]
    assert body["test_command"] == ["uv", "run", "python", "-m", "pytest", "-q"]
    assert (body["token_budget"], body["turn_budget"]) == (32000, 48)
    body["test_command"] = [sys.executable, "-m", "pytest", "-q"]  # offline: the engine venv's pytest
    if token_budget is not None:
        body["token_budget"] = token_budget
        environment["SATYRN_FAKE_PI_TOKENS"] = "300"
    contract_path.write_text(yaml.safe_dump(body, sort_keys=False))
    environment["SATYRN_FAKE_PI_MODE"] = "implement"
    environment["SATYRN_FAKE_PI_SELF_TEST_OUT"] = str(tmp_path / "self_test.json")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return repo, contract_path, environment


def _attempt_command(contract_path: Path) -> tuple[str, ...]:
    return ("uv", "run", "--project", str(ROOT), "satyrn-engine", "attempt", "--model=fixture/model", "--", str(contract_path))


def test_a_fake_model_completes_implement_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, contract_path, _ = _implement_fixture(tmp_path, capsys, monkeypatch)
    receipt = deliver(repo, contract_path, _attempt_command(contract_path), timeout=120.0)
    payload = receipt.payload()
    assert (payload["code"], payload["validation"]) == ("OK", "passed"), payload
    assert payload["changed_paths"] == ["app.py"]
    assert (payload["turns"], payload["tool_calls"], payload["tokens_in"], payload["tokens_out"]) == (3, 2, 3000, 600)
    budget = payload["budget"]
    assert (budget["state"], budget["turns_used"], budget["turn_limit"]) == ("within", 3, 48)
    assert (budget["token_limit"], budget["tokens_used"], budget["deadline_seconds"]) == (32000, 600, None)
    assert payload["guard_firings"]["command_bounded"] == 1
    assert payload["carried"] == {"preserve": ["tests/test_value.py"], "checks": [], "infrastructure": ["pyproject.toml"], "absent": [], "tampered": []}
    self_test = json.loads((tmp_path / "self_test.json").read_text())
    assert self_test["details"]["ok"] is True and self_test["details"]["result"]["exit_code"] == 0
    assert "1 passed" in self_test["content"][0]["text"]
    assert _git(repo, "status", "--porcelain").stdout == b""


def test_a_fake_model_that_exceeds_the_token_budget_is_budget_exhausted_with_the_candidate_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, contract_path, _ = _implement_fixture(tmp_path, capsys, monkeypatch, token_budget=500)
    receipt = deliver(repo, contract_path, _attempt_command(contract_path), timeout=120.0)
    payload = receipt.payload()
    assert payload["code"] == "BUDGET_EXHAUSTED" and payload["outcome"] == "candidate-created"
    assert payload["budget"]["state"] == "token_exhausted" and payload["budget"]["token_limit"] == 500
    assert payload["budget"]["tokens_used"] > 500
    assert payload["candidate_commit"] is not None
    assert payload["message"].startswith("candidate created; whole-attempt token budget exhausted after")
