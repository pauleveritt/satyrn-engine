# Roadmap

> **Planning surface, not the front door.** Where the current phase, the
> concept budget, deferred candidates, and the backlog live. Not where a
> new contributor should start — see
> [`README.md`](README.md) for what's usable now.

*Phases group feature cycles. One direction at a time. Tangents go to the
Backlog, not into the current phase.*

## Now

**Repository scaffolding — complete.** The repository is initialized with
the toolchain (`uv`, `ruff`, `pyrefly`, `pytest`), the docs stack (Sphinx,
MyST, Furo, sphinx-autobuild behind a Justfile recipe), CI for Pages, and
the superpowers structure. The roadmap itself — phases of feature cycles
derived from the two-repo rewrite brief — is **not yet written**; the
brief names the engine's slices (check, adapter proof, delivery, bounded
replacement, real attempt, packaging) and this section will record them in
named phases once the roadmap is authored.

## Concept budget

*Every term below is a cost against a 5–10 h/wk volunteer's ability to hold
the design in mind. Checked and updated at the end of each cycle; a term
earns its place by naming something the design actually needs, not by being
convenient shorthand.*

No terms yet. The concept budget starts empty and is earned.

## Phases

| # | Phase | Direction (one sentence) | Status |
|---|-------|--------------------------|--------|
| — | *(pending — roadmap not yet authored)* | The engine brief's slices: check, adapter proof, delivery, bounded replacement, real attempt, packaging | not started |

## Backlog

Deferred ideas land here, never into the current phase. Nothing is
scheduled yet.

## Prior work

Completed phases move here (or to `docs/superpowers/phase-history.md`)
when the roadmap outgrows the front page. Nothing here yet.

## Workflow

This repository runs on spec-driven development — see
[`docs/sdd.md`](docs/sdd.md). Each feature cycle gets a committed design
spec, an implementation plan, then code. The default test suite needs no
model, network, or subprocess; process behavior lives in a small marked
integration tier.
