# Local docs tooling. The CI build (`.github/workflows/pages.yml`) is the
# strict `-W` one-shot; these targets are for working in the docs.

# Rebuild the docs as you edit them, serving the result on
# http://127.0.0.1:8000 (sphinx-autobuild; add `--open-browser` to open it)
watch-docs:
    uv run --group docs sphinx-autobuild docs docs/_build/html

# One-shot strict build — the same gate CI runs, for a quick check
docs:
    uv run --group docs sphinx-build -W -b html docs docs/_build/html

# Launch pi with the engine's adapter wired to this checkout. The adapter
# must be installed globally (see docs/usage.md — `cp packages/engine/
# orchestrator.ts ~/.pi/agent/extensions/`). Do NOT also pass it with `-e`:
# pi then registers it twice and suffixes the command (`/implement:1`),
# so the plain `/implement` stops dispatching. This recipe sets
# SATYRN_ENGINE_REPO so /implement spawns this checkout. Run from the repo
# root.
pi-engine:
    @test -f ~/.pi/agent/extensions/orchestrator.ts \
        || (echo "adapter not installed: cp packages/engine/orchestrator.ts ~/.pi/agent/extensions/ (see docs/usage.md)" >&2 && exit 1)
    SATYRN_ENGINE_REPO=$$PWD pi
