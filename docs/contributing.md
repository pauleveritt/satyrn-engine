# Contributing

Welcome. The most useful thing to know up front: **you can contribute here
without a model server, a GPU, or any of the research history.** This is
ordinary Python with hermetic tests.

## Test commands

```bash
uv sync                  # install the project and the dev group
uv run pytest            # default, hermetic suite
uv run pytest -m integration   # local subprocess/Git tier; excluded from CI
uv run pytest -m "" --cov      # both tiers, 100% statement + branch coverage
node tools/replay_orchestrator.mjs   # adapter replay harness (TypeScript, no Python)
node --test --experimental-strip-types --experimental-test-coverage \
  --test-coverage-lines=100 --test-coverage-branches=100 \
  --test-coverage-functions=100 \
  --test-coverage-include=packages/engine/engine.ts tests/test_loop_breaker.mjs
node --experimental-strip-types tools/replay_guards.mjs  # all guard evidence fixtures
node --test --experimental-strip-types --experimental-test-coverage \
  --test-coverage-lines=100 --test-coverage-branches=100 \
  --test-coverage-functions=100 \
  --test-coverage-include=packages/engine/mutator.ts tests/test_mutator.mjs
node --test --experimental-strip-types --experimental-test-coverage \
  --test-coverage-lines=100 --test-coverage-branches=100 \
  --test-coverage-functions=100 \
  --test-coverage-include=packages/engine/orchestrator.ts \
  tests/test_orchestrator.mjs tests/test_transport.mjs
uv run ruff check .      # lint
uv run pyrefly check     # type-check
```

The default suite never starts a process or opens a socket (enforced
mechanically). The integration tier spawns the engine over the {term}`protocol`,
exercises delivery with temporary local Git repositories and real commands,
routes E4's shipped TypeScript mutator through the real Python process, and
runs E5 through a fake Pi that drives that same mutation path. It is excluded
from the default run and from CI. The combined
coverage command enables coverage's subprocess patch and fails below 100%
statement or branch coverage. See {doc}`architecture`.

The Node test commands independently enforce 100% line, branch, and function
coverage for the shipped loop breaker, bounded replacement adapter, and E5
orchestrator, including its termination lifecycle. The guard replay imports
that same `engine.ts` and runs all six retained fixtures in one process; none
of the Node behavior suites uses a model or network.

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
