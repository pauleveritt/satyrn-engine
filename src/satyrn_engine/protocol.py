"""The one-shot JSON protocol surface used by the Pi adapters."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, TypedDict

from .check import check
from .exits import ExitCode
from .mutation import (
    MutationCode,
    MutationReceipt,
    normalize_relative_path,
    replace_once,
)
from .runner import RunnerCode, RunnerReceipt, run_tests

PROTOCOL_VERSION = 1
OPERATIONS = ("check", "replace", "test")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
BASE_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")

_MUTATION_TO_EXIT: dict[MutationCode, ExitCode] = {
    MutationCode.OK: ExitCode.OK,
    MutationCode.PATH_UNDECLARED: ExitCode.MUTATION_REFUSED,
    MutationCode.REVISION_UNAVAILABLE: ExitCode.MUTATION_REFUSED,
    MutationCode.REVISION_STALE: ExitCode.MUTATION_REFUSED,
    MutationCode.NO_CHANGE_REQUESTED: ExitCode.MUTATION_REFUSED,
    MutationCode.ANCHOR_MISSING: ExitCode.MUTATION_REFUSED,
    MutationCode.ANCHOR_ALREADY_APPLIED: ExitCode.MUTATION_REFUSED,
    MutationCode.ANCHOR_AMBIGUOUS: ExitCode.MUTATION_REFUSED,
    MutationCode.MUTATION_FAILED: ExitCode.MUTATION_REFUSED,
}

_RUNNER_TO_EXIT: dict[RunnerCode, ExitCode] = {
    RunnerCode.OK: ExitCode.OK,
    RunnerCode.TEST_COMMAND_UNAVAILABLE: ExitCode.TEST_COMMAND_UNAVAILABLE,
    RunnerCode.TEST_COMMAND_NOT_ALLOWED: ExitCode.TEST_COMMAND_NOT_ALLOWED,
}


class ProtocolError(Exception):
    """A malformed request, refused as INVALID_REQUEST."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class CheckRequest:
    """A contract-check protocol request."""

    operation: Literal["check"]
    repo: Path
    contract: Path


@dataclass(frozen=True, slots=True)
class ReplaceRequest:
    """A bounded-replacement protocol request."""

    operation: Literal["replace"]
    repo: Path
    contract: Path
    path: str
    expected_sha256: str | None
    old_text: str
    new_text: str


@dataclass(frozen=True, slots=True)
class RunTestsRequest:
    """A contract-declared self-test protocol request."""

    operation: Literal["test"]
    repo: Path
    contract: Path
    command: str | None
    base_commit: str | None = None


type ProtocolRequest = CheckRequest | ReplaceRequest | RunTestsRequest


class ResponsePayload(TypedDict):
    """Stable response fields shared by every protocol operation."""

    version: int
    ok: bool
    code: str
    message: str


class MutationResultPayload(TypedDict):
    """JSON result of a successful replacement."""

    path: str
    sha256: str
    region: str


class ReplaceResponsePayload(ResponsePayload):
    """Replacement response, including an operation-specific result."""

    result: MutationResultPayload | None


class RunnerResultPayload(TypedDict):
    """JSON result of a completed (or timed-out) test command run."""

    exit_code: int
    output: str
    truncated: bool
    timed_out: bool


class RunTestsResponsePayload(ResponsePayload):
    """Test-run response, including an operation-specific result."""

    result: RunnerResultPayload | None


def _decode(data: str | bytes) -> str:
    if isinstance(data, bytes):
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError(f"request is not valid UTF-8: {exc}") from exc
    return data


def _required_string(payload: dict[str, object], field: str, *, allow_empty: bool = False) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or (not allow_empty and not value):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise ProtocolError(f"request field {field!r} must be {qualifier}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProtocolError(f"request field {field!r} must contain only UTF-8 scalar values") from exc
    return value


def _required_revision(payload: dict[str, object]) -> str | None:
    if "expected_sha256" not in payload:
        raise ProtocolError("request field 'expected_sha256' is required")
    value = payload["expected_sha256"]
    if value is None:
        return None
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise ProtocolError("request field 'expected_sha256' must be null or 64 lowercase hexadecimal characters")
    return value


def parse_request(data: str | bytes) -> ProtocolRequest:
    """Parse and validate one operation-discriminated request."""
    text = _decode(data)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"request is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError(f"request top level must be a mapping, not {type(payload).__name__}")
    version = payload.get("version")
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported protocol version {version!r}; expected {PROTOCOL_VERSION}"
        )

    operation = payload.get("operation")
    if operation not in OPERATIONS:
        raise ProtocolError(f"unsupported operation {operation!r}; expected one of {OPERATIONS!r}")
    repo = Path(_required_string(payload, "repo"))
    contract = Path(_required_string(payload, "contract"))

    match operation:
        case "check":
            return CheckRequest(operation=operation, repo=repo, contract=contract)
        case "test":
            command = payload.get("command")
            if command is not None:
                command = _required_string(payload, "command")
            base_commit = payload.get("base_commit")
            if base_commit is not None and (
                not isinstance(base_commit, str) or BASE_COMMIT_PATTERN.fullmatch(base_commit) is None
            ):
                raise ProtocolError(
                    "request field 'base_commit' must be null or 40 lowercase hexadecimal characters"
                )
            return RunTestsRequest(
                operation=operation,
                repo=repo,
                contract=contract,
                command=command,
                base_commit=base_commit,
            )
        case "replace":
            if not repo.is_absolute() or not contract.is_absolute():
                raise ProtocolError("replace request fields 'repo' and 'contract' must be absolute paths")
            try:
                path = normalize_relative_path(_required_string(payload, "path"))
            except ValueError as exc:
                raise ProtocolError(f"invalid replacement path: {exc}") from exc
            return ReplaceRequest(
                operation=operation,
                repo=repo,
                contract=contract,
                path=path,
                expected_sha256=_required_revision(payload),
                old_text=_required_string(payload, "old_text"),
                new_text=_required_string(payload, "new_text", allow_empty=True),
            )
        case _:  # pragma: no cover - membership check above closes the union
            raise AssertionError(operation)


