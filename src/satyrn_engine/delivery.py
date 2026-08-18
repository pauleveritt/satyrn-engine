"""E3 delivery receipts and, in later tasks, the delivery lifecycle."""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, TypedDict

from .exits import ExitCode

DEFAULT_TIMEOUT = 30.0
RECEIPT_VERSION: Literal[1] = 1


class DeliveryOutcome(StrEnum):
    """Closed vocabulary for the high-level receipt result."""

    CANDIDATE_CREATED = "candidate-created"
    DISCARDED = "discarded"
    REFUSED = "refused"


class DeliveryCode(StrEnum):
    """Closed vocabulary for the authoritative delivery result."""

    OK = "OK"
    CONTRACT_UNREADABLE = "CONTRACT_UNREADABLE"
    CONTRACT_INVALID_YAML = "CONTRACT_INVALID_YAML"
    CONTRACT_MISSING_FIELD = "CONTRACT_MISSING_FIELD"
    REPO_UNAVAILABLE = "REPO_UNAVAILABLE"
    REPO_NOT_GIT = "REPO_NOT_GIT"
    REPO_DIRTY = "REPO_DIRTY"
    INVALID_CANDIDATE_ID = "INVALID_CANDIDATE_ID"
    CANDIDATE_EXISTS = "CANDIDATE_EXISTS"
    COMMAND_UNAVAILABLE = "COMMAND_UNAVAILABLE"
    COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
    COMMAND_FAILED = "COMMAND_FAILED"
    COMMAND_CHANGED_HEAD = "COMMAND_CHANGED_HEAD"
    NO_CHANGES = "NO_CHANGES"
    GIT_FAILED = "GIT_FAILED"
    CLEANUP_FAILED = "CLEANUP_FAILED"


_CODE_TO_OUTCOME: dict[DeliveryCode, DeliveryOutcome] = {
    DeliveryCode.OK: DeliveryOutcome.CANDIDATE_CREATED,
    DeliveryCode.CONTRACT_UNREADABLE: DeliveryOutcome.REFUSED,
    DeliveryCode.CONTRACT_INVALID_YAML: DeliveryOutcome.REFUSED,
    DeliveryCode.CONTRACT_MISSING_FIELD: DeliveryOutcome.REFUSED,
    DeliveryCode.REPO_UNAVAILABLE: DeliveryOutcome.REFUSED,
    DeliveryCode.REPO_NOT_GIT: DeliveryOutcome.REFUSED,
    DeliveryCode.REPO_DIRTY: DeliveryOutcome.REFUSED,
    DeliveryCode.INVALID_CANDIDATE_ID: DeliveryOutcome.REFUSED,
    DeliveryCode.CANDIDATE_EXISTS: DeliveryOutcome.REFUSED,
    DeliveryCode.COMMAND_UNAVAILABLE: DeliveryOutcome.REFUSED,
    DeliveryCode.COMMAND_TIMEOUT: DeliveryOutcome.DISCARDED,
    DeliveryCode.COMMAND_FAILED: DeliveryOutcome.DISCARDED,
    DeliveryCode.COMMAND_CHANGED_HEAD: DeliveryOutcome.DISCARDED,
    DeliveryCode.NO_CHANGES: DeliveryOutcome.DISCARDED,
    DeliveryCode.GIT_FAILED: DeliveryOutcome.REFUSED,
    DeliveryCode.CLEANUP_FAILED: DeliveryOutcome.REFUSED,
}

