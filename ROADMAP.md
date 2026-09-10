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
structure are initialized. `BRIEF.md` is landed.

**Correction, recorded rather than edited away.** This section previously
said the guards (`packages/engine/engine.ts`, `tools/replay_guards.mjs`,
their replay fixtures) were copied in verbatim from `local-ai-pi` and were
not a phase. They were not copied — no TypeScript existed in this
repository. That work is now phase **E3.5** below: written fresh here, not
transplanted, using `local-ai-pi`'s guard and its replay fixtures as
reference. See `BRIEF.md`'s Provenance section for the same correction.

**Correction, 2026-08-20.** Commit `565e652` proves that the files named above
were in fact copied into this repository. The second sentence of the preceding
correction is therefore false, but its phase decision still stands: the copied
implementation is superseded and E3.5 replaces it with fresh loop-breaker code
proven against the retained evidence fixtures. The copied preserve-symbols
guard is removed; contract-aware mutation checks arrive in E4.

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

**Phase E3 — Delivery. Complete.** `deliver` captures the repository's exact
`HEAD`, runs one trusted command in a temporary detached worktree, and emits
one receipt. A successful changed tree also publishes a candidate commit under
`refs/satyrn/candidates/<id>/head`; every other handled result publishes none.
The caller's checkout, index, branch, and `HEAD` remain untouched. Spec and plan:
`docs/superpowers/specs/2026-08-18-e3-delivery-design.md`,
`docs/superpowers/plans/2026-08-18-e3-delivery.md`.

**Phase E3.5 — The loop breaker, written here. Complete.** The Pi package
keeps the last twenty admitted call keys and refuses a sixth exact repeat when
five matching calls remain in that window. State is local to one extension
registration; each block records `loop_broken`; the third consecutive block
also ends the current Pi turn. The shipped TypeScript was written fresh and all
six retained evidence fixtures replay against it in one process. Spec and plan:
`docs/superpowers/specs/2026-08-20-e3-5-loop-breaker-design.md`,
`docs/superpowers/plans/2026-08-20-e3-5-loop-breaker.md`.

**Phase E4 — One bounded replacement. Complete.** The package conditionally
replaces Pi's `edit` tool when a parent supplies a versioned mutation context.
One exact replacement then runs Pi → TypeScript → Python; Python alone enforces
the contract's `writable_paths`, the captured SHA-256 revision, and unique
anchor cardinality. Unavailable/stale revisions, undeclared paths, and
missing/ambiguous anchors are named refusals that leave the file unchanged;
symlink targets are never followed. The contract-blind
preserve-symbols guard removed in E3.5 does not return. Spec and plan:
`docs/superpowers/specs/2026-08-20-e4-bounded-replacement-design.md`,
`docs/superpowers/plans/2026-08-20-e4-bounded-replacement.md`.

**Phase E5 — One real attempt. Complete.** `attempt` runs one explicit Pi
model with only `read` and E4's bounded `edit`; `/implement` wraps it in E3.
Artifacts are published only through pinned directories outside every
registered worktree and Git administrative directory. The adapter reports a
deadline only after E3 has torn down its child process group and worktree.
The recorded live run used Pi 0.84.1 and
`omlx/gemma-4-12B-it-MLX-8bit`, changed only `app.py`, published a candidate,
and left the caller checkout clean. Spec and plan:
`docs/superpowers/specs/2026-08-20-e5-real-attempt-design.md`,
`docs/superpowers/plans/2026-08-20-e5-real-attempt.md`.

**Phase E6 — Packaged. Not started; the current phase.** The same
`/implement` works outside either source checkout on POSIX and Windows.

**Measured from outside, 2026-09-06: the edit tool refuses the model's
calls, and the loop breaker cannot see it.** `satyrn-evals` ran a
preregistered three-arm probe (Baseline / Envelope / Engine) at `n=12` on
one repair cell against `25ca0be`. The result is a **null** — Baseline
7/12, Envelope 4/12, Engine 4/12 successful attempts — and the reason is
this repository's, not the harness's:

