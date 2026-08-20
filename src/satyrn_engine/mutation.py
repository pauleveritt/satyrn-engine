"""One contract-bounded, revision-checked exact replacement."""

import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch
from hashlib import sha256
from pathlib import Path
from stat import S_IMODE

from .contract import Contract


class MutationCode(StrEnum):
    """Closed mutation outcomes carried by the JSON protocol."""

    OK = "OK"
    PATH_UNDECLARED = "PATH_UNDECLARED"
    REVISION_STALE = "REVISION_STALE"
    ANCHOR_MISSING = "ANCHOR_MISSING"
    ANCHOR_AMBIGUOUS = "ANCHOR_AMBIGUOUS"
    MUTATION_FAILED = "MUTATION_FAILED"


@dataclass(frozen=True, slots=True)
class MutationResult:
    """The next revision produced by a successful replacement."""

    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class MutationReceipt:
    """One handled replacement result."""

    code: MutationCode
    message: str = ""
    result: MutationResult | None = None

    @property
    def ok(self) -> bool:
        """Whether the replacement was published."""
        return self.code is MutationCode.OK


def normalize_relative_path(candidate: str) -> str:
    """Return one safe POSIX-style relative path or raise ``ValueError``."""
    if not candidate:
        raise ValueError("path must be a non-empty string")
    if "\0" in candidate:
        raise ValueError("path must not contain NUL")
    if "\\" in candidate:
        raise ValueError("path must use '/' separators")
    if candidate.startswith("/"):
        raise ValueError("path must be relative")
    if any(part in {"", ".", ".."} for part in candidate.split("/")):
        raise ValueError("path must not contain empty, '.' or '..' segments")
    return candidate


def file_sha256(content: bytes) -> str:
    """Return the lowercase SHA-256 revision for exact file bytes."""
    return sha256(content).hexdigest()


def replace_once(
    repo: Path,
    contract: Contract,
    path: str,
    expected_sha256: str,
    old_text: str,
    new_text: str,
) -> MutationReceipt:
    """Replace one exact unique anchor or return a typed refusal."""
    if not any(fnmatch(path, pattern) for pattern in contract.writable_paths):
        return MutationReceipt(
            MutationCode.PATH_UNDECLARED,
            f"path is outside the contract's writable paths: {path}",
        )

    try:
        root = repo.resolve(strict=True)
        target = (root / Path(*path.split("/"))).resolve(strict=True)
        target.relative_to(root)
        if not target.is_file():
            raise OSError(f"mutation target is not a regular file: {path}")
        before = target.read_bytes()
        before.decode("utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        return MutationReceipt(MutationCode.MUTATION_FAILED, f"cannot read mutation target {path}: {exc}")

    actual_sha256 = file_sha256(before)
    if actual_sha256 != expected_sha256:
        return MutationReceipt(
            MutationCode.REVISION_STALE,
            f"file revision changed for {path}: expected {expected_sha256}, found {actual_sha256}",
        )

    old_bytes = old_text.encode()
    match before.count(old_bytes):
        case 0:
            return MutationReceipt(
                MutationCode.ANCHOR_MISSING,
                f"old_text was not found in {path}",
            )
        case 1:
            pass
        case count:
            return MutationReceipt(
                MutationCode.ANCHOR_AMBIGUOUS,
                f"old_text matches {count} locations in {path}; it must be unique",
            )

    after = before.replace(old_bytes, new_text.encode(), 1)
    try:
        _atomic_replace(target, after)
    except OSError as exc:
        return MutationReceipt(MutationCode.MUTATION_FAILED, f"cannot replace {path}: {exc}")
    return MutationReceipt(
        MutationCode.OK,
        result=MutationResult(path=path, sha256=file_sha256(after)),
    )


def _atomic_replace(target: Path, content: bytes) -> None:
    """Atomically replace ``target`` with ``content``, retaining its mode."""
    mode = S_IMODE(target.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.satyrn-",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(mode)
        os.replace(temporary, target)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
