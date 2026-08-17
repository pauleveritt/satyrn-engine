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

Despite the name, it is not an engine in the AI sense: no model, no
inference, no server. It is ordinary Python that runs anywhere Python
runs — a library, a CLI, in CI, from other tooling — with Pi as one
surface it serves through a thin TypeScript adapter.

> More: [architecture](docs/architecture.md) ·
> [glossary](docs/glossary.md)

## What it owns — and doesn't

The engine owns:

- contract parsing and validation;
- writable-path and revision enforcement;
- candidate worktree, validation, commit-or-discard, and receipt behavior;
- the Pi package and its thin TypeScript adapter;
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
environment. The CLI validates a contract against a repository:

```console
$ uv run satyrn-engine check --repo REPO CONTRACT
```

Inside Pi, the adapter exposes the same engine as a command:

```console
/implement CONTRACT
```

Acceptance is silent over the CLI (`OK` over the protocol); a refusal is
a one-line `satyrn-engine: <CAUSE>: <detail>` on stderr with a stable
exit code — no model calls, no processes started, on every path.

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

The roadmap and the current phase (E3 — Delivery) live in
[`ROADMAP.md`](ROADMAP.md).

> More: [architecture](docs/architecture.md) — why the engine is one
> process per operation, and why the guards stay TypeScript.

## Development

This repository presumes `uv`, `ruff`, `pyrefly`, and `pytest`:

```bash
uv sync                # install the project and the dev group
uv run pytest          # default, hermetic suite: no model, no network, no subprocess
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
