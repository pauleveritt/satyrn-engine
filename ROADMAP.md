# Roadmap

> **Planning surface, not the front door.** Where the current phase, the
> concept budget, deferred candidates, and the backlog live. Not where a
> new contributor should start — see
> [`README.md`](README.md) for what's usable now.

*Phases group feature cycles. One direction at a time. Tangents go to the
Backlog, not into the current phase.*

## Now

A pointer, not a narrative — detail lives in each phase's own row, its
linked specs, and `docs/superpowers/phase-history.md`. Update this list
when what's in flight changes; do not grow it back into prose.

- **Closed and archived:** Phase E (E1–E10, the walking skeleton and its
  evidence-driven refinements) — closed 2026-09-11. Record:
  [`docs/superpowers/phase-history.md`](docs/superpowers/phase-history.md).
- **Accepted, standing:** HP3 (chained isolation) — see the HP3 row and
  its own section below. `satyrn-evals`' own HP7 live-route proof still
  needs its own spending authorization there, not owned by this repository.
- **Track A closed, Track B proposed:** Phase V — verified, bounded delivery.
  `satyrn-evals`' Track A (V1–V3) is closed (`satyrn-evals@2d20aac`) and
  published its Track B gate and an exploratory engine gap register; this
  repository owns Track B (V4–V6), which needs a design proposal per
  `CLAUDE.md`'s gate before any code.

## Concept budget

*Every term below is a cost against a 5–10 h/wk volunteer's ability to hold
the design in mind. Checked and updated at the end of each cycle; a term
earns its place by naming something the design actually needs, not by being
convenient shorthand.*

Defined terms: **contract**, with E1's working terms; **adapter** and
**protocol** (E2); **candidate**, **receipt**, and **worktree isolation**
(E3); **guard** (E3.5); **revision** (E4); and **attempt** (E5), in
`docs/glossary.md`. Phase V has not yet earned any new term — none is
defined until a cycle's design actually needs it.

## Phases

