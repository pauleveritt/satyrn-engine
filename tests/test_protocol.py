"""Default-tier tests for the JSON protocol surface.

No process is involved: the seam ``run_protocol`` is fed BytesIO streams
directly. Every refusal has a sibling success (binding rule 4).
"""

import io
import json
from hashlib import sha256
from pathlib import Path

import pytest

from satyrn_engine.exits import ExitCode
from satyrn_engine.mutation import MutationCode, MutationReceipt, MutationResult
from satyrn_engine.protocol import (
    _MUTATION_TO_EXIT,
    PROTOCOL_VERSION,
    CheckRequest,
    ProtocolError,
    ReplaceRequest,
    parse_request,
    render_replace_response,
    render_response,
    run_protocol,
)

FIXTURES = Path(__file__).parent / "fixtures" / "protocol"
CONTRACTS = Path(__file__).parent / "fixtures" / "contracts"


def _run(text: str | bytes) -> tuple[bytes, int]:
    stdin = io.BytesIO(text if isinstance(text, bytes) else text.encode("utf-8"))
    stdout = io.BytesIO()
    code = run_protocol(stdin, stdout)
    return stdout.getvalue(), code


def _replace_request(
    repo: Path,
    contract: Path,
    *,
    path: str = "app.py",
    expected_sha256: str | None,
    old_text: str = "value = 1",
    new_text: str = "value = 2",
) -> dict[str, object]:
    return {
        "version": 1,
        "operation": "replace",
        "repo": str(repo),
        "contract": str(contract),
        "path": path,
        "expected_sha256": expected_sha256,
        "old_text": old_text,
        "new_text": new_text,
    }


def test_accepts_valid_request() -> None:
    request = {
        "version": 1,
        "operation": "check",
        "repo": str(Path(__file__).parents[1]),
        "contract": str(CONTRACTS / "valid.yaml"),
    }
    out, code = _run(json.dumps(request))
    assert code == int(ExitCode.OK)
    assert json.loads(out) == {
        "version": 1,
        "ok": True,
        "code": "OK",
        "message": "",
    }
    assert parse_request(json.dumps(request)) == CheckRequest(
        operation="check",
        repo=Path(__file__).parents[1],
        contract=CONTRACTS / "valid.yaml",
    )


