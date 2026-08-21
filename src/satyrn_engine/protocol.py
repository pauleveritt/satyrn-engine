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

PROTOCOL_VERSION = 1
OPERATIONS = ("check", "replace")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

_MUTATION_TO_EXIT: dict[MutationCode, ExitCode] = {
    MutationCode.OK: ExitCode.OK,
    MutationCode.PATH_UNDECLARED: ExitCode.MUTATION_REFUSED,
    MutationCode.REVISION_UNAVAILABLE: ExitCode.MUTATION_REFUSED,
    MutationCode.REVISION_STALE: ExitCode.MUTATION_REFUSED,
    MutationCode.ANCHOR_MISSING: ExitCode.MUTATION_REFUSED,
    MutationCode.ANCHOR_AMBIGUOUS: ExitCode.MUTATION_REFUSED,
    MutationCode.MUTATION_FAILED: ExitCode.MUTATION_REFUSED,
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


type ProtocolRequest = CheckRequest | ReplaceRequest


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


class ReplaceResponsePayload(ResponsePayload):
    """Replacement response, including an operation-specific result."""

    result: MutationResultPayload | None


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
        result = {"path": receipt.result.path, "sha256": receipt.result.sha256}
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


def handle_protocol(data: str | bytes) -> tuple[str, int]:
    """Turn one request into ``(response_text, exit_code)``."""
    try:
        request = parse_request(data)
    except ProtocolError as exc:
        response = render_response(ExitCode.INVALID_REQUEST, exc.message)
        return response, int(ExitCode.INVALID_REQUEST)

    if isinstance(request, CheckRequest):
        result = check(request.repo, request.contract)
        return render_response(result.code, result.message), int(result.code)

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


def run_protocol(stdin: BinaryIO, stdout: BinaryIO) -> int:
    """The stdin/stdout plumbing behind the ``protocol`` subcommand."""
    response, code = handle_protocol(stdin.read())
    stdout.write(response.encode("utf-8"))
    stdout.flush()
    return code
