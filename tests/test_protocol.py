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
    _RUNNER_TO_EXIT,
    OPERATIONS,
    PROTOCOL_VERSION,
    CheckRequest,
    ProtocolError,
    ReplaceRequest,
    RunTestsRequest,
    parse_request,
    render_replace_response,
    render_response,
    render_test_response,
    run_protocol,
)
from satyrn_engine.runner import RunnerCode, RunnerReceipt, RunnerResult

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
            "region": "1: value = 2",
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
        replacements=(("value = 1", ""),),
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
        replacements=(("value = 1", "value = 2"),),
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
        result=MutationResult(path="app.py", sha256="1" * 64, region="1: value = 2"),
    )
    refusal = MutationReceipt(MutationCode.ANCHOR_MISSING, "old_text was not found")

    assert json.loads(render_replace_response(success)) == {
        "version": 1,
        "ok": True,
        "code": "OK",
        "message": "",
        "result": {"path": "app.py", "sha256": "1" * 64, "region": "1: value = 2"},
    }
    assert json.loads(render_replace_response(refusal)) == {
        "version": 1,
        "ok": False,
        "code": "ANCHOR_MISSING",
        "message": "old_text was not found",
        "result": None,
    }


def test_render_replace_response_round_trips_new_e9_refusal_codes() -> None:
    """Sibling of the round-trip above, for the two codes E9 adds
    (mutation.py's NO_CHANGE_REQUESTED and ANCHOR_ALREADY_APPLIED): both
    must appear in the protocol response exactly like every other refusal."""
    already_applied = MutationReceipt(
        MutationCode.ANCHOR_ALREADY_APPLIED,
        "new_text is already present in app.py at line 2; old_text was not found",
    )
    no_change = MutationReceipt(
        MutationCode.NO_CHANGE_REQUESTED,
        "old_text and new_text are identical in app.py; nothing to replace",
    )

    assert json.loads(render_replace_response(already_applied)) == {
        "version": 1,
        "ok": False,
        "code": "ANCHOR_ALREADY_APPLIED",
        "message": "new_text is already present in app.py at line 2; old_text was not found",
        "result": None,
    }
    assert json.loads(render_replace_response(no_change)) == {
        "version": 1,
        "ok": False,
        "code": "NO_CHANGE_REQUESTED",
        "message": "old_text and new_text are identical in app.py; nothing to replace",
        "result": None,
    }


def test_full_protocol_reports_no_change_requested_and_leaves_file_unmodified(
    tmp_path: Path,
) -> None:
    """E9(c) through the full `run_protocol` seam, not just `replace_once`
    directly: the file must be unmodified end to end."""
    nested = tmp_path / "tests" / "fixtures" / "app.py"
    nested.parent.mkdir(parents=True)
    before = b"value = 1\n"
    nested.write_bytes(before)
    payload = _replace_request(
        tmp_path,
        CONTRACTS / "writable.yaml",
        path="tests/fixtures/app.py",
        expected_sha256=sha256(before).hexdigest(),
        old_text="value = 1",
        new_text="value = 1",
    )

    out, code = _run(json.dumps(payload))

    assert code == int(ExitCode.MUTATION_REFUSED)
    body = json.loads(out)
    assert body["code"] == "NO_CHANGE_REQUESTED"
    assert body["result"] is None
    assert nested.read_bytes() == before


def test_full_protocol_reports_anchor_already_applied(tmp_path: Path) -> None:
    """E9(b) through the full `run_protocol` seam."""
    nested = tmp_path / "tests" / "fixtures" / "app.py"
    nested.parent.mkdir(parents=True)
    before = b"value = 2\n"
    nested.write_bytes(before)
    payload = _replace_request(
        tmp_path,
        CONTRACTS / "writable.yaml",
        path="tests/fixtures/app.py",
        expected_sha256=sha256(before).hexdigest(),
        old_text="value = 1",
        new_text="value = 2",
    )

    out, code = _run(json.dumps(payload))

    assert code == int(ExitCode.MUTATION_REFUSED)
    body = json.loads(out)
    assert body["code"] == "ANCHOR_ALREADY_APPLIED"
    assert "line 1" in body["message"]
    assert body["result"] is None
    assert nested.read_bytes() == before


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


