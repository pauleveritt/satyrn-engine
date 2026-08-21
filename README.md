# Satyrn Engine

**Help Python developers write code their way, using Local AI.**

Satyrn is a two-repo effort to make that practical: a developer's own AI
partner works on their machine, in their repo, at their pace — and its
output arrives as something they review and own, never as a rewrite of
their working tree underneath them.

**satyrn-engine** is the Python core of that effort. It exists because
small models — the ones that fit on your own machine — wind up in the
ditch: they lose their place, edit the wrong file, drift from the task.
Satyrn finds those problems and fixes them in the engine, keeping a small
model on track. That pays off twice: the model works faster, because it
avoids the problems instead of stumbling over them; and the change it
produces reads the way you would have written it — your conventions, your
standards, your repo — ready for you to review and own.

Despite the name, it is not a model or inference server. It is ordinary
Python that runs anywhere Python runs — a library, a CLI, in CI, from other
tooling — and E5 can start one explicitly selected Pi model through a thin
TypeScript adapter.

> More: [architecture](docs/architecture.md) ·
> [glossary](docs/glossary.md)

## What it owns — and doesn't

The engine owns:

- contract parsing and validation;
- contract-aware writable-path and revision enforcement for one replacement;
- candidate worktree, commit-or-discard, and receipt behavior;
- the Pi-side loop breaker for repeated identical tool calls;
- the Pi package and its thin TypeScript adapter;
- one real Pi attempt that connects isolation to bounded replacement;
- the internal Pi-adapter protocol and its compatibility fixtures.

It does **not** own workloads, grading, repeated runs, comparison
statistics, or contract authoring. Those live in the satyrn-evals
repository, or stay a main-agent skill. That split is deliberate: evals
runs the measurements, and the features built into the engine are the ones
that evidence surfaces — no machinery ahead of its contract.

> More: [glossary](docs/glossary.md) — the terms used here (`contract`,
> `adapter`, `protocol`, `refusal`, …), defined in this repository's own
> words.

## Usage

From a checkout, `uv sync` installs the engine into the project
environment. The CLI validates a contract, runs one command in an isolated
Git worktree, or runs one model inside a worktree that is already disposable.
Delivery and attempt require POSIX and Git 2.36 or newer:

```console
$ uv run satyrn-engine check --repo REPO CONTRACT
$ uv run satyrn-engine deliver --repo REPO CONTRACT -- COMMAND [ARG ...]
$ uv run satyrn-engine attempt --model MODEL CONTRACT
```

Inside Pi, the adapter exposes the same engine as a command:

```console
/implement CONTRACT
```

Set both `SATYRN_ENGINE_REPO` and `SATYRN_MODEL` before using `/implement`.
It runs `attempt` inside E3 isolation, so the source checkout is never the
model's workspace.

`check` acceptance is silent (`OK` over the protocol); its refusal is a
one-line `satyrn-engine: <CAUSE>: <detail>` on stderr. `deliver` writes one
JSON receipt to stdout and sends command output to stderr. A successful command
that changes the tree creates a candidate commit under
`refs/satyrn/candidates/<id>/head`; no result is ever applied or merged.

> More: [usage](docs/usage.md) — contract format, the exit-code table, and
> how to install the adapter.

## Status

Phases completed, each with its design spec and implementation plan:

