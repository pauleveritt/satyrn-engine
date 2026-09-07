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
    NO_CHANGE_REQUESTED = "NO_CHANGE_REQUESTED"
    ANCHOR_MISSING = "ANCHOR_MISSING"
    ANCHOR_ALREADY_APPLIED = "ANCHOR_ALREADY_APPLIED"
    ANCHOR_AMBIGUOUS = "ANCHOR_AMBIGUOUS"
    MUTATION_FAILED = "MUTATION_FAILED"


# E9: across 499 retained transcripts, 2,872 edit calls failed, and the
# model's freshest *textual* view of the file stayed its pre-edit `read` --
# a successful edit returned only `Replaced app.py; sha256=...`, no file
# state at all. `region` carries the post-edit text back instead so the
# model's next call is grounded in what the file now says. See
# docs/superpowers/specs/2026-09-07-e9-file-state-in-edit-results-design.md.
REGION_CONTEXT_LINES = 3
REGION_MAX_LINES = 40
REGION_MAX_BYTES = 4_000
_REGION_TRUNCATION_MARKER = (
    f"...[region truncated at {REGION_MAX_LINES} lines / {REGION_MAX_BYTES} bytes]...\n"
)


@dataclass(frozen=True, slots=True)
class MutationResult:
    """The next revision produced by a successful replacement."""

    path: str
    sha256: str
    region: str = ""

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
        if not isinstance(self.region, str):
            raise TypeError("mutation result region must be a string")


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


def _line_of_offset(content: bytes, offset: int) -> int:
    """The 1-based line number containing byte `offset` of `content`."""
    return content.count(b"\n", 0, offset) + 1


def _post_edit_region(before: bytes, after: bytes, anchor_offset: int, new_bytes: bytes) -> str:
    """The changed region of `after`: numbered lines, context, capped.

    `anchor_offset` is the byte offset of the (unique) match inside
    `before`; because `after` is `before` with exactly that span replaced,
    everything up to `anchor_offset` is identical in both, so the same
    offset locates the start of the change in `after` too.
    """
    start_line = _line_of_offset(before, anchor_offset)
    end_line = start_line + new_bytes.count(b"\n")
    lines = after.decode("utf-8").splitlines()
    total = len(lines)
    region_start = max(1, start_line - REGION_CONTEXT_LINES)
    region_end = min(total, end_line + REGION_CONTEXT_LINES)
    numbered = [f"{number}: {lines[number - 1]}" for number in range(region_start, region_end + 1)]

    truncated = False
    if len(numbered) > REGION_MAX_LINES:
        numbered = numbered[:REGION_MAX_LINES]
        truncated = True
    rendered = "\n".join(numbered)
    encoded = rendered.encode("utf-8")
    if len(encoded) > REGION_MAX_BYTES:
        truncated = True
        cut = encoded[:REGION_MAX_BYTES]
        while cut and (cut[-1] & 0xC0) == 0x80:
            cut = cut[:-1]
        rendered = cut.decode("utf-8", errors="ignore")
    return rendered + "\n" + _REGION_TRUNCATION_MARKER if truncated else rendered


def replace_once(
    repo: Path,
    contract: Contract,
    path: str,
    expected_sha256: str | None,
    old_text: str,
    new_text: str,
) -> MutationReceipt:
    """Replace one exact unique anchor or return a typed refusal."""
    try:
        path = normalize_relative_path(path)
    except ValueError as exc:
        return MutationReceipt(MutationCode.MUTATION_FAILED, f"invalid mutation path: {exc}")
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

        # (c) checked before any write, and before the anchor is even
        # located: 386 edits across 42 cells had old_text == new_text and
        # were reported OK, "Replaced" -- active misinformation about a
        # no-op. See the E9 design doc referenced above.
        if old_bytes == new_bytes:
            return MutationReceipt(
                MutationCode.NO_CHANGE_REQUESTED,
                f"old_text and new_text are identical in {path}; nothing to replace",
            )

        anchor_offset = before.find(old_bytes)
        match before.count(old_bytes):
            case 0:
                # (b) 182 of 208 ANCHOR_MISSING events, across the same
                # sample, were an edit that had already landed, re-sent
                # with its original (now-stale) anchor. A plain substring
                # test -- a fact about the file, not a similarity guess
                # (fuzzy anchor matching is refused, E8). `new_bytes` is
                # never empty here: the identity check above already
                # returned for old_bytes == new_bytes == b"", and an empty
                # `new_bytes` would otherwise "match" trivially everywhere
                # and say nothing true about the file.
                if new_bytes and new_bytes in before:
                    line = _line_of_offset(before, before.find(new_bytes))
                    return MutationReceipt(
                        MutationCode.ANCHOR_ALREADY_APPLIED,
                        f"new_text is already present in {path} at line {line}; old_text was not found",
                    )
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
            result=MutationResult(
                path=path,
                sha256=file_sha256(after),
                region=_post_edit_region(before, after, anchor_offset, new_bytes),
            ),
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