def test_runner_exit_mapping_is_exhaustive() -> None:
    assert set(_RUNNER_TO_EXIT) == set(RunnerCode)


def test_operations_includes_test() -> None:
    assert OPERATIONS == ("check", "replace", "test")


def test_parses_test_request() -> None:
    request = {
        "version": 1,
        "operation": "test",
        "repo": str(Path(__file__).parents[1]),
        "contract": str(CONTRACTS / "valid.yaml"),
        "command": "pytest",
    }
    assert parse_request(json.dumps(request)) == RunTestsRequest(
        operation="test",
        repo=Path(__file__).parents[1],
        contract=CONTRACTS / "valid.yaml",
        command="pytest",
    )


def test_test_request_accepts_a_null_or_absent_command() -> None:
    """`self_test`'s schema is open and the model's argument is ignored
    (Ruling 1): both an absent and an explicit ``null`` `command` parse to
    `command=None`, not a refusal."""
    base_request = {
        "version": 1,
        "operation": "test",
        "repo": str(Path(__file__).parents[1]),
        "contract": str(CONTRACTS / "valid.yaml"),
    }
    absent = parse_request(json.dumps(base_request))
    explicit_null = parse_request(json.dumps({**base_request, "command": None}))
    assert absent.command is None
    assert explicit_null.command is None


def test_test_request_carries_an_optional_base_commit() -> None:
    base_request = {
        "version": 1,
        "operation": "test",
        "repo": str(Path(__file__).parents[1]),
        "contract": str(CONTRACTS / "valid.yaml"),
    }
    forty_hex = "b" * 40
    absent = parse_request(json.dumps(base_request))
    present = parse_request(json.dumps({**base_request, "base_commit": forty_hex}))
    assert absent.base_commit is None
    assert present.base_commit == forty_hex
    with pytest.raises(ProtocolError, match="base_commit"):
        parse_request(json.dumps({**base_request, "base_commit": "not-40-hex"}))
    with pytest.raises(ProtocolError, match="base_commit"):
        parse_request(json.dumps({**base_request, "base_commit": 1}))


def test_test_operation_without_declared_command_is_a_typed_refusal_not_a_crash(
    tmp_path: Path,
) -> None:
    """No `test_command` never reaches `subprocess.run` (binding rule 3).

    `tests/conftest.py` monkeypatches `subprocess.run` to explode in this
    default tier; this exercises the real `run_tests` function through the
    full `run_protocol` seam and proves the no-command path never calls it.
    """
    contract = tmp_path / "contract.yaml"
    contract.write_text("id: e7-no-command\ntask: test\n", encoding="utf-8")
    request = {
        "version": 1,
        "operation": "test",
        "repo": str(tmp_path),
        "contract": str(contract),
        "command": "pytest",
    }

    out, code = _run(json.dumps(request))

    assert code == int(ExitCode.TEST_COMMAND_UNAVAILABLE)
    body = json.loads(out)
    assert body == {
        "version": 1,
        "ok": False,
        "code": "TEST_COMMAND_UNAVAILABLE",
        "message": body["message"],
        "result": None,
    }
    assert "test_command" in body["message"]


def test_test_operation_refuses_a_command_that_does_not_match_the_contract(
    tmp_path: Path,
) -> None:
    """Sibling of the matching-command success above: the mismatch never
    reaches `subprocess.run` either, so it stays safe under the same
    default-tier tripwire."""
    contract = tmp_path / "contract.yaml"
    contract.write_text(
        "id: e7-mismatch\ntask: test\ntest_command:\n  - pytest\n  - tests/\n",
        encoding="utf-8",
    )
    request = {
        "version": 1,
        "operation": "test",
        "repo": str(tmp_path),
        "contract": str(contract),
        "command": "rm -rf /",
    }

    out, code = _run(json.dumps(request))

    assert code == int(ExitCode.TEST_COMMAND_NOT_ALLOWED)
    body = json.loads(out)
    assert body["ok"] is False
    assert body["code"] == "TEST_COMMAND_NOT_ALLOWED"
    assert body["result"] is None
    assert '"pytest tests/"' in body["message"]


