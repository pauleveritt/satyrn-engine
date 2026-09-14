"""Integration tier: the real console script over the JSON protocol.

Marked ``integration`` and excluded from the default run and from CI.
These are the tier's first tests: they start the engine as a subprocess,
the one process this phase earns. The tripwire in ``tests/conftest.py``
yields to this marker.
"""

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "protocol"
CONTRACTS = ROOT / "tests" / "fixtures" / "contracts"

pytestmark = pytest.mark.integration


def run_protocol_process(request_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--project", str(ROOT), "satyrn-engine", "protocol"],
        input=request_text,
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )


def request_with(repo: str, contract: str) -> str:
    return json.dumps({"version": 1, "operation": "check", "repo": repo, "contract": contract})


def test_protocol_accepts_valid_contract() -> None:
    proc = run_protocol_process(request_with(".", str(CONTRACTS / "valid.yaml")))
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"version": 1, "ok": True, "code": "OK", "message": ""}


def test_protocol_refuses_unreadable_contract() -> None:
    proc = run_protocol_process(request_with(".", str(ROOT / "no-such.yaml")))
    assert proc.returncode == 3
    body = json.loads(proc.stdout)
    assert body["ok"] is False
    assert body["code"] == "CONTRACT_UNREADABLE"


def test_protocol_refuses_unavailable_repo() -> None:
    proc = run_protocol_process(request_with("/nonexistent", str(CONTRACTS / "valid.yaml")))
    assert proc.returncode == 6
    body = json.loads(proc.stdout)
    assert body["ok"] is False
    assert body["code"] == "REPO_UNAVAILABLE"


def test_protocol_refuses_malformed_request() -> None:
    proc = run_protocol_process("{not json")
    assert proc.returncode == 7
    body = json.loads(proc.stdout)
    assert body["ok"] is False
    assert body["code"] == "INVALID_REQUEST"


def test_compatibility_fixture_round_trip() -> None:
    """The committed request/response pair matches the real console script."""
    proc = run_protocol_process((FIXTURES / "request-check-valid.json").read_text())
    assert proc.returncode == 0
    assert proc.stdout.rstrip("\n") == (FIXTURES / "response-check-ok.json").read_text().rstrip("\n")