def _base_payload(code: ExitCode, message: str) -> ResponsePayload:
    return {
        "version": PROTOCOL_VERSION,
        "ok": code is ExitCode.OK,
        "code": code.name,
        "message": message,
    }


def render_response(code: ExitCode, message: str) -> str:
    """Render one check response as a compact JSON string."""
    return json.dumps(_base_payload(code, message), separators=(",", ":"))


def render_replace_response(receipt: MutationReceipt) -> str:
    """Render one operation-specific replacement response."""
    result: MutationResultPayload | None = None
    if receipt.result is not None:
        result = {
            "path": receipt.result.path,
            "sha256": receipt.result.sha256,
            "region": receipt.result.region,
        }
    payload: ReplaceResponsePayload = {
        "version": PROTOCOL_VERSION,
        "ok": receipt.ok,
        "code": receipt.code.value,
        "message": receipt.message,
        "result": result,
    }
    return json.dumps(payload, separators=(",", ":"))


def _render_replace_check_failure(code: ExitCode, message: str) -> str:
    payload: ReplaceResponsePayload = {
        **_base_payload(code, message),
        "result": None,
    }
    return json.dumps(payload, separators=(",", ":"))


def render_test_response(receipt: RunnerReceipt) -> str:
    """Render one operation-specific test-run response."""
    result: RunnerResultPayload | None = None
    if receipt.result is not None:
        result = {
            "exit_code": receipt.result.exit_code,
            "output": receipt.result.output,
            "truncated": receipt.result.truncated,
            "timed_out": receipt.result.timed_out,
        }
    payload: RunTestsResponsePayload = {
        "version": PROTOCOL_VERSION,
        "ok": receipt.ok,
        "code": receipt.code.value,
        "message": receipt.message,
        "result": result,
    }
    return json.dumps(payload, separators=(",", ":"))


def _render_test_check_failure(code: ExitCode, message: str) -> str:
    payload: RunTestsResponsePayload = {
        **_base_payload(code, message),
        "result": None,
    }
    return json.dumps(payload, separators=(",", ":"))


def handle_protocol(data: str | bytes) -> tuple[str, int]:
    """Turn one request into ``(response_text, exit_code)``."""
    try:
        request = parse_request(data)
    except ProtocolError as exc:
        response = render_response(ExitCode.INVALID_REQUEST, exc.message)
        return response, int(ExitCode.INVALID_REQUEST)

    match request:
        case CheckRequest():
            result = check(request.repo, request.contract)
            return render_response(result.code, result.message), int(result.code)
        case ReplaceRequest():
            checked = check(request.repo, request.contract)
            if checked.code is not ExitCode.OK or checked.contract is None:
                return (
                    _render_replace_check_failure(checked.code, checked.message),
                    int(checked.code),
                )
            receipt = replace_once(
                request.repo,
                checked.contract,
                request.path,
                request.expected_sha256,
                request.old_text,
                request.new_text,
            )
            return render_replace_response(receipt), int(_MUTATION_TO_EXIT[receipt.code])
        case RunTestsRequest():
            checked = check(request.repo, request.contract)
            if checked.code is not ExitCode.OK or checked.contract is None:
                return (
                    _render_test_check_failure(checked.code, checked.message),
                    int(checked.code),
                )
            test_receipt = run_tests(
                request.repo, checked.contract, request.command, base_commit=request.base_commit
            )
            return render_test_response(test_receipt), int(_RUNNER_TO_EXIT[test_receipt.code])
        case _:  # pragma: no cover - ProtocolRequest union closes here
            raise AssertionError(request)


def run_protocol(stdin: BinaryIO, stdout: BinaryIO) -> int:
    """The stdin/stdout plumbing behind the ``protocol`` subcommand."""
    response, code = handle_protocol(stdin.read())
    stdout.write(response.encode("utf-8"))
    stdout.flush()
    return code
