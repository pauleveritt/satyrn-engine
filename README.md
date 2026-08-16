# Satyrn Engine

**Turns a bounded contract into a candidate change without modifying the
caller's working tree.**

The engine is the Python core of the two-repo satyrn effort. It owns:

- the Python library and CLI;
- contract parsing and validation;
- writable-path and revision enforcement;
- candidate worktree, validation, commit-or-discard, and receipt behavior;
- the Pi package and its thin TypeScript adapter;
- the internal Pi-adapter protocol and its compatibility fixtures.

It does **not** own workloads, grading, repeated runs, comparison
statistics, or contract authoring. Those live in the satyrn-evals
repository, or stay a main-agent skill.

## Status

Phase E2 ships: `/implement CONTRACT` in the TypeScript adapter reaches
the same refusal as `check` — the adapter starts the engine as a
subprocess (`uv run --project $SATYRN_ENGINE_REPO satyrn-engine
protocol`), sends one versioned JSON request, reads one JSON response, and
converts every transport failure into a named refusal. Verified end to end
on POSIX; the recorded Windows run is the remaining gate (see
[`ROADMAP.md`](ROADMAP.md)).

## Toolchain

This repository presumes `uv`, `ruff`, `pyrefly`, and `pytest`:

```bash
uv sync                # install the project and the dev group
uv run pytest          # default, hermetic test suite
uv run ruff check .    # lint
uv run pyrefly check   # type-check
```

Docs are Sphinx with MyST and Furo. `just docs` runs the same strict build
CI runs; `just watch-docs` serves a live-rebuilding copy at
http://127.0.0.1:8000.