def test_test_operation_refuses_before_running_when_contract_is_unreadable() -> None:
    request = {
        "version": 1,
        "operation": "test",
        "repo": str(Path(__file__).parents[1]),
        "contract": str(Path(__file__).parents[1] / "no-such.yaml"),
        "command": "pytest",
    }
    out, code = _run(json.dumps(request))
    assert code == int(ExitCode.CONTRACT_UNREADABLE)
    body = json.loads(out)
    assert body["code"] == "CONTRACT_UNREADABLE"
    assert body["result"] is None


def test_render_test_response_round_trips_success_and_refusal() -> None:
    success = RunnerReceipt(
        RunnerCode.OK,
        result=RunnerResult(exit_code=1, output="assert 1 == 2", truncated=False, timed_out=False),
    )
    refusal = RunnerReceipt(RunnerCode.TEST_COMMAND_UNAVAILABLE, "contract does not declare a test_command")

    assert json.loads(render_test_response(success)) == {
        "version": 1,
        "ok": True,
        "code": "OK",
        "message": "",
        "result": {
            "exit_code": 1,
            "output": "assert 1 == 2",
            "truncated": False,
            "timed_out": False,
            "compact_bytes": 0,
        },
    }
    assert json.loads(render_test_response(refusal)) == {
        "version": 1,
        "ok": False,
        "code": "TEST_COMMAND_UNAVAILABLE",
        "message": "contract does not declare a test_command",
        "result": None,
    }

    not_allowed = RunnerReceipt(RunnerCode.TEST_COMMAND_NOT_ALLOWED, 'only this exact command is allowed: "pytest"')
    assert json.loads(render_test_response(not_allowed)) == {
        "version": 1,
        "ok": False,
        "code": "TEST_COMMAND_NOT_ALLOWED",
        "message": 'only this exact command is allowed: "pytest"',
        "result": None,
    }


def test_replace_request_accepts_an_edits_array() -> None:
    request = parse_request(json.dumps({
        "version": 1, "operation": "replace", "repo": "/w", "contract": "/w/c.yaml",
        "path": "app.py", "expected_sha256": "a" * 64,
        "edits": [{"old_text": "a", "new_text": "b"}, {"old_text": "c", "new_text": "d"}],
    }))
    assert request.replacements == (("a", "b"), ("c", "d"))


def test_replace_request_still_accepts_one_old_text_new_text_pair() -> None:
    request = parse_request(json.dumps({
        "version": 1, "operation": "replace", "repo": "/w", "contract": "/w/c.yaml",
        "path": "app.py", "expected_sha256": "a" * 64, "old_text": "a", "new_text": "b",
    }))
    assert request.replacements == (("a", "b"),)


def test_replace_request_refuses_both_forms_at_once() -> None:
    with pytest.raises(ProtocolError, match="edits"):
        parse_request(json.dumps({
            "version": 1, "operation": "replace", "repo": "/w", "contract": "/w/c.yaml",
            "path": "app.py", "expected_sha256": "a" * 64, "old_text": "a", "new_text": "b",
            "edits": [{"old_text": "c", "new_text": "d"}],
        }))


def test_test_response_reports_the_compact_size():
    receipt = RunnerReceipt(
        RunnerCode.OK,
        result=RunnerResult(exit_code=1, output="FAILED a::b\n", truncated=False, timed_out=False,
                            compact_bytes=13),
    )
    payload = json.loads(render_test_response(receipt))
    assert payload["result"]["compact_bytes"] == 13
