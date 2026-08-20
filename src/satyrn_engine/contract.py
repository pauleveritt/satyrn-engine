"""Contract loading and validation."""

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
    return Contract(
        id=data["id"],
        task=data["task"],
        writable_paths=normalized_paths,
    )


def _field_problems(data: dict[str, object]) -> list[str]:
    problems: list[str] = []
    for field in REQUIRED_FIELDS:
        value = data.get(field)
        if value is None:
            problems.append(f"missing required field {field!r}")
        elif not isinstance(value, str) or not value.strip():
            problems.append(f"required field {field!r} must be a non-empty string")
    if "writable_paths" not in data:
        return problems

    match data["writable_paths"]:
        case list() as paths if all(isinstance(path, str) and path.strip() for path in paths):
            pass
        case list():
            problems.append("optional field 'writable_paths' must contain only non-empty strings")
        case _:
            problems.append("optional field 'writable_paths' must be a list")
    return problems
