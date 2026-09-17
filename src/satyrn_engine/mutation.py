"""One contract-bounded, revision-checked exact replacement."""

import os
import secrets
from collections.abc import Sequence
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

# Two distinct markers: one for the ordinary case (context around the edit
# was trimmed to fit a cap, but the edit itself is whole) and one for the
# rarer case (the edit itself -- with no context at all -- still busts a
# cap). A reader must be able to tell these apart: the first says the
# edit is intact; the second warns the edit itself did not fully survive.
_REGION_CONTEXT_TRUNCATED_MARKER = (
    f"...[region context trimmed to fit cap: {REGION_MAX_LINES} lines / {REGION_MAX_BYTES} bytes"
    f"; the change above is shown in full]...\n"
)
_REGION_CHANGE_TRUNCATED_MARKER = (
    f"...[region truncated: the change itself exceeds {REGION_MAX_LINES} lines / "
    f"{REGION_MAX_BYTES} bytes with no context]...\n"
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


def _clip_to_bytes(text: str, limit: int) -> str:
    """`text` cut to at most `limit` UTF-8 bytes, never splitting a
    multi-byte character."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    cut = encoded[:limit]
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    return cut.decode("utf-8", errors="ignore")


def _balanced_grow(budget: int, desired_before: int, desired_after: int) -> tuple[int, int]:
    """Split `budget` context lines between the two sides of an edit.

    Grants one line at a time to whichever side has taken fewer so far
    (ties favor `before`), stopping once both sides have what they
    desire or the budget runs out. This keeps the surviving window as
    balanced as possible rather than draining one side first.
    """
    before = after = 0
    remaining = budget
    while remaining > 0 and (before < desired_before or after < desired_after):
        if before < desired_before and before <= after:
            before += 1
        elif after < desired_after:
            after += 1
        else:
            before += 1
        remaining -= 1
    return before, after


def _balanced_shrink(context_before: int, context_after: int, excess: int) -> tuple[int, int]:
    """Remove `excess` lines total from context, one at a time from
    whichever side currently holds more (ties favor removing from
    `after`), so the surviving context stays balanced. Only ever touches
    context counts -- the changed span is never passed through here.
    """
    before, after = context_before, context_after
    remaining = excess
    while remaining > 0 and (before > 0 or after > 0):
        if after >= before and after > 0:
            after -= 1
        elif before > 0:
            before -= 1
        else:
            break
        remaining -= 1
    return before, after


def _post_edit_region(before: bytes, after: bytes, anchor_offset: int, new_bytes: bytes) -> str:
    """The changed region of `after`: numbered lines, context, capped.

    `anchor_offset` is the byte offset of the (unique) match inside
    `before`; because `after` is `before` with exactly that span replaced,
    everything up to `anchor_offset` is identical in both, so the same
    offset locates the start of the change in `after` too.

    Budget is reserved for the changed span FIRST, in both the line and
    byte dimensions; only surplus budget is spent on context, and context
    is what gets trimmed when a cap bites. Only when the changed span
    ALONE busts a cap does the span itself get cut -- and that case is
    marked with a distinct marker from ordinary context trimming, so a
    reader can tell "context was trimmed" from "the change itself did not
    fit".
    """
    start_line = _line_of_offset(before, anchor_offset)
    end_line = start_line + new_bytes.count(b"\n")
    # Split on "\n" alone, never str.splitlines(): the latter also breaks on
    # \x0c, \r, \x1c-\x1e, \x85, U+2028 and U+2029, while `_line_of_offset`
    # and `end_line` count "\n" only. A form feed before the anchor -- ordinary
    # in CPython stdlib modules -- shifted every line number and made the
    # "reserved" span the wrong lines, so the region could show none of the
    # change while reporting no truncation at all.
    lines = after.decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # a trailing newline ends the last line, it does not add one
    total = len(lines)
    if total == 0:
        # The whole file was deleted (or the edit deleted the only
        # remaining line and its trailing newline). Nothing to show.
        return ""
    # A replacement that deletes a whole trailing line (newline included)
    # can point `start_line`/`end_line` past the last line that still
    # exists in `after` -- there is no residual "changed" line to show,
    # so clamp to the last real line rather than indexing past it.
    end_line = min(end_line, total)
    start_line = min(start_line, end_line)
    changed_line_count = end_line - start_line + 1

    def numbered(range_start: int, range_end: int) -> list[str]:
        return [f"{number}: {lines[number - 1]}" for number in range(range_start, range_end + 1)]

    # 1) Reserve the changed span's own line budget before spending
    # anything on context. If the span alone is already too long, it is
    # the only thing that can be truncated -- keep its head and mark it
    # distinctly from a context trim.
    if changed_line_count > REGION_MAX_LINES:
        changed_lines = numbered(start_line, start_line + REGION_MAX_LINES - 1)
    else:
        changed_lines = numbered(start_line, end_line)

    changed_rendered = "\n".join(changed_lines)
    if changed_line_count > REGION_MAX_LINES or len(changed_rendered.encode("utf-8")) > REGION_MAX_BYTES:
        rendered = _clip_to_bytes(changed_rendered, REGION_MAX_BYTES)
        return rendered + "\n" + _REGION_CHANGE_TRUNCATED_MARKER

    # 2) The changed span fits both caps whole. Allocate the remaining
    # LINE budget as context, trimmed from whichever side has more
    # surplus once the naive [start - REGION_CONTEXT_LINES,
    # end + REGION_CONTEXT_LINES] window would exceed REGION_MAX_LINES.
    avail_before = start_line - 1
    avail_after = total - end_line
    desired_before = min(REGION_CONTEXT_LINES, avail_before)
    desired_after = min(REGION_CONTEXT_LINES, avail_after)
    line_budget = REGION_MAX_LINES - changed_line_count

    context_trimmed = desired_before + desired_after > line_budget
    if context_trimmed:
        context_before, context_after = _balanced_grow(line_budget, desired_before, desired_after)
    else:
        context_before, context_after = desired_before, desired_after

    def render(context_before: int, context_after: int) -> str:
        parts: list[str] = []
        if context_before:
            parts.extend(numbered(start_line - context_before, start_line - 1))
        parts.extend(changed_lines)
        if context_after:
            parts.extend(numbered(end_line + 1, end_line + context_after))
        return "\n".join(parts)

    rendered = render(context_before, context_after)

    # 3) Apply the BYTE cap the same way: shrink context lines, never the
    # changed span, until the region fits. The changed span alone was
    # already verified to fit under REGION_MAX_BYTES above, so shrinking
    # context all the way to zero is guaranteed to succeed.
    while len(rendered.encode("utf-8")) > REGION_MAX_BYTES and (context_before > 0 or context_after > 0):
        context_trimmed = True
        context_before, context_after = _balanced_shrink(context_before, context_after, 1)
        rendered = render(context_before, context_after)

    if context_trimmed:
        return rendered + "\n" + _REGION_CONTEXT_TRUNCATED_MARKER
    return rendered


def _close_both(parent_descriptor: int, target_descriptor: int, receipt: MutationReceipt) -> MutationReceipt:
    """Close both descriptors and pass a refusal receipt through -- used by
    `_prepare_target` for every failure branch after the open succeeds, so
    neither descriptor leaks on a refusal that returns before any caller's
    own `finally` is in scope."""
    os.close(target_descriptor)
    os.close(parent_descriptor)
    return receipt


def _prepare_target(
    repo: Path,
    contract: Contract,
    path: str,
    expected_sha256: str | None,
) -> MutationReceipt | tuple[int, int, str, str, int, bytes]:
    """Validate `path`, open it without following any symlink, and check its
    current revision -- every step every replacement needs before any text
    is even looked at. On success the caller owns both descriptors (and
    must close them, typically in a `finally` around a later
    `_atomic_replace`); on failure both descriptors are already closed and
    a refusal receipt is returned. Shared by `replace_once`'s single
    replacement and `replace_many`'s in-memory sequence, so the two open,
    read and revision-check a target exactly the same way.
    """
    try:
        normalized_path = normalize_relative_path(path)
    except ValueError as exc:
        return MutationReceipt(MutationCode.MUTATION_FAILED, f"invalid mutation path: {exc}")
    if not any(fnmatch(normalized_path, pattern) for pattern in contract.writable_paths):
        return MutationReceipt(
            MutationCode.PATH_UNDECLARED,
            f"path is outside the contract's writable paths: {normalized_path}",
        )

    try:
        root = repo.resolve(strict=True)
        parent_descriptor, target_descriptor, target_name = _open_target(root, normalized_path)
    except OSError as exc:
        return MutationReceipt(MutationCode.MUTATION_FAILED, f"cannot read mutation target {normalized_path}: {exc}")

    try:
        target_stat = os.fstat(target_descriptor)
        if not S_ISREG(target_stat.st_mode):
            raise OSError(f"mutation target is not a regular file: {normalized_path}")
        with os.fdopen(target_descriptor, "rb", closefd=False) as input_file:
            before = input_file.read()
        before.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        return _close_both(
            parent_descriptor,
            target_descriptor,
            MutationReceipt(MutationCode.MUTATION_FAILED, f"cannot read mutation target {normalized_path}: {exc}"),
        )

    if expected_sha256 is None:
        return _close_both(
            parent_descriptor,
            target_descriptor,
            MutationReceipt(
                MutationCode.REVISION_UNAVAILABLE,
                f"no captured revision is available for {normalized_path}",
            ),
        )
    actual_sha256 = file_sha256(before)
    if actual_sha256 != expected_sha256:
        return _close_both(
            parent_descriptor,
            target_descriptor,
            MutationReceipt(
                MutationCode.REVISION_STALE,
                f"file revision changed for {normalized_path}: expected {expected_sha256}, found {actual_sha256}",
            ),
        )

    return parent_descriptor, target_descriptor, target_name, normalized_path, S_IMODE(target_stat.st_mode), before


def _apply_replacement(
    current: bytes,
    path: str,
    old_text: str,
    new_text: str,
) -> MutationReceipt | tuple[bytes, int, bytes]:
    """Validate and apply one replacement against `current`'s in-memory
    bytes. Pure -- no I/O -- so it is shared by `replace_once`'s single
    write and `replace_many`'s sequence, which writes only once after every
    replacement in the sequence has been proven to apply. Returns a
    refusal receipt (never OK, never carrying a result) or
    `(after, anchor_offset, new_bytes)`.
    """
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

    anchor_offset = current.find(old_bytes)
    match current.count(old_bytes):
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
            if new_bytes and new_bytes in current:
                line = _line_of_offset(current, current.find(new_bytes))
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

    after = current.replace(old_bytes, new_bytes, 1)
    return after, anchor_offset, new_bytes


def replace_once(
    repo: Path,
    contract: Contract,
    path: str,
    expected_sha256: str | None,
    old_text: str,
    new_text: str,
) -> MutationReceipt:
    """Replace one exact unique anchor or return a typed refusal."""
    prepared = _prepare_target(repo, contract, path, expected_sha256)
    if isinstance(prepared, MutationReceipt):
        return prepared
    parent_descriptor, target_descriptor, target_name, normalized_path, mode, before = prepared

    try:
        outcome = _apply_replacement(before, normalized_path, old_text, new_text)
        if isinstance(outcome, MutationReceipt):
            return outcome
        after, anchor_offset, new_bytes = outcome

        try:
            _atomic_replace(parent_descriptor, target_name, mode, after)
        except OSError as exc:
            return MutationReceipt(MutationCode.MUTATION_FAILED, f"cannot replace {normalized_path}: {exc}")
        return MutationReceipt(
            MutationCode.OK,
            result=MutationResult(
                path=normalized_path,
                sha256=file_sha256(after),
                region=_post_edit_region(before, after, anchor_offset, new_bytes),
            ),
        )
    finally:
        os.close(target_descriptor)
        os.close(parent_descriptor)


#: The most replacements one ``replace`` exchange may carry (design section
#: 5.1, Ruling 8). Baseline's edit tool already allows many; unbounded is not
#: the parity being restored -- a turn cut at the 16,000-token per-turn cap
#: can emit an arbitrarily long `edits` array, and one exchange should stay
#: one bounded unit of work.
MAX_REPLACEMENTS = 16


def _post_edit_regions(before: bytes, after: bytes, applied: Sequence[tuple[int, bytes]]) -> str:
    """The changed regions of `after` for every replacement `replace_many`
    applied, merged into one region so a multi-edit exchange does not
    reintroduce E9 for every replacement but the last (see the module
    comment above `REGION_CONTEXT_LINES`).

    `applied` is `(anchor_offset, new_bytes)` per replacement, in `after`'s
    own byte coordinates (every earlier replacement's recorded offset has
    already been shifted by every later replacement's length delta -- see
    `replace_many`). For exactly one replacement this must render
    byte-for-byte the same as the single-replacement path, so it delegates
    to `_post_edit_region` rather than risk drifting from that function's
    truncation and non-newline-separator handling.

    For more than one, each replacement's changed line span is widened by
    `REGION_CONTEXT_LINES` on both sides into a window; windows that
    overlap or sit adjacent (no blank line of true gap between them) merge
    into one continuous numbered block, so two nearby edits are shown once,
    not as two overlapping copies. Separate blocks are joined by a bare
    "..." line, the way an ordinary unified diff marks a gap. The whole
    rendered result is still capped at REGION_MAX_BYTES, clipped from the
    end with the same marker `_post_edit_region` uses for an over-long
    single change -- unlike that function, this does not yet balance the
    cut across blocks or reserve budget per replacement first; the worst
    case for MAX_REPLACEMENTS widely-spread replacements is exactly that
    cap (REGION_MAX_BYTES) plus the marker's own bytes, never more.
    """
    if len(applied) == 1:
        anchor_offset, new_bytes = applied[0]
        return _post_edit_region(before, after, anchor_offset, new_bytes)

    lines = after.decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # a trailing newline ends the last line, it does not add one
    total = len(lines)
    if total == 0:
        # The whole file was deleted by the sequence. Nothing to show.
        return ""

    windows: list[tuple[int, int]] = []
    for anchor_offset, new_bytes in applied:
        start_line = _line_of_offset(after, anchor_offset)
        end_line = start_line + new_bytes.count(b"\n")
        end_line = min(end_line, total)
        start_line = min(start_line, end_line)
        windows.append((max(1, start_line - REGION_CONTEXT_LINES), min(total, end_line + REGION_CONTEXT_LINES)))

    windows.sort()
    merged: list[tuple[int, int]] = []
    for window_start, window_end in windows:
        if merged and window_start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], window_end))
        else:
            merged.append((window_start, window_end))

    def numbered(range_start: int, range_end: int) -> list[str]:
        return [f"{number}: {lines[number - 1]}" for number in range(range_start, range_end + 1)]

    rendered = "\n...\n".join("\n".join(numbered(window_start, window_end)) for window_start, window_end in merged)
    if len(rendered.encode("utf-8")) > REGION_MAX_BYTES:
        rendered = _clip_to_bytes(rendered, REGION_MAX_BYTES) + "\n" + _REGION_CONTEXT_TRUNCATED_MARKER
    return rendered


def replace_many(
    root: Path,
    contract: Contract,
    path: str,
    expected_sha256: str | None,
    replacements: Sequence[tuple[str, str]],
) -> MutationReceipt:
    """Apply every replacement in order in memory, then write once -- or
    write nothing.

    All-or-nothing, and truly one write: every replacement is validated and
    applied against an in-memory `current` buffer (`_apply_replacement`
    performs no I/O), threaded from one replacement to the next, and
    `_atomic_replace` is called exactly once, only after every replacement
    in the sequence has succeeded. A failure at any replacement leaves the
    file untouched -- there is nothing to restore, because nothing was
    written -- and the refusal names the 1-based failing index, because a
    model that sent four replacements needs to know which one it must fix.
    One exchange, one revision, one mutation generation, one write.
    """
    if not replacements:
        return MutationReceipt(MutationCode.MUTATION_FAILED, "no replacements were supplied")
    if len(replacements) > MAX_REPLACEMENTS:
        return MutationReceipt(
            MutationCode.MUTATION_FAILED,
            f"{len(replacements)} replacements exceeds the limit of {MAX_REPLACEMENTS}; send fewer",
        )

    prepared = _prepare_target(root, contract, path, expected_sha256)
    if isinstance(prepared, MutationReceipt):
        return prepared
    parent_descriptor, target_descriptor, target_name, normalized_path, mode, before = prepared

    try:
        current = before
        # `applied` carries (anchor_offset, new_bytes) per replacement, in
        # `current`'s own evolving coordinate space. When a later
        # replacement changes the byte length of the file, every earlier
        # entry whose span sits at or after that replacement's changed
        # span is shifted by the length delta, so by the time the loop
        # ends every entry is expressed in the FINAL `current` bytes'
        # coordinates -- which is exactly what `_post_edit_regions` needs.
        applied: list[tuple[int, bytes]] = []
        for index, (old_text, new_text) in enumerate(replacements, start=1):
            outcome = _apply_replacement(current, normalized_path, old_text, new_text)
            if isinstance(outcome, MutationReceipt):
                return MutationReceipt(outcome.code, f"replacement {index}: {outcome.message}")
            after, anchor_offset, new_bytes = outcome

            old_bytes_length = len(old_text.encode("utf-8"))
            changed_end = anchor_offset + old_bytes_length
            delta = len(new_bytes) - old_bytes_length
            applied = [
                (start + delta, span) if start >= changed_end else (start, span) for start, span in applied
            ]
            applied.append((anchor_offset, new_bytes))
            current = after

        try:
            _atomic_replace(parent_descriptor, target_name, mode, current)
        except OSError as exc:
            return MutationReceipt(MutationCode.MUTATION_FAILED, f"cannot replace {normalized_path}: {exc}")
        return MutationReceipt(
            MutationCode.OK,
            result=MutationResult(
                path=normalized_path,
                sha256=file_sha256(current),
                region=_post_edit_regions(before, current, applied),
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
