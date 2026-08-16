# Contributing

Welcome. The most useful thing to know up front: **you can contribute here
without a model server, a GPU, or any of the research history.** This is
ordinary Python with hermetic tests.

## Test commands

```bash
uv sync                  # install the project and the dev group
uv run pytest            # default, hermetic suite
uv run ruff check .      # lint
uv run pyrefly check     # type-check
```

`just docs` runs the same strict Sphinx build CI runs; `just watch-docs`
serves a live-rebuilding copy at http://127.0.0.1:8000.

## Repository conventions

- **Spec-driven development.** Every real feature has a committed design
  spec and implementation plan under `docs/superpowers/specs/` and
  `docs/superpowers/plans/` before the code — see
  [`sdd.md`](sdd.md).
- **Verify, don't assert.** A claim (a fix works, a test is non-vacuous, a
  refusal fires) gets demonstrated — stash the fix and show the new test
  fails first, or write the exploit and run it — not just stated.
- **No machinery ahead of the contract it serves.** Build what a real task
  needs, not what might be needed later. Deferred ideas go to
  `ROADMAP.md`'s Backlog, never into the current phase.
- **Concept budget.** New jargon is a real cost. If a change needs a term a
  contributor doing this a few hours a week can't quickly absorb, prefer
  cutting the term over keeping it — see `ROADMAP.md`'s concept budget.
- **A refusal test has a sibling success test**, so rejection cannot pass
  vacuously.
- **Modern 3.14 Python.** Prefer `match`/`case` over long `if`/`elif`
  chains, `type` aliases (PEP 695) under semantic names over
  `TypeVar`/`TypeAlias` assignments, and the walrus operator (`:=`) where
  it saves a repeated computation.
