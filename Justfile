# Local docs tooling. The CI build (`.github/workflows/pages.yml`) is the
# strict `-W` one-shot; these targets are for working in the docs.

# Rebuild the docs as you edit them, serving the result on
# http://127.0.0.1:8000 (sphinx-autobuild; add `--open-browser` to open it)
watch-docs:
    uv run --group docs sphinx-autobuild docs docs/_build/html

# One-shot strict build — the same gate CI runs, for a quick check
docs:
    uv run --group docs sphinx-build -W -b html docs docs/_build/html
