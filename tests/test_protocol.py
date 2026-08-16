"""Default-tier tests for the JSON protocol surface.

No process is involved: the seam ``run_protocol`` is fed BytesIO streams
directly. Every refusal has a sibling success (binding rule 4).
"""

import io
import json
from pathlib import Path

import pytest

from satyrn_engine.exits import ExitCode
from satyrn_engine.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    parse_request,
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


def test_refuses_unsupported_operation() -> None:
    out, code = _run('{"version":1,"operation":"deliver","repo":".","contract":"x"}')
    assert code == int(ExitCode.INVALID_REQUEST)
    assert json.loads(out)["code"] == "INVALID_REQUEST"


def test_refuses_unsupported_version() -> None:
    out, code = _run('{"version":2,"operation":"check","repo":".","contract":"x"}')
    assert code == int(ExitCode.INVALID_REQUEST)
    assert "version" in json.loads(out)["message"]


def test_refuses_missing_field() -> None:
    out, code = _run('{"version":1,"operation":"check","repo":"."}')
    assert code == int(ExitCode.INVALID_REQUEST)
    assert "contract" in json.loads(out)["message"]


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
