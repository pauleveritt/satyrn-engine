"""Record where every file in this tree came from.

The release-one trees start empty. A file arrives only by an explicit
`git checkout <tag> -- path` or by being written here, and either way it
gets one row in PROVENANCE.md. `check` names any file that has neither.
No model, no network, no subprocess.
"""

import sys
from pathlib import Path

TAG = "pre-release-one-2026-09-13"
TRACKED_DIRS = ("src", "tests", "scripts", "tools", "arms", "docs")
TRACKED_ROOT_SUFFIXES = (".md", ".toml", ".py")
TRACKED_ROOT_NAMES = ("Justfile", "LICENSE", ".gitignore", ".gitattributes")
SKIP_PARTS = frozenset({"__pycache__", ".venv", "node_modules", "_build", ".pytest_cache", ".ruff_cache"})
SKIP_SUFFIXES = frozenset({".pyc"})
HEADER = "# Provenance\n\n| path | source |\n|---|---|\n"


def _rows_file(root: Path) -> Path:
    path = root / "PROVENANCE.md"
    if not path.exists():
        path.write_text(HEADER)
    return path


def _append(root: Path, paths: list[str], source: str) -> None:
    for rel in paths:
        if not (root / rel).exists():
            raise FileNotFoundError(rel)
    with _rows_file(root).open("a") as handle:
        for rel in paths:
            handle.write(f"| {rel} | {source} |\n")


def record_imported(root: Path, sha: str, paths: list[str]) -> None:
    _append(root, paths, f"{TAG} @ {sha}")


def record_new(root: Path, paths: list[str]) -> None:
    _append(root, paths, "created in release-one")


def recorded(root: Path) -> set[str]:
    rows = set()
    for line in _rows_file(root).read_text().splitlines():
        if line.startswith("| ") and not line.startswith("| path ") and not line.startswith("|---"):
            rows.add(line.split("|")[1].strip())
    return rows


def tracked(root: Path) -> list[str]:
    files: list[str] = []
    for directory in TRACKED_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or any(p in SKIP_PARTS for p in path.parts) or path.suffix in SKIP_SUFFIXES:
                continue
            files.append(path.relative_to(root).as_posix())
    for path in sorted(root.iterdir()):
        if path.is_file() and (path.suffix in TRACKED_ROOT_SUFFIXES or path.name in TRACKED_ROOT_NAMES):
            if path.name != "PROVENANCE.md":
                files.append(path.name)
    return files


def check(root: Path) -> list[str]:
    have = recorded(root)
    return [rel for rel in tracked(root) if rel not in have]


def main(argv: list[str]) -> int:
    root = Path.cwd()
    match argv:
        case ["record", "--sha", sha, *paths] if paths:
            record_imported(root, sha, paths)
        case ["new", *paths] if paths:
            record_new(root, paths)
        case ["check"]:
            missing = check(root)
            for rel in missing:
                print(f"no provenance: {rel}")
            return 1 if missing else 0
        case _:
            print("usage: provenance.py record --sha SHA PATH... | new PATH... | check", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
