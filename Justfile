# Local docs tooling. The CI build (`.github/workflows/pages.yml`) is the
# strict `-W` one-shot; these targets are for working in the docs.

# Rebuild the docs as you edit them, serving the result on
# http://127.0.0.1:8000 (sphinx-autobuild; add `--open-browser` to open it)
watch-docs:
    uv run --group docs sphinx-autobuild docs docs/_build/html

# One-shot strict build — the same gate CI runs, for a quick check
docs:
    uv run --group docs sphinx-build -W -b html docs docs/_build/html

# Launch pi with the package wired to this checkout. Install it once with
# `pi install /path/to/satyrn-engine/packages/engine` (see docs/usage.md).
# Do NOT also pass either extension with `-e`: duplicate registration suffixes
# `/implement` as `/implement:1`. This recipe only sets SATYRN_ENGINE_REPO so
# /implement spawns this checkout. Run it from the repository root.
pi-engine:
    SATYRN_ENGINE_REPO=$$PWD pi

# Cap the active planning documents (ROADMAP, BACKLOG) and refuse trailing
# whitespace. No model, no network, no subprocess.
lint-docs:
    uv run python tools/lint_docs.py

# Every gate, in order, stopping at the first non-zero exit. Read this
# recipe's own exit code; never pipe it into anything, which is how a check
# that cannot fail gets written.
gates:
    uv run pytest -q
    uv run ruff check
    just lint-docs
    just docs