def test_replace_operation_uses_stable_success_and_refusal_exits(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    before = b"value = 1\n"
    target.write_bytes(before)
    contract = tmp_path / "contract.yaml"
    contract.write_text(
        "id: protocol-replace\ntask: replace\nwritable_paths:\n  - app.py\n",
        encoding="utf-8",
    )

    request = {
        "version": 1,
        "operation": "replace",
        "repo": str(tmp_path),
        "contract": str(contract),
        "path": "app.py",
        "expected_sha256": sha256(before).hexdigest(),
        "old_text": "value = 1",
        "new_text": "value = 2",
    }
    accepted = run_protocol_process(json.dumps(request))
    # E9(b): the file now reads "value = 2\n" after `accepted` above, so
    # re-sending the old `new_text` unchanged would report
    # ANCHOR_ALREADY_APPLIED, not ANCHOR_MISSING (see
    # test_replace_operation_reports_anchor_already_applied below, which
    # pins that behaviour deliberately). This case wants a genuine miss, so
    # `new_text` also has to name something the file does not contain.
    request["old_text"] = "not present"
    request["new_text"] = "not present either"
    request["expected_sha256"] = sha256(target.read_bytes()).hexdigest()
    refused = run_protocol_process(json.dumps(request))

    assert accepted.returncode == 0
    assert json.loads(accepted.stdout)["code"] == "OK"
    assert refused.returncode == 9
    assert json.loads(refused.stdout)["code"] == "ANCHOR_MISSING"


def test_replace_operation_reports_anchor_already_applied(tmp_path: Path) -> None:
    """E9(b) through the real console script: re-sending an edit whose
    `new_text` already landed is a named, actionable refusal, not a bare
    ANCHOR_MISSING."""
    target = tmp_path / "app.py"
    before = b"value = 1\n"
    target.write_bytes(before)
    contract = tmp_path / "contract.yaml"
    contract.write_text(
        "id: protocol-replace-already-applied\ntask: replace\nwritable_paths:\n  - app.py\n",
        encoding="utf-8",
    )
    request = {
        "version": 1,
        "operation": "replace",
        "repo": str(tmp_path),
        "contract": str(contract),
        "path": "app.py",
        "expected_sha256": sha256(before).hexdigest(),
        "old_text": "value = 1",
        "new_text": "value = 2",
    }
    accepted = run_protocol_process(json.dumps(request))
    request["expected_sha256"] = sha256(target.read_bytes()).hexdigest()
    repeated = run_protocol_process(json.dumps(request))

    assert accepted.returncode == 0
    assert repeated.returncode == 9
    body = json.loads(repeated.stdout)
    assert body["code"] == "ANCHOR_ALREADY_APPLIED"
    assert "line 1" in body["message"]


def test_replace_operation_refuses_identical_old_and_new_text(tmp_path: Path) -> None:
    """E9(c) through the real console script: a no-op replacement is
    refused before the file is touched."""
    target = tmp_path / "app.py"
    before = b"value = 1\n"
    target.write_bytes(before)
    contract = tmp_path / "contract.yaml"
    contract.write_text(
        "id: protocol-replace-no-change\ntask: replace\nwritable_paths:\n  - app.py\n",
        encoding="utf-8",
    )
    request = {
        "version": 1,
        "operation": "replace",
        "repo": str(tmp_path),
        "contract": str(contract),
        "path": "app.py",
        "expected_sha256": sha256(before).hexdigest(),
        "old_text": "value = 1",
        "new_text": "value = 1",
    }

    refused = run_protocol_process(json.dumps(request))

    assert refused.returncode == 9
    assert json.loads(refused.stdout)["code"] == "NO_CHANGE_REQUESTED"
    assert target.read_bytes() == before


def test_test_operation_runs_the_declared_command_through_the_real_console_script(
    tmp_path: Path,
) -> None:
    contract = tmp_path / "contract.yaml"
    contract.write_text(
        "id: protocol-test\ntask: run tests\n"
        f"test_command:\n  - {sys.executable}\n  - -c\n  - \"assert 1 == 2, 'boom'\"\n",
        encoding="utf-8",
    )
    request = {
        "version": 1,
        "operation": "test",
        "repo": str(tmp_path),
        "contract": str(contract),
        "command": f"{sys.executable} -c assert 1 == 2, 'boom'",
    }

    proc = run_protocol_process(json.dumps(request))

    assert proc.returncode == 0
    body = json.loads(proc.stdout)
    assert body["ok"] is True
    assert body["code"] == "OK"
    assert body["result"]["exit_code"] != 0
    assert "boom" in body["result"]["output"]


def test_test_operation_without_a_declared_command_is_refused(tmp_path: Path) -> None:
    contract = tmp_path / "contract.yaml"
    contract.write_text("id: protocol-test-none\ntask: run tests\n", encoding="utf-8")
    request = {
        "version": 1,
        "operation": "test",
        "repo": str(tmp_path),
        "contract": str(contract),
        "command": "pytest",
    }

    proc = run_protocol_process(json.dumps(request))

    assert proc.returncode == 11
    body = json.loads(proc.stdout)
    assert body["ok"] is False
    assert body["code"] == "TEST_COMMAND_UNAVAILABLE"
    assert body["result"] is None


def test_test_operation_refuses_a_command_that_does_not_match_the_contract(tmp_path: Path) -> None:
    """Sibling of the matching-command run above."""
    contract = tmp_path / "contract.yaml"
    contract.write_text(
        "id: protocol-test-mismatch\ntask: run tests\n"
        f"test_command:\n  - {sys.executable}\n  - -c\n  - \"print('all good')\"\n",
        encoding="utf-8",
    )
    request = {
        "version": 1,
        "operation": "test",
        "repo": str(tmp_path),
        "contract": str(contract),
        "command": "rm -rf /",
    }

    proc = run_protocol_process(json.dumps(request))

    assert proc.returncode == 12
    body = json.loads(proc.stdout)
    assert body["ok"] is False
    assert body["code"] == "TEST_COMMAND_NOT_ALLOWED"
    assert body["result"] is None