- **Five of twelve Engine cells timed out, and all five are edit-refusal
  loops.** `mutator.ts:107-127` sets `additionalProperties: false` on the
  **edit item**, whose permitted keys are `oldText` and `newText`. The
  model sends `path` inside the item *as well as* at the top level, where
  the schema requires it, and receives `Validation failed for tool
  "edit": edits.0: must not have additional properties`. It re-sends with
  a varied `newText` until the deadline. **973 refused calls across 6/12
  cells**; five of five timeouts carry ≥151 refusals, seven of seven
  non-timeouts carry ≤1. Neither Pi arm carries the message — it is our
  tool.
- **The loop breaker never fires on it.** `engine.ts:100-126` keys on
  `callKey` — an exact match on tool name plus input — so a varying
  `newText` never matches, and a cell burns 238 refusals while
  `consecutiveBlocks` stays at zero.

> **Correction, 2026-09-06, recorded rather than edited away.** The
> sentence above is true but not the reason. **A schema-validation failure
> is invisible to every extension hook**, so the breaker did not merely
> fail to match these calls — it never received them. In pi 0.84.4's
> `prepareToolCall`, `validateToolArguments` runs *before*
> `config.beforeToolCall`, and a throw is caught into
> `{kind:"immediate"}`; `executeToolCallsSequential` then builds the
> finalized result directly and **skips `finalizeExecutedToolCall`**, which
> is the only site that calls `config.afterToolCall`. So neither the
> `tool_call` nor the `tool_result` hook sees the call. The
> `tool_execution_start`/`_end` transcript events are still emitted, which
> is why 973 of them are countable offline while no guard observed one.
> Read it in
> `~/.volta/tools/image/packages/@earendil-works/pi-coding-agent/lib/node_modules/@earendil-works/pi-coding-agent/dist/bundle/chunks/chunk-OMWWHBTG.js`,
> functions `prepareToolCall`, `executeToolCallsSequential` and
> `finalizeExecutedToolCall`.
>
> **The design principle this yields, which is the durable lesson:**
> strictness expressed in a tool's JSON Schema is enforced by the runtime
> *outside* the engine's sight, so a model that trips it cannot be helped,
> counted, or stopped by any guard we write. Strictness that must be
> observable belongs in `parseEditInput`, where a refusal goes through
> `execute`, carries a named cause, and reaches both the model and the
> `isError` seam. `f5d1c62` moved the `path` rule across exactly that line.

Counts and the recompute:
`~/satyrn-smokes/2026-09-06-v13-143343/RESULT.md`. Evidence per cell:
`grep -c 'must not have additional properties' cell-*-engine/*/transcript.txt`.

**Two changes follow, and each is a phase needing its own design proposal
before code** (`CLAUDE.md`) — they are recorded here, not started:

1. **Accept the redundant `path`** on the edit item (or tolerate item
   extras), leaving the top-level contract unchanged. The acceptance test
   costs no inference and is already run: of **701** refused `args`
   objects recovered from those transcripts, **701/701** fail today for
   exactly `item extras ['path']`, **701/701** pass once item extras are
   tolerated, and the extra key equals the top-level `path` in
   **701/701** — so tolerating it changes no semantics. Refusal siblings
   still refused: missing `oldText`, empty `oldText`, empty `edits`,
   missing top-level `path`, a top-level extra.
2. **A refusal-keyed breaker — proposed, measured, and rejected
   2026-09-06. Do not build it.** The design was: count consecutive error
   results at the `isError` seam (`mutator.ts:325-330`), intervene at K,
   terminate at 2K. Two independent findings kill it. **It cannot see the
   failure it was for** — schema-validation refusals never reach a hook
   (correction above), so it would have observed zero of the 973.
   **And the refusals it *can* see do not discriminate.** Pooled over all
   24 retained Engine cells (V11c spike and V13), the longest run of
   hook-visible mutator refusals is **0 on every one of the 7 cells that
   failed** and 0–3 on the 17 that succeeded: refusals occur *only* in
   healthy cells recovering from a stale revision or a missing anchor. No
   K separates them, and any K ≤ 3 fires exclusively on known-good — the
   exact inverse of `BRIEF.md` rule 8. A third point, if one were needed:
   a `tool_result` handler cannot terminate a turn — `ToolResultEventResult`
   carries only `content`, `details`, `isError` and `usage`.
   Recompute the runs by scanning each Engine transcript for
   `tool_execution_end` events whose `result.details.satyrn === true` and
   `ok === false`. **What remains true** is the narrower lesson in the
   correction above: keep observable strictness in `parseEditInput`, not
   in the schema.

