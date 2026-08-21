"""One contract-bounded, revision-checked exact replacement."""

import os
import secrets
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch
from hashlib import sha256
from pathlib import Path
from stat import S_IMODE, S_ISREG

from .contract import Contract


class MutationCode(StrEnum):
    """Closed mutation outcomes carried by the JSON protocol."""

    OK = "OK"
    PATH_UNDECLARED = "PATH_UNDECLARED"
    REVISION_UNAVAILABLE = "REVISION_UNAVAILABLE"
    REVISION_STALE = "REVISION_STALE"
    ANCHOR_MISSING = "ANCHOR_MISSING"
    ANCHOR_AMBIGUOUS = "ANCHOR_AMBIGUOUS"
    MUTATION_FAILED = "MUTATION_FAILED"


@dataclass(frozen=True, slots=True)
class MutationResult:
    """The next revision produced by a successful replacement."""

    path: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str):
            raise TypeError("mutation result path must be a string")
        try:
            normalize_relative_path(self.path)
        except ValueError as exc:
            raise ValueError("mutation result path must be a safe relative path") from exc
        if not isinstance(self.sha256, str):
            raise TypeError("mutation result sha256 must be a string")
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ValueError("mutation result sha256 must be 64 lowercase hexadecimal characters")


@dataclass(frozen=True, slots=True)
class MutationReceipt:
    """One handled replacement result."""

    code: MutationCode
    message: str = ""
    result: MutationResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, MutationCode):
            raise TypeError("code must be a MutationCode")
        if not isinstance(self.message, str):
            raise TypeError("message must be a string")
        if self.code is MutationCode.OK:
            if not isinstance(self.result, MutationResult):
                raise ValueError("successful mutation receipt requires a result")
        elif self.result is not None:
            raise ValueError("refused mutation receipt must not carry a result")

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
    expected_sha256: str | None,
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
        parent_descriptor, target_descriptor, target_name = _open_target(root, path)
    except OSError as exc:
        return MutationReceipt(MutationCode.MUTATION_FAILED, f"cannot read mutation target {path}: {exc}")

    try:
        try:
            target_stat = os.fstat(target_descriptor)
            if not S_ISREG(target_stat.st_mode):
                raise OSError(f"mutation target is not a regular file: {path}")
            with os.fdopen(target_descriptor, "rb", closefd=False) as input_file:
                before = input_file.read()
            before.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            return MutationReceipt(MutationCode.MUTATION_FAILED, f"cannot read mutation target {path}: {exc}")

        if expected_sha256 is None:
            return MutationReceipt(
                MutationCode.REVISION_UNAVAILABLE,
                f"no captured revision is available for {path}",
            )
        actual_sha256 = file_sha256(before)
        if actual_sha256 != expected_sha256:
            return MutationReceipt(
                MutationCode.REVISION_STALE,
                f"file revision changed for {path}: expected {expected_sha256}, found {actual_sha256}",
            )

        try:
            old_bytes = old_text.encode("utf-8")
            new_bytes = new_text.encode("utf-8")
        except UnicodeEncodeError as exc:
            return MutationReceipt(MutationCode.MUTATION_FAILED, f"cannot encode replacement text: {exc}")
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

        after = before.replace(old_bytes, new_bytes, 1)
        try:
            _atomic_replace(parent_descriptor, target_name, S_IMODE(target_stat.st_mode), after)
        except OSError as exc:
            return MutationReceipt(MutationCode.MUTATION_FAILED, f"cannot replace {path}: {exc}")
        return MutationReceipt(
            MutationCode.OK,
            result=MutationResult(path=path, sha256=file_sha256(after)),
        )
    finally:
        os.close(target_descriptor)
        os.close(parent_descriptor)


def _open_target(root: Path, path: str) -> tuple[int, int, str]:
    """Open a regular-file candidate without following any path symlink."""
    components = path.split("/")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent_descriptor = os.open(root, directory_flags)
    try:
        for component in components[:-1]:
            child_descriptor = os.open(component, directory_flags, dir_fd=parent_descriptor)
            os.close(parent_descriptor)
            parent_descriptor = child_descriptor
        target_name = components[-1]
        target_descriptor = os.open(target_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_descriptor)
    except BaseException:
        os.close(parent_descriptor)
        raise
    return parent_descriptor, target_descriptor, target_name


def _atomic_replace(parent_descriptor: int, target_name: str, mode: int, content: bytes) -> None:
    """Atomically replace one entry relative to a pinned, no-follow parent."""
    temporary_name = f".{target_name}.satyrn-{secrets.token_hex(8)}.tmp"
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent_descriptor,
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
        os.replace(
            temporary_name,
            target_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
    finally:
        with suppress(OSError):
            os.unlink(temporary_name, dir_fd=parent_descriptor)
