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

Phase E1 ships: `satyrn-engine check --repo REPO CONTRACT` parses and
validates a contract and lints the repository path, refusing with a named
cause and a stable exit code. The current phase — the TypeScript adapter
reaching E1 — and the phase list live in [`ROADMAP.md`](ROADMAP.md).

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
