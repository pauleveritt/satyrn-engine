"""Default-tier tests for one bounded replacement."""

import os
from pathlib import Path
from stat import S_IMODE

import pytest

from satyrn_engine.contract import Contract
from satyrn_engine.mutation import (
    MutationCode,
    MutationReceipt,
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


def test_refuses_stale_revision_without_changing_file(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = target.read_bytes()

    receipt = _replace(tmp_path, "app.py", "1", "2", revision="0" * 64)

    assert receipt.code is MutationCode.REVISION_STALE
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

    receipt = replace_once(tmp_path, _contract("app.py"), "app.py", "0" * 64, "1", "2")

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

    def fail_replace(_source: os.PathLike[str] | str, _target: os.PathLike[str] | str) -> None:
        raise OSError("publication denied")

    monkeypatch.setattr(os, "replace", fail_replace)
    receipt = _replace(tmp_path, "app.py", "1", "2")

    assert receipt.code is MutationCode.MUTATION_FAILED
    assert target.read_bytes() == before
    assert list(tmp_path.glob(".app.py.satyrn-*.tmp")) == []
