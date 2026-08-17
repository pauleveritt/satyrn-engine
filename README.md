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

Phases completed, each with its design spec and implementation plan:

- [_E1_](https://github.com/pauleveritt/satyrn-engine/tree/e1) — it installs and refuses. `satyrn-engine check --repo REPO CONTRACT`
  parses, validates, and path-lints a contract, refusing with a named cause
  and a stable exit code. ([_spec_](https://github.com/pauleveritt/satyrn-engine/blob/main/docs/superpowers/specs/2026-08-16-e1-check-design.md), [_plan_](https://github.com/pauleveritt/satyrn-engine/blob/main/docs/superpowers/plans/2026-08-16-e1-check.md))
- [_E2_](https://github.com/pauleveritt/satyrn-engine/tree/e2) — the adapter reaches E1. `/implement CONTRACT` reaches the same
  refusal through the TypeScript adapter over one-shot, versioned JSON —
  one Python process per operation. ([_spec_](https://github.com/pauleveritt/satyrn-engine/blob/main/docs/superpowers/specs/2026-08-16-e2-adapter-reaches-e1-design.md), [_plan_](https://github.com/pauleveritt/satyrn-engine/blob/main/docs/superpowers/plans/2026-08-16-e2-adapter-reaches-e1.md))

The roadmap and the current phase (E3 — Delivery) live in
[`ROADMAP.md`](ROADMAP.md).

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