**A note on scope.** The null says nothing about whether this engine's
design beats bare Pi: five of its twelve cells never got to try. It is
not evidence for or against the mutator, the breaker or the handoff.

## Concept budget

*Every term below is a cost against a 5–10 h/wk volunteer's ability to hold
the design in mind. Checked and updated at the end of each cycle; a term
earns its place by naming something the design actually needs, not by being
convenient shorthand.*

Defined terms: **contract**, with E1's working terms; **adapter** and
**protocol** (E2); **candidate**, **receipt**, and **worktree isolation**
(E3); **guard** (E3.5); **revision** (E4); and **attempt** (E5), in
`docs/glossary.md`.

## Phases

| # | Phase | Direction (one sentence) | Status |
|---|-------|--------------------------|--------|
| E1 | It installs and refuses | `check` parses, validates, path-lints, and refuses a contract with a named cause, zero model calls, zero processes started | **done** |
| E2 | The adapter reaches E1 | `/implement CONTRACT` reaches the same refusal through the TypeScript adapter, on POSIX and Windows — the architecture gate | **done** (POSIX recorded; Windows leg deferred, see Backlog) |
| E3 | Delivery | `deliver` runs a trivial executable in an isolated worktree, always emits a receipt, and publishes a candidate ref only for a successful changed tree | **done** |
| E3.5 | The guards, written here | The loop-breaker guard refuses a sixth exact repeat, records every block, and ends a turn on its third consecutive block; it is implemented fresh here and proven against replay fixtures | **done** |
| E4 | One bounded replacement | A single file replacement runs Pi → TypeScript → Python with revision checking | **done** |
| E5 | One real attempt | `attempt` and `/implement` complete one named task end to end from a source checkout | **done** |
| E6 | Packaged | The same `/implement` works outside either source checkout, on POSIX and Windows | **current** |
| HP3 | Chained isolation | Phase N of a multi-request workload runs in a worktree branched from phase N-1's **accepted commit**, not `HEAD`, so committed code folds forward through the checkout while context folds forward through the packet; a refused phase stops the chain with no candidate ref and no partial chain | **implemented, awaiting acceptance** (`7221982`, `d7946c3`); externally reachable as of 2026-09-10 (`deliver --base`, `337` default-tier tests, `99` integration) — **still not yet re-earned by this repository's own acceptance review**, and `satyrn-evals` has not yet wired its route to call it — see "Composing HP3 into `satyrn-evals`" below |

Done-when criteria are restated in each phase's plan — for E1, the Goal
of `docs/superpowers/plans/2026-08-16-e1-check.md` — not in this file,
to avoid drift between two copies.

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

**When HP3 starts, this repository's planning surface needs the caps regime
`satyrn-evals` adopted on 2026-09-02** — a backlog file and a document-length
checker. The work exists, unmerged, on branch `research/facts-field-backlog`
(commit `c7af592`), but that branch predates the 2026-09-07 reset in
`satyrn-evals`, so derive the regime from the current post-reset shape rather
than merging the stale branch. That branch also carries the recorded
deferral of a `facts` field in the handoff contract (`7b847eb`), which Phase
HP's packet now specifies; read it before re-deciding anything about facts.

### Composing HP3 into `satyrn-evals` — the concrete gap, named 2026-09-10

`satyrn-evals`' HP7 (live route proof) is blocked on this, by explicit
maintainer decision: it will not run without chained isolation, rather than
running a narrower operability-only proof
(`satyrn-evals/docs/current/hp7-live-route-proof-pre-run-record.md`). HP7's
other blocking gap — the implementer having no way to run its own declared
test command — is already closed on that side (`command_implementer` now
runs it and retains the outcome, evidence only, never gating). This one is
what remains, and it lives here, per the ownership split above.

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

