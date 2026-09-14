"""Contract loading and validation."""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from .exits import ExitCode

REQUIRED_FIELDS = ("id", "task")


class ContractError(Exception):
    """A named refusal from loading or validating a contract."""

    def __init__(self, code: ExitCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Contract:
    """A parsed, valid contract."""

    id: str
    task: str
    writable_paths: tuple[str, ...] = ()
    test_command: tuple[str, ...] = ()
    turn_budget: int | None = None
    deadline_seconds: float | None = None
    preserve: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    token_budget: int | None = None


def load_contract(path: Path) -> Contract:
    """Read, parse, and validate a contract at ``path``.

    Raises :class:`ContractError` with a stable :class:`ExitCode` for each
    refusal cause: unreadable file, invalid YAML, or a missing/invalid
    required field.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(
            ExitCode.CONTRACT_UNREADABLE,
            f"cannot read contract {path}: {exc}",
        ) from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ContractError(
            ExitCode.CONTRACT_INVALID_YAML,
            f"invalid YAML in {path}: {exc}",
        ) from exc

    if not isinstance(data, dict):
        raise ContractError(
            ExitCode.CONTRACT_MISSING_FIELD,
            f"top level of {path} must be a mapping, not {type(data).__name__}",
        )

    problems = _field_problems(data)
    if problems:
        raise ContractError(ExitCode.CONTRACT_MISSING_FIELD, "; ".join(problems))

    normalized_paths = tuple(cast(list[str], data.get("writable_paths", [])))
    normalized_command = tuple(cast(list[str], data.get("test_command", [])))
    normalized_preserve = tuple(cast(list[str], data.get("preserve", [])))
    normalized_checks = tuple(cast(list[str], data.get("checks", [])))
    raw_deadline = data.get("deadline_seconds")
    return Contract(
        id=data["id"],
        task=data["task"],
        writable_paths=normalized_paths,
        test_command=normalized_command,
        turn_budget=data.get("turn_budget"),
        deadline_seconds=None if raw_deadline is None else float(raw_deadline),
        preserve=normalized_preserve,
        checks=normalized_checks,
        token_budget=data.get("token_budget"),
    )


def _field_problems(data: dict[str, object]) -> list[str]:
    problems: list[str] = []
    for field in REQUIRED_FIELDS:
        value = data.get(field)
        if value is None:
            problems.append(f"missing required field {field!r}")
        elif not isinstance(value, str) or not value.strip():
            problems.append(f"required field {field!r} must be a non-empty string")
    if "writable_paths" in data:
        match data["writable_paths"]:
            case list() as paths if all(isinstance(path, str) and path.strip() for path in paths):
                pass
            case list():
                problems.append("optional field 'writable_paths' must contain only non-empty strings")
            case _:
                problems.append("optional field 'writable_paths' must be a list")

    if "test_command" in data:
        match data["test_command"]:
            case list() as command if command and all(
                isinstance(token, str) and token.strip() for token in command
            ):
                pass
            case list():
                problems.append(
                    "optional field 'test_command' must be a non-empty list of non-empty strings"
                )
            case _:
                problems.append("optional field 'test_command' must be a list")

    for field in ("preserve", "checks"):
        if field in data:
            match data[field]:
                case list() as paths if all(isinstance(path, str) and path.strip() for path in paths):
                    pass
                case list():
                    problems.append(f"optional field {field!r} must contain only non-empty strings")
                case _:
                    problems.append(f"optional field {field!r} must be a list")

    if "turn_budget" in data:
        match data["turn_budget"]:
            case int() if not isinstance(data["turn_budget"], bool) and data["turn_budget"] > 0:
                pass
            case _:
                problems.append("optional field 'turn_budget' must be a positive integer")

    if "token_budget" in data:
        match data["token_budget"]:
            case int() if not isinstance(data["token_budget"], bool) and data["token_budget"] > 0:
                pass
            case _:
                problems.append("optional field 'token_budget' must be a positive integer")

    if "deadline_seconds" in data:
        match data["deadline_seconds"]:
            case int() | float() if (
                not isinstance(data["deadline_seconds"], bool)
                and data["deadline_seconds"] > 0
                and math.isfinite(data["deadline_seconds"])
            ):
                pass
            case _:
                problems.append(
                    "optional field 'deadline_seconds' must be a positive finite number"
                )

    return problems