def test_accepts_replace_request_and_returns_next_revision(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    before = b"value = 1\n"
    target.write_bytes(before)
    request = _replace_request(
        tmp_path,
        CONTRACTS / "writable.yaml",
        expected_sha256=sha256(before).hexdigest(),
    )
    request["path"] = "tests/fixtures/app.py"
    nested = tmp_path / "tests" / "fixtures" / "app.py"
    nested.parent.mkdir(parents=True)
    target.replace(nested)

    out, code = _run(json.dumps(request))

    assert code == int(ExitCode.OK)
    assert json.loads(out) == {
        "version": 1,
        "ok": True,
        "code": "OK",
        "message": "",
        "result": {
            "path": "tests/fixtures/app.py",
            "sha256": sha256(b"value = 2\n").hexdigest(),
        },
    }
    assert nested.read_bytes() == b"value = 2\n"


def test_parse_replace_request_has_closed_shape(tmp_path: Path) -> None:
    contract = CONTRACTS / "writable.yaml"
    payload = _replace_request(tmp_path, contract, expected_sha256="0" * 64, new_text="")

    assert parse_request(json.dumps(payload)) == ReplaceRequest(
        operation="replace",
        repo=tmp_path,
        contract=contract,
        path="app.py",
        expected_sha256="0" * 64,
        old_text="value = 1",
        new_text="",
    )


def test_parse_replace_request_preserves_explicit_unavailable_revision(tmp_path: Path) -> None:
    contract = CONTRACTS / "writable.yaml"
    payload = _replace_request(tmp_path, contract, expected_sha256=None)

    assert parse_request(json.dumps(payload)) == ReplaceRequest(
        operation="replace",
        repo=tmp_path,
        contract=contract,
        path="app.py",
        expected_sha256=None,
        old_text="value = 1",
        new_text="value = 2",
    )


def test_refuses_unreadable_contract() -> None:
    request = {
        "version": 1,
        "operation": "check",
        "repo": str(Path(__file__).parents[1]),
        "contract": str(Path(__file__).parents[1] / "no-such.yaml"),
    }
    out, code = _run(json.dumps(request))
    assert code == int(ExitCode.CONTRACT_UNREADABLE)
    assert json.loads(out)["code"] == "CONTRACT_UNREADABLE"


def test_refuses_unavailable_repo() -> None:
    request = {
        "version": 1,
        "operation": "check",
        "repo": "/nonexistent",
        "contract": str(CONTRACTS / "valid.yaml"),
    }
    out, code = _run(json.dumps(request))
    assert code == int(ExitCode.REPO_UNAVAILABLE)
    assert json.loads(out)["code"] == "REPO_UNAVAILABLE"


def test_refuses_not_json() -> None:
    out, code = _run("{not json")
    assert code == int(ExitCode.INVALID_REQUEST)
    body = json.loads(out)
    assert body["ok"] is False
    assert body["code"] == "INVALID_REQUEST"
    assert "not valid JSON" in body["message"]


def test_refuses_invalid_utf8() -> None:
    out, code = _run(b"\xff")
    assert code == int(ExitCode.INVALID_REQUEST)
    assert json.loads(out)["code"] == "INVALID_REQUEST"


def test_refuses_unsupported_operation() -> None:
    out, code = _run('{"version":1,"operation":"deliver","repo":".","contract":"x"}')
    assert code == int(ExitCode.INVALID_REQUEST)
    assert json.loads(out)["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repo", "."),
        ("contract", "contract.yaml"),
        ("path", "../app.py"),
        ("expected_sha256", "not-a-hash"),
        ("old_text", ""),
        ("new_text", 1),
    ],
)
def test_refuses_malformed_replace_request(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    payload = _replace_request(
        tmp_path,
        CONTRACTS / "writable.yaml",
        expected_sha256="0" * 64,
    )
    payload[field] = value

    out, code = _run(json.dumps(payload))

    assert code == int(ExitCode.INVALID_REQUEST)
    assert json.loads(out)["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("version", [2, True])
def test_refuses_unsupported_version(version: object) -> None:
    out, code = _run(json.dumps({"version": version, "operation": "check", "repo": ".", "contract": "x"}))
    assert code == int(ExitCode.INVALID_REQUEST)
    assert "version" in json.loads(out)["message"]


def test_refuses_missing_field() -> None:
    out, code = _run('{"version":1,"operation":"check","repo":"."}')
    assert code == int(ExitCode.INVALID_REQUEST)
    assert "contract" in json.loads(out)["message"]


def test_refuses_missing_replace_revision_field(tmp_path: Path) -> None:
    payload = _replace_request(tmp_path, CONTRACTS / "writable.yaml", expected_sha256=None)
    del payload["expected_sha256"]

    out, code = _run(json.dumps(payload))

    assert code == int(ExitCode.INVALID_REQUEST)
    assert "expected_sha256" in json.loads(out)["message"]


@pytest.mark.parametrize("field", ["old_text", "new_text"])
def test_refuses_non_utf8_scalar_replacement_text(tmp_path: Path, field: str) -> None:
    payload = _replace_request(tmp_path, CONTRACTS / "writable.yaml", expected_sha256="0" * 64)
    payload[field] = "\ud800"

    out, code = _run(json.dumps(payload))

    assert code == int(ExitCode.INVALID_REQUEST)
    assert json.loads(out)["code"] == "INVALID_REQUEST"


def test_parse_request_is_strict_about_shape() -> None:
    with pytest.raises(ProtocolError):
        parse_request("[1, 2]")
    with pytest.raises(ProtocolError):
        parse_request('{"version":1,"operation":"check","repo":".","contract":""}')


def test_render_response_round_trips() -> None:
    text = render_response(ExitCode.REPO_UNAVAILABLE, "repo is not a directory: /nonexistent")
    assert json.loads(text) == {
        "version": PROTOCOL_VERSION,
        "ok": False,
        "code": "REPO_UNAVAILABLE",
        "message": "repo is not a directory: /nonexistent",
    }


def test_render_replace_response_round_trips_success_and_refusal() -> None:
    success = MutationReceipt(
        MutationCode.OK,
        result=MutationResult(path="app.py", sha256="1" * 64),
    )
    refusal = MutationReceipt(MutationCode.ANCHOR_MISSING, "old_text was not found")

    assert json.loads(render_replace_response(success)) == {
        "version": 1,
        "ok": True,
        "code": "OK",
        "message": "",
        "result": {"path": "app.py", "sha256": "1" * 64},
    }
    assert json.loads(render_replace_response(refusal)) == {
        "version": 1,
        "ok": False,
        "code": "ANCHOR_MISSING",
        "message": "old_text was not found",
        "result": None,
    }


def test_replace_contract_refusal_keeps_replace_response_shape(tmp_path: Path) -> None:
    payload = _replace_request(
        tmp_path,
        tmp_path / "missing.yaml",
        expected_sha256=None,
    )

    out, code = _run(json.dumps(payload))

    assert code == int(ExitCode.CONTRACT_UNREADABLE)
    assert json.loads(out)["result"] is None


def test_unavailable_revision_is_typed_only_after_contract_path_and_readability(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "tests" / "fixtures" / "app.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("value = 1\n", encoding="utf-8")
    payload = _replace_request(
        tmp_path,
        CONTRACTS / "writable.yaml",
        path="tests/fixtures/app.py",
        expected_sha256=None,
    )

    unavailable, unavailable_exit = _run(json.dumps(payload))
    payload["path"] = "app.py"
    undeclared, undeclared_exit = _run(json.dumps(payload))
    nested.unlink()
    payload["path"] = "tests/fixtures/app.py"
    missing, missing_exit = _run(json.dumps(payload))

    assert unavailable_exit == int(ExitCode.MUTATION_REFUSED)
    assert json.loads(unavailable)["code"] == "REVISION_UNAVAILABLE"
    assert undeclared_exit == int(ExitCode.MUTATION_REFUSED)
    assert json.loads(undeclared)["code"] == "PATH_UNDECLARED"
    assert missing_exit == int(ExitCode.MUTATION_REFUSED)
    assert json.loads(missing)["code"] == "MUTATION_FAILED"


def test_mutation_exit_mapping_is_exhaustive() -> None:
    assert set(_MUTATION_TO_EXIT) == set(MutationCode)
