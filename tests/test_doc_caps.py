"""The document checker, proven to fail before it is trusted.

Every check here has a refusal and its sibling success (`CLAUDE.md`). The
refusals matter more than usual: a checker that cannot fail reports a green
planning surface forever, and this repository's own lessons file records that
family.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from lint_docs import (  # noqa: E402
    DIRECTION_CAP,
    FILE_CAPS,
    check_direction_cells,
    check_file_caps,
    check_trailing_whitespace,
    run,
)

REPO = Path(__file__).resolve().parents[1]


def _roadmap(tmp_path: Path, body: str) -> Path:
    (tmp_path / "ROADMAP.md").write_text(body)
    (tmp_path / "BACKLOG.md").write_text("# Backlog\n")
    return tmp_path


def test_this_repository_passes_every_check() -> None:
    """The success sibling for all three checks at once, on real documents."""
    assert run(REPO).failures == []


def test_an_over_long_roadmap_is_refused(tmp_path: Path) -> None:
    root = _roadmap(tmp_path, "x\n" * (FILE_CAPS["ROADMAP.md"] + 1))
    failures = check_file_caps(root)
    assert failures and "exceeds the 400-line cap" in failures[0]


def test_a_roadmap_at_the_cap_is_accepted(tmp_path: Path) -> None:
    root = _roadmap(tmp_path, "x\n" * FILE_CAPS["ROADMAP.md"])
    assert check_file_caps(root) == []


def test_a_missing_capped_file_is_refused(tmp_path: Path) -> None:
    """Absence is a failure, not a pass. A checker that skips what is not
    there reports green on an empty repository."""
    (tmp_path / "ROADMAP.md").write_text("# Roadmap\n")
    failures = check_file_caps(tmp_path)
    assert any("BACKLOG.md: missing" in f for f in failures)


_TABLE = "| # | Phase | Direction (one sentence) | Status |\n|---|---|---|---|\n"


def test_an_over_long_direction_cell_is_refused(tmp_path: Path) -> None:
    root = _roadmap(tmp_path, _TABLE + f"| E1 | x | {'d' * (DIRECTION_CAP + 1)} | done |\n")
    failures = check_direction_cells(root)
    assert failures and "over the 400 cap" in failures[0]


def test_a_direction_cell_within_the_cap_is_accepted(tmp_path: Path) -> None:
    root = _roadmap(tmp_path, _TABLE + f"| E1 | x | {'d' * DIRECTION_CAP} | done |\n")
    assert check_direction_cells(root) == []


def test_the_direction_column_is_found_by_header_not_position(
    tmp_path: Path,
) -> None:
    """Inserting a column must not silently start capping a different one."""
    header = "| # | Owner | Phase | Direction (one sentence) |\n|---|---|---|---|\n"
    root = _roadmap(tmp_path, header + f"| E1 | me | x | {'d' * (DIRECTION_CAP + 1)} |\n")
    assert check_direction_cells(root)


def test_a_table_with_no_direction_column_is_left_alone(tmp_path: Path) -> None:
    header = "| # | Phase | Status |\n|---|---|---|\n"
    root = _roadmap(tmp_path, header + f"| E1 | {'d' * 900} | done |\n")
    assert check_direction_cells(root) == []


def test_trailing_whitespace_is_refused(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("clean\ntrailing \n")
    failures = check_trailing_whitespace(tmp_path)
    assert failures and "notes.md:2" in failures[0]


def test_a_clean_document_is_accepted(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("clean\nalso clean\n")
    assert check_trailing_whitespace(tmp_path) == []


def test_the_build_output_is_not_scanned(tmp_path: Path) -> None:
    """Sphinx writes markdown sources into `_build`; scanning them would fail
    the gate on generated copies of files that already passed."""
    built = tmp_path / "docs" / "_build"
    built.mkdir(parents=True)
    (built / "generated.md").write_text("trailing \n")
    assert check_trailing_whitespace(tmp_path) == []


def test_the_report_exit_code_distinguishes_pass_from_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gate reads this exit code, so it is asserted rather than assumed."""
    assert run(_roadmap(tmp_path, "# Roadmap\n")).render() == 0
    over = _roadmap(tmp_path, "x\n" * (FILE_CAPS["ROADMAP.md"] + 1))
    assert run(over).render() == 1
    assert "check failures" in capsys.readouterr().out