| # | Phase | Direction (one sentence) | Status |
|---|-------|--------------------------|--------|
| E | The walking skeleton, then evidence-driven refinement (E1–E10) | Contract to refused/delivered candidate, through a real model attempt, packaged | **closed 2026-09-11** — archived in [`docs/superpowers/phase-history.md`](docs/superpowers/phase-history.md); E6 (packaging outside a checkout) was never separately verified and is carried forward as backlog, not resumed as E work |
| HP3 | Chained isolation | Phase N of a multi-request workload runs in a worktree branched from phase N-1's **accepted commit**, not `HEAD`, so committed code folds forward through the checkout while context folds forward through the packet; a refused phase stops the chain with no candidate ref and no partial chain | **accepted 2026-09-10** (`7221982`, `d7946c3`, `c0f4801`) — independent review confirmed the fold-forward property both in-process and through two real `deliver --base` subprocess calls, no-partial-chain both offline and against real Git, `d7946c3`'s ref-survival fix genuine, `--base` wiring correct end to end, scope discipline held (only `delivery.py`/`cli.py` touched). `337` default-tier + `99` integration tests. **`satyrn-evals` has now wired its own packet route to call it and independently accepted that composition, same day** (`satyrn-evals@9115608`..`141f3bb`) — see "Composing HP3 into `satyrn-evals`" below |
| V | Verified, bounded delivery — **Track A closed; Track B proposed** | A model attempt returns a candidate whose validation status is authoritative (not the model's own prose) and cannot spend past a declared turn/deadline budget without retaining evidence — then one bounded live proof | **Track A closed 2026-09-11 in `satyrn-evals`** — design `docs/current/phase-v-design.md` at revision `2d20aac`, superseding this sketch. **Track A (analysis, closed):** V1 claim inventory + per-phase ledger; V2 claim-level measures + census/pathology repair; V3 close-out + exploratory engine gap register. **Track B (this repository, proposed):** V4 make `Contract.test_command`'s result authoritative on `AttemptResult`, independent of model-generated text; V5 a real whole-attempt turn limit and wall-clock deadline, retaining partial work on exhaustion; V6 one separately authorized live proof, `n` frozen at 1. Track B starts only after Track A's gate, which is published; the register names the candidates Track B acts on. **V4 propagation, 2026-09-11:** V4's propagation landed in `satyrn-evals`' composed packet route, which now reads the engine's authoritative `validation` verdict rather than the adapter's own self-test as sole authority (`uncommitted working tree, hash to be recorded at commit`). A gated follow-on (one bounded repair handoff, at most one fresh implementer retry with the real public failure) is named, not started, and waits for V4–V6 to be accepted first |

Done-when criteria are restated in each phase's plan — for E1, the Goal
of `docs/superpowers/plans/2026-08-16-e1-check.md` — not in this file,
to avoid drift between two copies. Phase V has no plan yet in this
repository; Track B's first cycle (V4) is a design proposal away from one,
and Track A's plan lives in `satyrn-evals`.

### HP3, and why a cycle from another repository appears here

`satyrn-evals` opened **Phase HP**, the handoff packet: an orchestrator
carries an existing multi-request workload through bounded implementer
handoffs. Ownership was settled deliberately and along the line `BRIEF.md`
already draws. **This repository owns packet execution, chained isolation and
candidate production**, because it already owns the contract seam and because
contract authoring is a main-agent skill and not engine machinery
(`BRIEF.md:15,29`). `satyrn-evals` owns the packet schema, the arm, capture,
grading, attribution and comparison, and does not import engine internals.

HP3 is the only cycle of that phase that lands here. It is listed in this
table because a cycle nobody's roadmap owns is a cycle nobody schedules, and
because **its acceptance has to be re-earned in this repository** rather than
asserted from the other one.

**The reference implementation already exists and is not ours.** SwiftStar's
`WorktreeTransaction` states the property in its own docstring: each phase
runs in a disposable worktree branched from the prior phase's commit rather
than `HEAD`, so "the code folds forward through the checkout, while context
folds forward through the packet", and on a phase receipt the transaction
stops with no partial chain
(`swiftstar/Sources/SwiftStarAppKit/WorktreeTransaction.swift:4-12`). Borrow
the behaviour; re-earn it here against this repository's own fixtures, as
`BRIEF.md`'s provenance rule requires of everything taken from a prior
project.

**Entry condition.** HP1, the packet schema, and HP2, the offline route, are
both **complete and committed** in `satyrn-evals`
(`docs/superpowers/plans/2026-09-09-hp1-handoff-packet.md` and
`2026-09-09-hp2-offline-route.md`). HP2 already holds the no-partial-chain
rule in its route so the two cycles cannot disagree; HP3 replaces its plain
workspace with real chained worktrees.

**Not in HP3:** a worker pool, parallel dispatch, automatic retry or repair,
generalized routing, or any orchestrator. Autonomous packet authoring is out
of scope for the whole phase — a system authoring and gating contract content
on its own is measured at 3/8 against 8/8 by hand, with a remediated
authoring prompt collapsing to 0/8 all no-op (`local-ai-pi/ROADMAP.md:386-402`).

### Composing HP3 into `satyrn-evals` — closed 2026-09-10

`satyrn-evals`' HP7 (live route proof) was blocked on this, by explicit
maintainer decision, rather than a narrower operability-only proof
(`satyrn-evals/docs/current/hp7-live-route-proof-pre-run-record.md`). Both
disclosed blocking gaps are now closed: the implementer running its own
declared test command, and this one — chained isolation is composed into
`satyrn-evals`' packet route via `engine_command_implementer` (drives real
`deliver --base` per phase) and `run_and_record_engine_chain` (git-backed
retention, since the isolated worktree each phase ran in is already gone by
the time retention runs). Independently accepted there 2026-09-10
(`satyrn-evals@9115608`..`141f3bb`): no import of this repository's
internals, fold-forward proven via a real two-subprocess/real-Git test,
`orchestrator_mutations` structurally `()` never `None`, and one disclosed
gap (no per-phase persistence, unlike HP6's `run_and_record_chain`)
**closed the same day** (`satyrn-evals@b8ca71a`). HP7 itself still needs a
real Pi process and spending authorization, neither granted yet.

**`deliver_chain` had no external interface — resolved 2026-09-10.**
`deliver`'s CLI now takes `--base COMMIT_ISH` (`src/satyrn_engine/cli.py`,
`_nonblank_base`), threading straight through to `deliver(..., base=...)`.
This completes the HP3 design doc's own intent
(`docs/superpowers/specs/2026-09-09-hp3-chained-isolation-design.md`
§2-3: "the fix is a parameter and a loop rather than a new subsystem";
"No new subcommand") rather than reopening it — the parameter existed on
`deliver()` already, only the CLI flag exposing it to an external caller
was missing. Proven against real Git, through the real CLI subprocess, not
`deliver_chain`'s own in-process loop: two real `satyrn-engine deliver`
calls, the second's `--base` set to the first's real `candidate_commit`,
phase 2's tree checked to contain both phases' files
(`tests/test_integration_delivery.py::test_base_composes_two_real_cli_deliveries_into_one_fold_forward`).
Verified the test actually catches a regression, not just an addition:
temporarily forced `base=None` in `main()`, watched this exact test fail,
then restored the fix. `satyrn-evals` can now drive a chain as an external
process, respecting its own "does not import engine internals" line.