- [_E1_](https://github.com/pauleveritt/satyrn-engine/tree/e1) — it installs and refuses. `satyrn-engine check --repo REPO CONTRACT`
  parses, validates, and path-lints a contract, refusing with a named cause
  and a stable exit code. ([_spec_](https://github.com/pauleveritt/satyrn-engine/blob/main/docs/superpowers/specs/2026-08-16-e1-check-design.md), [_plan_](https://github.com/pauleveritt/satyrn-engine/blob/main/docs/superpowers/plans/2026-08-16-e1-check.md))
- [_E2_](https://github.com/pauleveritt/satyrn-engine/tree/e2) — the adapter reaches E1. `/implement CONTRACT` reaches the same
  refusal through the TypeScript adapter over one-shot, versioned JSON —
  one Python process per operation. ([_spec_](https://github.com/pauleveritt/satyrn-engine/blob/main/docs/superpowers/specs/2026-08-16-e2-adapter-reaches-e1-design.md), [_plan_](https://github.com/pauleveritt/satyrn-engine/blob/main/docs/superpowers/plans/2026-08-16-e2-adapter-reaches-e1.md))
- _E3_ — delivery. On POSIX systems, `deliver` runs one trusted command in a
  detached worktree pinned to the caller's exact `HEAD` and always emits a
  receipt. A successful changed tree also publishes one reviewable candidate
  ref.
  ([_spec_](docs/superpowers/specs/2026-08-18-e3-delivery-design.md), [_plan_](docs/superpowers/plans/2026-08-18-e3-delivery.md))
- _E3.5_ — the loop breaker, written here. The Pi package refuses a sixth
  identical tool call while five matching admitted calls remain in its
  twenty-call window, with registration-local state and `loop_broken`
  telemetry. ([_spec_](docs/superpowers/specs/2026-08-20-e3-5-loop-breaker-design.md), [_plan_](docs/superpowers/plans/2026-08-20-e3-5-loop-breaker.md))
- _E4_ — one bounded replacement. A conditional Pi `edit` override sends one
  exact replacement to Python, which enforces the contract's writable path,
  captured SHA-256 revision, and unique anchor before an atomic write.
  ([_spec_](docs/superpowers/specs/2026-08-20-e4-bounded-replacement-design.md), [_plan_](docs/superpowers/plans/2026-08-20-e4-bounded-replacement.md))
- _E5_ — one real attempt. `attempt` gives one explicit Pi model `read` plus
  E4's bounded `edit`; `/implement` runs that command inside E3 and reports
  its candidate or named refusal. Artifact publication is pinned outside every
  registered worktree and Git administrative directory, and timeout reporting
  waits for E3 to tear down the attempt.
  ([_spec_](docs/superpowers/specs/2026-08-20-e5-real-attempt-design.md), [_plan_](docs/superpowers/plans/2026-08-20-e5-real-attempt.md))

The roadmap and the next phase (E6 — Packaged) live in
[`ROADMAP.md`](ROADMAP.md).

> More: [architecture](docs/architecture.md) — why the engine is one
> process per operation, and why the guards stay TypeScript.

## Development

This repository presumes `uv`, `ruff`, `pyrefly`, and `pytest`:

```bash
uv sync                # install the project and the dev group
uv run pytest          # default, hermetic suite: no model, no network, no subprocess
uv run pytest -m "" --cov  # default + local integration tier, 100% branch coverage
node --test --experimental-strip-types --experimental-test-coverage \
  --test-coverage-lines=100 --test-coverage-branches=100 \
  --test-coverage-functions=100 \
  --test-coverage-include=packages/engine/engine.ts tests/test_loop_breaker.mjs
node --experimental-strip-types tools/replay_guards.mjs
node --test --experimental-strip-types --experimental-test-coverage \
  --test-coverage-lines=100 --test-coverage-branches=100 \
  --test-coverage-functions=100 \
  --test-coverage-include=packages/engine/mutator.ts tests/test_mutator.mjs
node --test --experimental-strip-types --experimental-test-coverage \
  --test-coverage-lines=100 --test-coverage-branches=100 \
  --test-coverage-functions=100 \
  --test-coverage-include=packages/engine/orchestrator.ts \
  tests/test_orchestrator.mjs tests/test_transport.mjs
uv run ruff check .    # lint
uv run pyrefly check   # type-check
```

Docs are Sphinx with MyST and Furo. `just docs` runs the same strict build
CI runs; `just watch-docs` serves a live-rebuilding copy at
http://127.0.0.1:8000.

> More: [contributing](docs/contributing.md) — the integration tier
> (`uv run pytest -m integration`), the adapter replay harness, and the
> repository conventions.

## License

Apache-2.0 — see [LICENSE](LICENSE).