**The contract and the packet do not carry the same fields.** `Contract`
(`src/satyrn_engine/contract.py:23-29`) is `id`, `task`, `writable_paths`,
`test_command` — a YAML file per phase. `satyrn-evals`' `HandoffPacket`
carries `objective`, `facts`, `preserve`, `self_test_command`,
`writable_paths`, `redacts`, budgets and a role. `writable_paths` maps
directly; `self_test_command` maps to `test_command`; `objective`/`facts`/
`preserve` have no structured home here and would need to fold into
`task`'s rendered text, the way `satyrn-evals.packet.render_projection`
already folds them into one prompt string for its own implementer. `redacts`
and the two budgets have no contract field at all and are not this
repository's concern to carry.

**`Contract.test_command` is itself declared and never applied here** —
`delivery.py` never reads it (verified: no reference to `test_command`
anywhere in that module). This is the same "declared, not applied" shape
`satyrn-evals`' `self_test_command` had before its 2026-09-10 fix, now
visible on this side of the same field. Not blocking HP3 composition, since
`satyrn-evals` already runs its own self-test after the implementer's turn
regardless of what this repository does with the field — named here so it
is not later mistaken for coverage this repository already provides.

**What HP3 acceptance and composition need, concretely, stated so a cycle
can be sized against it rather than left open-ended:**

1. ~~An external way to drive `deliver_chain`~~ **Done 2026-09-10** —
   `deliver --base`, above.
2. A documented mapping from a rendered task description (whatever
   `satyrn-evals` sends as `task`) plus `writable_paths`/`test_command` to
   one `Contract` per phase — this repository's format, `satyrn-evals`'
   responsibility to render into it, matching the ownership split.
3. HP3's own Astra-style acceptance review in this repository, independent
   of implementation — not yet done (see the Phases table above).

None of this is attempted here. It is named so the next cycle in this
repository has a stated target instead of a status line reading "proposed"
against work that already exists.

## Backlog

Moved to [`BACKLOG.md`](BACKLOG.md) on 2026-09-09, verbatim. Tangents go
there rather than into the current phase, and each entry carries the
condition that reopens it.

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
- **E3 — Delivery.** One synchronous trusted command runs in a detached
  worktree at the captured base commit. Every handled result is a stable
  receipt; a successful changed tree also becomes one create-once candidate
  ref.
  Spec: `docs/superpowers/specs/2026-08-18-e3-delivery-design.md`. Plan:
  `docs/superpowers/plans/2026-08-18-e3-delivery.md`.
- **E3.5 — The loop breaker, written here.** One registration-local
  TypeScript guard refuses repeated identical tool calls, records each
  refusal, and ends a Pi turn after the third consecutive refusal. Six retained
  evidence fixtures replay through the shipped package.
  Spec: `docs/superpowers/specs/2026-08-20-e3-5-loop-breaker-design.md`.
  Plan: `docs/superpowers/plans/2026-08-20-e3-5-loop-breaker.md`.
- **E4 — One bounded replacement.** One conditional Pi `edit` override sends
  one exact replacement over the one-shot protocol. Python checks writable
  path, prior revision, and anchor count before atomically replacing the file.
  Spec: `docs/superpowers/specs/2026-08-20-e4-bounded-replacement-design.md`.
  Plan: `docs/superpowers/plans/2026-08-20-e4-bounded-replacement.md`.
- **E5 — One real attempt.** One explicit Pi model receives the contract task
  and writable-file list, reads normally, and writes only through E4 inside
  E3's detached worktree. The transcript is preserved separately from E3's
  receipt and candidate.
  Spec: `docs/superpowers/specs/2026-08-20-e5-real-attempt-design.md`.
  Plan: `docs/superpowers/plans/2026-08-20-e5-real-attempt.md`.

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
`tests/test_integration_attempt.py`.
Run the tier explicitly with `uv run pytest -m integration`.
