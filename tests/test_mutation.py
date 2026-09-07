"""Default-tier tests for one bounded replacement."""

import os
from pathlib import Path
from stat import S_IMODE

import pytest

from satyrn_engine.contract import Contract
from satyrn_engine.mutation import (
    REGION_MAX_BYTES,
    REGION_MAX_LINES,
    MutationCode,
    MutationReceipt,
    MutationResult,
    file_sha256,
    normalize_relative_path,
    replace_once,
)


def _contract(*patterns: str) -> Contract:
    return Contract(id="e4", task="replace", writable_paths=patterns)


def _replace(
    repo: Path,
    path: str,
    old_text: str,
    new_text: str,
    *,
    contract: Contract | None = None,
    revision: str | None = None,
) -> MutationReceipt:
    target = repo / path
    expected = revision if revision is not None else file_sha256(target.read_bytes())
    return replace_once(repo, contract or _contract(path), path, expected, old_text, new_text)


def test_replaces_one_unique_anchor_and_returns_next_revision(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("def value():\n    return 1\n", encoding="utf-8")
    target.chmod(0o754)

    receipt = _replace(tmp_path, "app.py", "return 1", "return 2")

    assert receipt.code is MutationCode.OK
    assert receipt.ok is True
    assert receipt.message == ""
    assert target.read_bytes() == b"def value():\n    return 2\n"
    assert receipt.result is not None
    assert receipt.result.path == "app.py"
    assert receipt.result.sha256 == file_sha256(target.read_bytes())
    # E9(a): the model-facing text is the post-edit region, not the hash --
    # the sha256 above is still carried, but only in `details`/the protocol
    # `result`, never in the text a model reads.
    assert receipt.result.region == "1: def value():\n2:     return 2"
    assert S_IMODE(target.stat().st_mode) == 0o754


def test_fnmatch_pattern_admits_nested_path(tmp_path: Path) -> None:
    target = tmp_path / "src" / "nested" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("value = 1\n", encoding="utf-8")

    receipt = _replace(
        tmp_path,
        "src/nested/app.py",
        "1",
        "2",
        contract=_contract("src/*.py"),
    )

    assert receipt.code is MutationCode.OK
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_refuses_undeclared_path_without_changing_file(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = target.read_bytes()

    receipt = _replace(tmp_path, "app.py", "1", "2", contract=_contract("src/*.py"))

    assert receipt.code is MutationCode.PATH_UNDECLARED
    assert receipt.ok is False
    assert receipt.result is None
    assert target.read_bytes() == before


def test_direct_mutation_seam_refuses_path_traversal(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = tmp_path / "outside.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = target.read_bytes()

    receipt = replace_once(
        repo,
        _contract("*"),
        "../outside.py",
        file_sha256(before),
        "1",
        "2",
    )

    assert receipt.code is MutationCode.MUTATION_FAILED
    assert "invalid mutation path" in receipt.message
    assert target.read_bytes() == before


def test_refuses_stale_revision_without_changing_file(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = target.read_bytes()

    receipt = _replace(tmp_path, "app.py", "1", "2", revision="0" * 64)

    assert receipt.code is MutationCode.REVISION_STALE
    assert target.read_bytes() == before


def test_refuses_unavailable_revision_after_path_and_target_checks(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = target.read_bytes()

    unavailable = replace_once(tmp_path, _contract("app.py"), "app.py", None, "1", "2")
    undeclared = replace_once(tmp_path, _contract("src/*.py"), "app.py", None, "1", "2")
    missing = replace_once(tmp_path, _contract("missing.py"), "missing.py", None, "1", "2")

    assert unavailable.code is MutationCode.REVISION_UNAVAILABLE
    assert undeclared.code is MutationCode.PATH_UNDECLARED
    assert missing.code is MutationCode.MUTATION_FAILED
    assert target.read_bytes() == before


def test_refuses_missing_anchor_without_changing_file(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = target.read_bytes()

    receipt = _replace(tmp_path, "app.py", "value = 2", "value = 3")

    assert receipt.code is MutationCode.ANCHOR_MISSING
    assert target.read_bytes() == before


def test_refuses_ambiguous_anchor_without_changing_file(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\nvalue = 1\n", encoding="utf-8")
    before = target.read_bytes()

    receipt = _replace(tmp_path, "app.py", "value = 1", "value = 2")

    assert receipt.code is MutationCode.ANCHOR_AMBIGUOUS
    assert target.read_bytes() == before


def test_refuses_missing_anchor_already_applied_names_the_line(tmp_path: Path) -> None:
    """E9(b): 182 of 208 ANCHOR_MISSING events, in the transcripts behind
    the design doc, were an edit that had already landed, re-sent with its
    original (now-stale) anchor. Sibling of
    ``test_refuses_missing_anchor_without_changing_file`` below, which pins
    the unchanged behaviour when ``new_text`` is *also* absent."""
    target = tmp_path / "app.py"
    target.write_text("def value():\n    return 2\n", encoding="utf-8")
    before = target.read_bytes()

    receipt = _replace(tmp_path, "app.py", "return 1", "return 2")

    assert receipt.code is MutationCode.ANCHOR_ALREADY_APPLIED
    assert receipt.result is None
    assert "line 2" in receipt.message
    assert target.read_bytes() == before


def test_refuses_missing_anchor_with_empty_new_text_stays_anchor_missing(tmp_path: Path) -> None:
    """An empty ``new_text`` (a deletion) is trivially "present" in every
    file, so it carries no fact about whether the deletion already
    happened -- treated as ANCHOR_MISSING, not ANCHOR_ALREADY_APPLIED."""
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = target.read_bytes()

    receipt = _replace(tmp_path, "app.py", "value = 2\n", "")

    assert receipt.code is MutationCode.ANCHOR_MISSING
    assert target.read_bytes() == before


def test_refuses_identical_replacement_without_changing_file(tmp_path: Path) -> None:
    """E9(c): sibling of the genuine-replacement success above
    (``test_replaces_one_unique_anchor_and_returns_next_revision``); 386
    edits across 42 cells had ``old_text == new_text`` and were reported
    OK, "Replaced" before this change."""
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = target.read_bytes()

    receipt = _replace(tmp_path, "app.py", "value = 1", "value = 1")

    assert receipt.code is MutationCode.NO_CHANGE_REQUESTED
    assert receipt.result is None
    assert target.read_bytes() == before


def test_identity_check_precedes_anchor_lookup(tmp_path: Path) -> None:
    """An identical old_text/new_text pair that does not even occur in the
    file still refuses as NO_CHANGE_REQUESTED, not ANCHOR_MISSING -- the
    check runs before the anchor is located at all."""
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = target.read_bytes()

    receipt = _replace(tmp_path, "app.py", "value = 9", "value = 9")

    assert receipt.code is MutationCode.NO_CHANGE_REQUESTED
    assert target.read_bytes() == before


def test_region_truncates_when_line_count_exceeds_cap(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("before\nANCHOR\nafter\n", encoding="utf-8")

    new_text = "\n".join(f"line-{index:03d}" for index in range(100))
    receipt = _replace(tmp_path, "app.py", "ANCHOR", new_text)

    assert receipt.code is MutationCode.OK
    assert receipt.result is not None
    region = receipt.result.region
    assert "truncated" in region
    numbered_lines = [line for line in region.splitlines() if line and line[0].isdigit()]
    assert len(numbered_lines) <= REGION_MAX_LINES
    # Genuine replacement still applies byte-identically regardless of the
    # message's own truncation.
    assert target.read_text(encoding="utf-8") == f"before\n{new_text}\nafter\n"


def test_region_truncates_when_byte_size_exceeds_cap(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("before\nANCHOR\nafter\n", encoding="utf-8")

    long_line = "x" * 300
    new_text = "\n".join(long_line for _ in range(20))  # ~6000 bytes, well under 40 lines
    receipt = _replace(tmp_path, "app.py", "ANCHOR", new_text)

    assert receipt.code is MutationCode.OK
    assert receipt.result is not None
    region = receipt.result.region
    assert "truncated" in region
    assert len(region.encode("utf-8")) <= REGION_MAX_BYTES + 200  # cap plus the marker itself
    assert target.read_text(encoding="utf-8") == f"before\n{new_text}\nafter\n"


@pytest.mark.parametrize(
    "path",
    ["", "/app.py", "../app.py", "src/../app.py", "./app.py", "src//app.py", "a\\b.py", "a\0b.py"],
)
def test_normalize_relative_path_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError):
        normalize_relative_path(path)


def test_normalize_relative_path_accepts_posix_relative_path() -> None:
    assert normalize_relative_path("src/nested/app.py") == "src/nested/app.py"


def test_refuses_symlink_escape_without_changing_external_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    external = tmp_path / "external.py"
    external.write_text("value = 1\n", encoding="utf-8")
    (repo / "alias.py").symlink_to(external)
    before = external.read_bytes()

    receipt = replace_once(
        repo,
        _contract("alias.py"),
        "alias.py",
        file_sha256(before),
        "1",
        "2",
    )

    assert receipt.code is MutationCode.MUTATION_FAILED
    assert external.read_bytes() == before


def test_refuses_internal_symlink_leaf_without_changing_target(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "alias.py").symlink_to(target.name)
    before = target.read_bytes()

    receipt = replace_once(
        tmp_path,
        _contract("alias.py"),
        "alias.py",
        file_sha256(before),
        "1",
        "2",
    )

    assert receipt.code is MutationCode.MUTATION_FAILED
    assert target.read_bytes() == before


def test_refuses_internal_symlink_component_without_changing_target(tmp_path: Path) -> None:
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    target = real_directory / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "alias").symlink_to(real_directory.name)
    before = target.read_bytes()

    receipt = replace_once(
        tmp_path,
        _contract("alias/*.py"),
        "alias/app.py",
        file_sha256(before),
        "1",
        "2",
    )

    assert receipt.code is MutationCode.MUTATION_FAILED
    assert target.read_bytes() == before


@pytest.mark.parametrize("kind", ["missing", "directory", "non_utf8"])
def test_refuses_unusable_target(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "app.py"
    match kind:
        case "missing":
            pass
        case "directory":
            target.mkdir()
        case "non_utf8":
            target.write_bytes(b"value = \xff\n")
        case _:  # pragma: no cover - closed parameter set
            raise AssertionError(kind)

    receipt = replace_once(tmp_path, _contract("app.py"), "app.py", None, "1", "2")

    assert receipt.code is MutationCode.MUTATION_FAILED


def test_replacement_is_literal_and_can_be_empty(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("price = '$& \\ value'\nremove = True\n", encoding="utf-8")

    first = _replace(tmp_path, "app.py", "'$& \\ value'", "'$1 \\ next'")
    second = _replace(tmp_path, "app.py", "remove = True\n", "")

    assert first.code is MutationCode.OK
    assert second.code is MutationCode.OK
    assert target.read_text(encoding="utf-8") == "price = '$1 \\ next'\n"


def test_crlf_bytes_are_preserved_outside_replacement(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_bytes(b"first = 1\r\nsecond = 2\r\n")

    receipt = _replace(tmp_path, "app.py", "second = 2", "second = 3")

    assert receipt.code is MutationCode.OK
    assert target.read_bytes() == b"first = 1\r\nsecond = 3\r\n"


def test_atomic_replace_failure_is_named_and_removes_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = target.read_bytes()

    def fail_replace(
        _source: os.PathLike[str] | str,
        _target: os.PathLike[str] | str,
        **_kwargs: object,
    ) -> None:
        raise OSError("publication denied")

    monkeypatch.setattr(os, "replace", fail_replace)
    receipt = _replace(tmp_path, "app.py", "1", "2")

    assert receipt.code is MutationCode.MUTATION_FAILED
    assert target.read_bytes() == before
    assert list(tmp_path.glob(".app.py.satyrn-*.tmp")) == []


@pytest.mark.parametrize("field", ["old", "new"])
def test_direct_api_refuses_non_utf8_replacement_text(tmp_path: Path, field: str) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = target.read_bytes()
    old_text = "\ud800" if field == "old" else "1"
    new_text = "\ud800" if field == "new" else "2"

    receipt = _replace(tmp_path, "app.py", old_text, new_text)

    assert receipt.code is MutationCode.MUTATION_FAILED
    assert target.read_bytes() == before


@pytest.mark.parametrize(
    ("path", "revision", "error"),
    [
        (1, "0" * 64, TypeError),
        ("../app.py", "0" * 64, ValueError),
        ("app.py", 1, TypeError),
        ("app.py", "0" * 63, ValueError),
        ("app.py", "G" * 64, ValueError),
    ],
)
def test_mutation_result_closes_path_and_revision_invariants(
    path: object,
    revision: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        MutationResult(path=path, sha256=revision)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("code", "message", "result", "error"),
    [
        (MutationCode.OK, "", None, ValueError),
        (MutationCode.ANCHOR_MISSING, "missing", MutationResult("app.py", "0" * 64), ValueError),
        ("OK", "", MutationResult("app.py", "0" * 64), TypeError),
        (MutationCode.ANCHOR_MISSING, 1, None, TypeError),
    ],
)
def test_mutation_receipt_closes_success_and_refusal_invariants(
    code: object,
    message: object,
    result: MutationResult | None,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        MutationReceipt(code=code, message=message, result=result)  # type: ignore[arg-type]
