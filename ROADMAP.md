# Roadmap

> **Planning surface, not the front door.** Where the current phase, the
> concept budget, deferred candidates, and the backlog live. Not where a
> new contributor should start — see
> [`README.md`](README.md) for what's usable now.

*Phases group feature cycles. One direction at a time. Tangents go to the
Backlog, not into the current phase.*

## Now

**Repository scaffolding — complete.** The toolchain (`uv`, `ruff`,
`pyrefly`, `pytest`), the docs stack, CI for Pages, and the superpowers
structure are initialized. `BRIEF.md` is landed. The guards
(`packages/engine/engine.ts`, `tools/replay_guards.mjs`, their replay
fixtures) are copied in verbatim from `local-ai-pi` and are **not a
phase** — see `BRIEF.md`.

**Phase E1 — It installs and refuses. Complete.** `satyrn-engine check
--repo REPO CONTRACT` parses, validates, and path-lints a contract and
refuses with a named cause and a stable exit code — zero model calls, zero
processes. Spec and plan:
`docs/superpowers/specs/2026-08-16-e1-check-design.md`,
`docs/superpowers/plans/2026-08-16-e1-check.md`.

**Phase E2 — The adapter reaches E1. Not started; the current phase.** See
the Phases table below and `BRIEF.md` for the binding rules. Do not reopen
the phase list or the architecture — both are settled by the two-repo
rewrite research, cited in
`docs/superpowers/research/2026-08-16-harvest-index.md`.

## Concept budget

*Every term below is a cost against a 5–10 h/wk volunteer's ability to hold
the design in mind. Checked and updated at the end of each cycle; a term
earns its place by naming something the design actually needs, not by being
convenient shorthand.*

Seed terms, not yet defined in this repository's own words — define each
when the phase that needs it lands: **candidate**, **receipt**,
**adapter**, **guard**, **worktree isolation**. Defined so far:
**contract**, with E1's other working terms, in `docs/glossary.md`.

## Phases

| # | Phase | Direction (one sentence) | Status |
|---|-------|--------------------------|--------|
| E1 | It installs and refuses | `check` parses, validates, path-lints, and refuses a contract with a named cause, zero model calls, zero processes started | **done** |
| E2 | The adapter reaches E1 | `/implement CONTRACT` reaches the same refusal through the TypeScript adapter, on POSIX and Windows — the architecture gate | **current** |
| E3 | Delivery | `deliver` creates or discards a candidate ref in an isolated worktree, from a trivial executable standing in for the model | not started |
| E4 | One bounded replacement | A single file replacement runs Pi → TypeScript → Python with revision checking | not started |
| E5 | One real attempt | `attempt` and `/implement` complete one named task end to end from a source checkout | not started |
| E6 | Packaged | The same `/implement` works outside either source checkout, on POSIX and Windows | not started |

Full done-when criteria for each phase are in `BRIEF.md`'s referenced
roadmap research, not restated here to avoid drift between two copies.

## Backlog

Deferred, each with the condition that reopens it — see `BRIEF.md`:
a persistent sidecar (reopens on measured startup cost); a subinterpreter
pool (reopens on a concurrent caller); guards in Python (reopens only if
the everyday path acquires a Python prerequisite for some other reason —
the latency argument against it is wrong and should not be re-derived);
contract authoring (stays a main-agent skill); a multi-method protocol
(add a second method only when a vertical slice needs it).

## Prior work

Completed phases move here (or to `docs/superpowers/phase-history.md`)
when the roadmap outgrows the front page.

- **E1 — It installs and refuses.** `check` parses, validates, and
  path-lints a contract, refusing with a named cause and a stable exit
  code, zero model calls and zero processes. Spec:
  `docs/superpowers/specs/2026-08-16-e1-check-design.md`. Plan:
  `docs/superpowers/plans/2026-08-16-e1-check.md`.

## Workflow

This repository runs on spec-driven development — see
[`docs/sdd.md`](docs/sdd.md). Each feature cycle gets a committed design
spec, an implementation plan, then code. The default test suite needs no
model, network, or subprocess; process behavior lives in a small marked
integration tier.
