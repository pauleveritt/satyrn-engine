"""Cap the active planning documents, and refuse trailing whitespace.

Written here rather than transplanted. `satyrn-evals` runs a larger checker
with status-column and backlog-entry caps its roadmap shape needs; this
repository's roadmap has a different shape, so the behaviour is re-earned
from what this repository actually has -- `CLAUDE.md`'s provenance rule, and
its "no framework before three concrete implementations" rule.

No model, no network, no subprocess: it fits the default test tier and runs
from `just lint-docs`.
"""

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

type Failure = str

FILE_CAPS: dict[str, int] = {"ROADMAP.md": 400, "BACKLOG.md": 400}
"""Why a cap at all: an unbounded roadmap stops being read, and a planning
surface nobody reads is where a settled decision gets re-derived. 400 is the
figure `satyrn-evals` has run with since 2026-09-02."""

DIRECTION_CAP = 400
"""One phase row's Direction cell. The column header says "one sentence"; the
cap is what makes that a check rather than an aspiration."""

SKIP_PARTS = frozenset({"_build", ".venv", "node_modules", ".git", ".worktrees"})


@dataclass(frozen=True, slots=True)
class Report:
    failures: list[Failure]

    def render(self) -> int:
        for line in self.failures:
            print(f"  {line}")
        if self.failures:
            print(f"\nlint-docs: {len(self.failures)} active-document check failures.")
            return 1
        print("lint-docs: all documents within cap")
        return 0


def _cells(line: str) -> list[str] | None:
    """Cells of a Markdown table row, or None when the line is not one."""
    if not line.startswith("|") or set(line) <= set("|- "):
        return None
    return [c.strip() for c in line.strip().strip("|").split("|")]


def check_file_caps(root: Path) -> list[Failure]:
    failures: list[Failure] = []
    for name, cap in FILE_CAPS.items():
        path = root / name
        if not path.is_file():
            failures.append(f"{name}: missing, but capped -- the regime expects it")
            continue
        if (count := len(path.read_text().splitlines())) > cap:
            failures.append(f"{name}: {count} lines exceeds the {cap}-line cap")
    return failures


def check_direction_cells(root: Path) -> list[Failure]:
    """Cap the Direction cell of each phase row.

    The header is matched rather than the column index, so inserting a column
    does not silently start capping the wrong one.
    """
    path = root / "ROADMAP.md"
    if not path.is_file():
        return []
    index: int | None = None
    failures: list[Failure] = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if (cells := _cells(line)) is None:
            continue
        if index is None:
            for position, cell in enumerate(cells):
                if cell.strip("* ").lower().startswith("direction"):
                    index = position
            continue
        if index < len(cells) and len(cells[index]) > DIRECTION_CAP:
            failures.append(
                f"ROADMAP.md:{number}: Direction cell is {len(cells[index])} "
                f"characters, over the {DIRECTION_CAP} cap"
            )
    return failures


def check_trailing_whitespace(root: Path) -> list[Failure]:
    failures: list[Failure] = []
    for path in sorted(root.rglob("*.md")):
        if SKIP_PARTS & set(path.parts):
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if line != line.rstrip():
                failures.append(
                    f"{path.relative_to(root)}:{number}: trailing whitespace"
                )
    return failures


def run(root: Path = ROOT) -> Report:
    return Report(
        check_file_caps(root)
        + check_direction_cells(root)
        + check_trailing_whitespace(root)
    )


if __name__ == "__main__":
    raise SystemExit(run().render())
