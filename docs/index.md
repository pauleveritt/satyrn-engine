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

## Status

Phases completed, each with its design spec and implementation plan:

- [_E1_](https://github.com/pauleveritt/satyrn-engine/tree/e1) — it installs and refuses. `satyrn-engine check --repo REPO CONTRACT`
  parses, validates, and path-lints a contract, refusing with a named cause
  and a stable exit code. ({doc}`spec <superpowers/specs/2026-08-16-e1-check-design>`, {doc}`plan <superpowers/plans/2026-08-16-e1-check>`)
- [_E2_](https://github.com/pauleveritt/satyrn-engine/tree/e2) — the adapter reaches E1. `/implement CONTRACT` reaches the same
  refusal through the TypeScript adapter over one-shot, versioned JSON —
  one Python process per operation. ({doc}`spec <superpowers/specs/2026-08-16-e2-adapter-reaches-e1-design>`, {doc}`plan <superpowers/plans/2026-08-16-e2-adapter-reaches-e1>`)
- _E3_ — delivery. `deliver` runs one trusted command in a detached worktree
  pinned to the caller's exact `HEAD`, then emits one receipt without touching
  the caller's checkout. A successful changed tree also publishes the candidate
  named in that receipt. ({doc}`spec
  <superpowers/specs/2026-08-18-e3-delivery-design>`, {doc}`plan
  <superpowers/plans/2026-08-18-e3-delivery>`)

The roadmap and the next phase (E3.5 — The guards, written here) live in
[`ROADMAP.md`](https://github.com/pauleveritt/satyrn-engine/blob/main/ROADMAP.md).

## What is Satyrn Engine?

### The big picture

The engine is the Python core of a two-repo effort: a library and CLI
that parse and validate a bounded contract and deliver a candidate change as a
reviewable ref recorded in a receipt — never writing to the caller's tree. E4
adds writable-path and revision enforcement; E5 adds validation. The sibling repository,
satyrn-evals, runs the workloads and measurements; the features built
into the engine are the ones that evidence surfaces. What the engine
deliberately does **not** own: workloads, grading, repeated runs,
comparison statistics, or contract authoring — those stay in satyrn-evals
or remain a main-agent skill.

### How it works, from an end-user's perspective

From your side it is `check`, `deliver`, or `/implement CONTRACT` inside Pi.
`check` parses and validates without starting a process. `deliver` runs one
explicit command in an isolated worktree and returns one JSON receipt. A
successful receipt names its published candidate; other receipts publish no
candidate, although `candidate_ref` can still record the intended identity. It
never merges the result or writes to the caller's checkout.

### What is planned

One phase at a time, each shipping one user-visible behavior:

- **E1 — It installs and refuses.** `check` parses, validates, and
  path-lints a contract. *Complete.*
- **E2 — The adapter reaches E1.** `/implement` reaches the same refusal
  through the TypeScript adapter. *Complete.*
- **E3 — Delivery.** `deliver` emits a receipt for one isolated attempt and
  publishes a candidate ref only for a successful changed tree. *Complete.*
- **E3.5 — The guards, written here.** The loop breaker is implemented fresh
  and proven against replay fixtures. *Not started.*
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
