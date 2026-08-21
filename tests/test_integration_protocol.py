"""Integration tier: the real console script over the JSON protocol.

Marked ``integration`` and excluded from the default run and from CI.
These are the tier's first tests: they start the engine as a subprocess,
the one process this phase earns. The tripwire in ``tests/conftest.py``
yields to this marker.
"""

import json
import subprocess
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
    request["old_text"] = "not present"
    request["expected_sha256"] = sha256(target.read_bytes()).hexdigest()
    refused = run_protocol_process(json.dumps(request))

    assert accepted.returncode == 0
    assert json.loads(accepted.stdout)["code"] == "OK"
    assert refused.returncode == 9
    assert json.loads(refused.stdout)["code"] == "ANCHOR_MISSING"
