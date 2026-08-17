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

**Phase E2 — The adapter reaches E1. Complete.**
`/implement CONTRACT` starts the engine through the TypeScript adapter
(`uv run --project $SATYRN_ENGINE_REPO satyrn-engine protocol`), sends one
versioned JSON request, reads one JSON response, and converts every
transport failure into a named refusal. Verified end to end on POSIX: the
shipped `exchange` against the real spawner/uv/engine returns OK and the
named refusals; the extension intercepts `/implement` in a live pi; and a
recorded live run on 2026-08-16 showed `satyrn-engine: OK` for a valid
contract, `satyrn-engine: CONTRACT_UNREADABLE: …` for a missing one, and
`satyrn-engine: USAGE: …` for a missing argument. The phase's done-when
names POSIX and Windows; the Windows leg is deferred with a reopen
condition in the Backlog below, because no Windows machine is available
and the integration tier does not run in CI. Spec and plan:
`docs/superpowers/specs/2026-08-16-e2-adapter-reaches-e1-design.md`,
`docs/superpowers/plans/2026-08-16-e2-adapter-reaches-e1.md`. Do not
reopen the phase list or the architecture — both are settled by the
two-repo rewrite research, cited in
`docs/superpowers/research/2026-08-16-harvest-index.md`.

## Concept budget

*Every term below is a cost against a 5–10 h/wk volunteer's ability to hold
the design in mind. Checked and updated at the end of each cycle; a term
earns its place by naming something the design actually needs, not by being
convenient shorthand.*

Seed terms, not yet defined in this repository's own words — define each
when the phase that needs it lands: **candidate**, **receipt**,
**guard**, **worktree isolation**. Defined so far: **contract**, with
E1's working terms, plus **adapter** and **protocol** (E2), in
`docs/glossary.md`.

## Phases

| # | Phase | Direction (one sentence) | Status |
|---|-------|--------------------------|--------|
| E1 | It installs and refuses | `check` parses, validates, path-lints, and refuses a contract with a named cause, zero model calls, zero processes started | **done** |
| E2 | The adapter reaches E1 | `/implement CONTRACT` reaches the same refusal through the TypeScript adapter, on POSIX and Windows — the architecture gate | **done** (POSIX recorded; Windows leg deferred, see Backlog) |
| E3 | Delivery | `deliver` creates or discards a candidate ref in an isolated worktree, from a trivial executable standing in for the model | not started |
| E4 | One bounded replacement | A single file replacement runs Pi → TypeScript → Python with revision checking | not started |
| E5 | One real attempt | `attempt` and `/implement` complete one named task end to end from a source checkout | not started |
| E6 | Packaged | The same `/implement` works outside either source checkout, on POSIX and Windows | not started |

Done-when criteria are restated in each phase's plan — for E1, the Goal
of `docs/superpowers/plans/2026-08-16-e1-check.md` — not in this file,
to avoid drift between two copies.

## Backlog

Deferred, each with the condition that reopens it — see `BRIEF.md`:
a persistent sidecar (reopens on measured startup cost); a subinterpreter
pool (reopens on a concurrent caller); guards in Python (reopens only if
the everyday path acquires a Python prerequisite for some other reason —
the latency argument against it is wrong and should not be re-derived);
contract authoring (stays a main-agent skill); a multi-method protocol
(add a second method only when a vertical slice needs it); the **Windows
`/implement` run** (reopens on access to a Windows machine — E2's
done-when named POSIX and Windows, and only POSIX has been recorded; the
integration tier does not run in CI, so this is a manual recorded run,
not a CI job).

## Prior work

Completed phases move here (or to `docs/superpowers/phase-history.md`)
when the roadmap outgrows the front page.

- **E1 — It installs and refuses.** `check` parses, validates, and
  path-lints a contract, refusing with a named cause and a stable exit
  code, zero model calls and zero processes. Spec:
  `docs/superpowers/specs/2026-08-16-e1-check-design.md`. Plan:
  `docs/superpowers/plans/2026-08-16-e1-check.md`.
- **E2 — The adapter reaches E1.** `/implement CONTRACT` reaches the
  same refusal through the TypeScript adapter (one process per operation,
  versioned JSON over stdin/stdout), verified live on POSIX. The Windows
  leg is deferred — see the Backlog. Spec:
  `docs/superpowers/specs/2026-08-16-e2-adapter-reaches-e1-design.md`.
  Plan: `docs/superpowers/plans/2026-08-16-e2-adapter-reaches-e1.md`.

## Workflow

This repository runs on spec-driven development — see
[`docs/sdd.md`](docs/sdd.md). Each feature cycle gets a committed design
spec, an implementation plan, then code. The default test suite needs no
model, network, or subprocess; process behavior lives in a small marked
integration tier that does not run in CI. The tier's first tests
(`tests/test_integration_protocol.py`, E2) start the engine as a
subprocess over the JSON protocol; run them explicitly with
`uv run pytest -m integration`.