**The contract and the packet did not carry the same fields — resolved via
folding, not a schema change.** `Contract` (`src/satyrn_engine/contract.py:
23-29`) is `id`, `task`, `writable_paths`, `test_command`; `HandoffPacket`
carries more (`objective`, `facts`, `preserve`, `redacts`, budgets, a role).
`writable_paths` maps directly, `self_test_command` maps to `test_command`,
and the rest folds into `task`'s rendered text via `render_packet` — the
same text `satyrn-evals`' own implementer already reads, not a second
rendering. `redacts` and the budgets have no contract field; **Phase V's V3
is where that changes** — a real turn/deadline budget becomes this
repository's own concern rather than folded text `delivery.py` cannot read.

**What HP3 acceptance and composition needed, concretely — all three done
2026-09-10:** an external way to drive `deliver_chain` (`deliver --base`,
above); a documented mapping from a rendered task description to one
`Contract` per phase (`satyrn-evals.packet.contract_yaml`); HP3's own
Astra-style acceptance review (Phases table above). What remains is outside
this repository: `satyrn-evals`' HP7 live route proof still needs a real Pi
process and separate spending authorization before it can run.

## Backlog

Moved to [`BACKLOG.md`](BACKLOG.md) on 2026-09-09, verbatim. Tangents go
there rather than into the current phase, and each entry carries the
condition that reopens it. E6 (packaging outside a source checkout) belongs
there now too, following Phase E's close above.

## Prior work

**E1–E10, in full, including the null result that redirected the program and
the two follow-on findings it produced:**
[`docs/superpowers/phase-history.md`](docs/superpowers/phase-history.md).
Nothing from that record was deleted in this file's 2026-09-11 cleanup —
only moved, because `ROADMAP.md` had reached its own 400-line cap
(`tools/lint_docs.py`, `FILE_CAPS["ROADMAP.md"]`) and a correction is
recorded, not edited away, which requires room to keep recording in.

## Workflow

This repository runs on spec-driven development — see
[`docs/sdd.md`](docs/sdd.md). Each feature cycle gets a committed design
spec, an implementation plan, then code. The default test suite needs no
model, network, or subprocess; process behavior lives in a small marked
integration tier that does not run in CI. The tier's first tests
(`tests/test_integration_protocol.py`, E2/E4) start the engine as a subprocess
over the JSON protocol; E3 adds real local Git and process-group evidence in
`tests/test_integration_delivery.py`; E3.5 adds Node replay and a temporary Pi
package install in `tests/test_integration_guards.py`; E4 adds the real
TypeScript-to-Python replacement path in `tests/test_integration_mutator.py`;
E5 runs that shipped path inside a real Git attempt and E3 delivery in
`tests/test_integration_attempt.py`; E7 adds the model-invocable test runner
in `tests/test_integration_runner.py` and `tests/test_integration_runner_tool.py`.
Run the tier explicitly with `uv run pytest -m integration`.
