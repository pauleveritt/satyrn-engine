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

## What is Satyrn Engine?

### The big picture

The engine is the Python core of a two-repo effort: a library and CLI
that parse and validate a bounded contract, enforce writable paths and
revision state, and deliver a candidate change as a reviewable ref or a
receipt — never writing to the caller's tree. The sibling repository,
satyrn-evals, runs the workloads and measurements; the features built
into the engine are the ones that evidence surfaces. What the engine
deliberately does **not** own: workloads, grading, repeated runs,
comparison statistics, or contract authoring — those stay in satyrn-evals
or remain a main-agent skill.

### How it works, from an end-user's perspective

From your side it is a command: `uv run satyrn-engine check --repo REPO
CONTRACT`, or `/implement CONTRACT` inside Pi. The engine parses and
validates the contract, lints the repository path it names, and either
accepts it silently (exit `0`; `OK` over the protocol) or refuses with a
named cause and a stable exit code — no model calls, no processes
started, on every path. In later phases, an accepted contract becomes a
candidate change: a reviewable ref in an isolated worktree, or a receipt
— yours to review and own.

### What is planned

One phase at a time, each shipping one user-visible behavior:

- **E1 — It installs and refuses.** `check` parses, validates, and
  path-lints a contract. *Complete.*
- **E2 — The adapter reaches E1.** `/implement` reaches the same refusal
  through the TypeScript adapter. *Complete.*
- **E3 — Delivery.** `deliver` creates or discards a candidate ref in an
  isolated worktree. *Current.*
- **E4 — One bounded replacement.** A single file replacement runs Pi →
  TypeScript → Python with revision checking. *Not started.*
- **E5 — One real attempt.** `attempt` and `/implement` complete one named
  task end to end. *Not started.*
- **E6 — Packaged.** The same `/implement` works outside either source
  checkout. *Not started.*

The roadmap, concept budget, and backlog live in `ROADMAP.md` at the
repository root; the mission and status live in the README.

See {doc}`usage` for both surfaces and {doc}`architecture` for the shape.

```{toctree}
:maxdepth: 1
:caption: Usage

usage
glossary
```

```{toctree}
:maxdepth: 1
:caption: Development

architecture
contributing
sdd
superpowers/index
```
