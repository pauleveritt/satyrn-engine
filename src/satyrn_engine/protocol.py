"""The one-shot JSON protocol surface the Pi adapter talks to.

One request in on stdin, one response out on stdout, then exit. The
verdict travels in the JSON; the process exit code mirrors it so a caller
that cannot parse the response still has a named signal. stderr stays
empty on every protocol path.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .check import check
from .exits import ExitCode

PROTOCOL_VERSION = 1
OPERATIONS = ("check",)
REQUEST_FIELDS = ("repo", "contract")


class ProtocolError(Exception):
    """A malformed request, refused as INVALID_REQUEST."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Request:
    operation: str
    repo: Path
    contract: Path


def _decode(data: str | bytes) -> str:
    if isinstance(data, bytes):
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError(f"request is not valid UTF-8: {exc}") from exc
    return data


def parse_request(data: str | bytes) -> Request:
    """Parse and validate one protocol request; raise ProtocolError."""
    text = _decode(data)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"request is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError(f"request top level must be a mapping, not {type(payload).__name__}")
    if payload.get("version") != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported protocol version {payload.get('version')!r}; expected {PROTOCOL_VERSION}"
        )
    if payload.get("operation") not in OPERATIONS:
        raise ProtocolError(
            f"unsupported operation {payload.get('operation')!r}; expected {OPERATIONS[0]!r}"
        )
    for field in REQUEST_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise ProtocolError(f"request field {field!r} must be a non-empty string")
    return Request(operation=payload["operation"], repo=Path(payload["repo"]), contract=Path(payload["contract"]))


def render_response(code: ExitCode, message: str) -> str:
    """Render one protocol response as a JSON string."""
    return json.dumps(
        {"version": PROTOCOL_VERSION, "ok": code is ExitCode.OK, "code": code.name, "message": message},
        separators=(",", ":"),
    )


def handle_protocol(data: str | bytes) -> tuple[str, int]:
    """Turn one request into (response_text, exit_code); never raises for input problems."""
    try:
        request = parse_request(data)
    except ProtocolError as exc:
        response = render_response(ExitCode.INVALID_REQUEST, exc.message)
        return response, int(ExitCode.INVALID_REQUEST)
    result = check(request.repo, request.contract)
    return render_response(result.code, result.message), int(result.code)


def run_protocol(stdin: BinaryIO, stdout: BinaryIO) -> int:
    """The stdin/stdout plumbing behind the ``protocol`` subcommand."""
    response, code = handle_protocol(stdin.read())
    stdout.write(response.encode("utf-8"))
    stdout.flush()
    return code