_CODE_TO_EXIT: dict[DeliveryCode, ExitCode] = {
    DeliveryCode.OK: ExitCode.OK,
    DeliveryCode.CONTRACT_UNREADABLE: ExitCode.CONTRACT_UNREADABLE,
    DeliveryCode.CONTRACT_INVALID_YAML: ExitCode.CONTRACT_INVALID_YAML,
    DeliveryCode.CONTRACT_MISSING_FIELD: ExitCode.CONTRACT_MISSING_FIELD,
    DeliveryCode.REPO_UNAVAILABLE: ExitCode.REPO_UNAVAILABLE,
    DeliveryCode.REPO_NOT_GIT: ExitCode.NO_CANDIDATE,
    DeliveryCode.REPO_DIRTY: ExitCode.NO_CANDIDATE,
    DeliveryCode.INVALID_CANDIDATE_ID: ExitCode.NO_CANDIDATE,
    DeliveryCode.CANDIDATE_EXISTS: ExitCode.NO_CANDIDATE,
    DeliveryCode.COMMAND_UNAVAILABLE: ExitCode.NO_CANDIDATE,
    DeliveryCode.COMMAND_TIMEOUT: ExitCode.NO_CANDIDATE,
    DeliveryCode.COMMAND_FAILED: ExitCode.NO_CANDIDATE,
    DeliveryCode.COMMAND_CHANGED_HEAD: ExitCode.NO_CANDIDATE,
    DeliveryCode.NO_CHANGES: ExitCode.NO_CANDIDATE,
    DeliveryCode.GIT_FAILED: ExitCode.NO_CANDIDATE,
    DeliveryCode.CLEANUP_FAILED: ExitCode.NO_CANDIDATE,
}

_CHECK_REFUSAL_TO_DELIVERY_CODE: dict[ExitCode, DeliveryCode] = {
    ExitCode.CONTRACT_UNREADABLE: DeliveryCode.CONTRACT_UNREADABLE,
    ExitCode.CONTRACT_INVALID_YAML: DeliveryCode.CONTRACT_INVALID_YAML,
    ExitCode.CONTRACT_MISSING_FIELD: DeliveryCode.CONTRACT_MISSING_FIELD,
    ExitCode.REPO_UNAVAILABLE: DeliveryCode.REPO_UNAVAILABLE,
}


class DeliveryPayload(TypedDict):
    """Stable JSON shape emitted for every accepted delivery operation."""

    version: Literal[1]
    outcome: DeliveryOutcome
    code: DeliveryCode
    message: str
    contract_id: str | None
    repository: str
    base_commit: str | None
    candidate_ref: str | None
    candidate_commit: str | None
    changed_paths: list[str] | None
    command_exit: int | None
    worktree_path: str | None


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """One stable machine-readable result from an accepted delivery operation."""

    code: DeliveryCode
    message: str
    contract_id: str | None
    repository: str
    base_commit: str | None
    candidate_ref: str | None
    candidate_commit: str | None
    changed_paths: tuple[str, ...] | None
    command_exit: int | None
    worktree_path: str | None
    version: Literal[1] = RECEIPT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.code, DeliveryCode):
            raise TypeError("code must be a DeliveryCode")

    @property
    def outcome(self) -> DeliveryOutcome:
        """Derive the coarse result from the authoritative delivery code."""
        return _CODE_TO_OUTCOME[self.code]

    @property
    def exit_code(self) -> ExitCode:
        """Return the stable shell code without expanding it for every cause."""
        return _CODE_TO_EXIT[self.code]

    def payload(self) -> DeliveryPayload:
        """Return all receipt fields in their stable serialization order."""
        return {
            "version": self.version,
            "outcome": self.outcome,
            "code": self.code,
            "message": self.message,
            "contract_id": self.contract_id,
            "repository": self.repository,
            "base_commit": self.base_commit,
            "candidate_ref": self.candidate_ref,
            "candidate_commit": self.candidate_commit,
            "changed_paths": None if self.changed_paths is None else list(self.changed_paths),
            "command_exit": self.command_exit,
            "worktree_path": self.worktree_path,
        }

    def render(self) -> str:
        """Render exactly one compact UTF-8 JSON object followed by a newline."""
        return json.dumps(self.payload(), ensure_ascii=True, separators=(",", ":")) + "\n"
