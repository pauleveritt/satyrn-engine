# Phase history: the E-series (closed)

**Closed 2026-09-11.** This is the archived record of Phase E — the
walking-skeleton build-out from a contract file to a delivered, model-attempted
candidate. `ROADMAP.md`'s own text named this file as the overflow destination
once the front page outgrew its cap; this is that move, following the same
close-and-archive pattern `ds4-engine` used for its own E-series
(`ds4-engine/EXPERIMENTAL_ROADMAP.md`). Read `ROADMAP.md`'s **Now** section for
the active phase; nothing here is live work.

## Why E closed here, not later

E1 through E6 built the walking skeleton: parse and refuse a contract, reach it
through the Pi adapter, deliver a candidate in an isolated worktree, guard
against loops, bound one mutation to a checked revision, and run one real
model attempt. E7 through E10 were evidence-driven refinements on that same
skeleton, discovered by measuring it in use rather than by extending its
original design. That arc is complete: the skeleton exists, was measured
against a real baseline, and every finding from that measurement is either
shipped or explicitly recorded as rejected below.

What comes next — trustworthy verification and a real spending budget on a
model attempt — is motivated by a different measurement, run by a different
project (`satyrn-evals`' Phase TE) against the *whole* system in extended use,
not by dogfooding this repository's own mechanics. That is a new direction,
not a continuation of E's, so it gets its own phase letter rather than an E11.

## Phases E1–E6: the walking skeleton

- **E1 — It installs and refuses.** `check` parses, validates, and path-lints
  a contract, refusing with a named cause and a stable exit code, zero model
  calls and zero processes. Spec:
  `docs/superpowers/specs/2026-08-16-e1-check-design.md`. Plan:
  `docs/superpowers/plans/2026-08-16-e1-check.md`.
- **E2 — The adapter reaches E1.** `/implement CONTRACT` reaches the same
  refusal through the TypeScript adapter (one process per operation,
  versioned JSON over stdin/stdout), verified live on POSIX. The Windows leg
  is deferred — see `BACKLOG.md`. Spec:
  `docs/superpowers/specs/2026-08-16-e2-adapter-reaches-e1-design.md`. Plan:
  `docs/superpowers/plans/2026-08-16-e2-adapter-reaches-e1.md`.
- **E3 — Delivery.** One synchronous trusted command runs in a detached
  worktree at the captured base commit. Every handled result is a stable
  receipt; a successful changed tree also becomes one create-once candidate
  ref. Spec: `docs/superpowers/specs/2026-08-18-e3-delivery-design.md`. Plan:
  `docs/superpowers/plans/2026-08-18-e3-delivery.md`.
- **E3.5 — The loop breaker, written here.** One registration-local
  TypeScript guard refuses repeated identical tool calls, records each
  refusal, and ends a Pi turn after the third consecutive refusal. Six
  retained evidence fixtures replay through the shipped package. Spec:
  `docs/superpowers/specs/2026-08-20-e3-5-loop-breaker-design.md`. Plan:
  `docs/superpowers/plans/2026-08-20-e3-5-loop-breaker.md`.
- **E4 — One bounded replacement.** One conditional Pi `edit` override sends
  one exact replacement over the one-shot protocol. Python checks writable
  path, prior revision, and anchor count before atomically replacing the
  file. Spec:
  `docs/superpowers/specs/2026-08-20-e4-bounded-replacement-design.md`. Plan:
  `docs/superpowers/plans/2026-08-20-e4-bounded-replacement.md`.
- **E5 — One real attempt.** One explicit Pi model receives the contract task
  and writable-file list, reads normally, and writes only through E4 inside
  E3's detached worktree. The transcript is preserved separately from E3's
  receipt and candidate. Spec:
  `docs/superpowers/specs/2026-08-20-e5-real-attempt-design.md`. Plan:
  `docs/superpowers/plans/2026-08-20-e5-real-attempt.md`.
- **E6 — Packaged.** The same `/implement` works outside either source
  checkout, on POSIX and Windows. **Status at close: not separately verified
  as its own cycle** — no dedicated spec/plan landed under this name before
  the program moved to evidence-driven refinement (E7 onward). Recorded here
  rather than silently dropped; packaging-outside-a-checkout is unverified
  and, if still wanted, belongs in the new phase's backlog, not reopened as
  unfinished E work.

## The null result that redirected the program

**Measured from outside, 2026-09-06.** `satyrn-evals` ran a preregistered
three-arm probe (Baseline / Envelope / Engine) at `n=12` on one repair cell
against `25ca0be`. The result was a **null** — Baseline 7/12, Envelope 4/12,
Engine 4/12 — and the reason was this repository's, not the harness's:

- **Five of twelve Engine cells timed out, and all five were edit-refusal
  loops.** `mutator.ts:107-127` set `additionalProperties: false` on the edit
  item; the model sent `path` inside the item as well as at the top level and
  was refused, then re-sent with a varied `newText` until the deadline. 973
  refused calls across 6/12 cells; five of five timeouts carried ≥151
  refusals, seven of seven non-timeouts carried ≤1.
- **The loop breaker never fired on it**, because a schema-validation
  failure is invisible to every extension hook in pi 0.84.4 —
  `validateToolArguments` runs before `config.beforeToolCall` and a throw
  skips `finalizeExecutedToolCall` entirely, so neither `tool_call` nor
  `tool_result` ever reaches a guard. Read in
  `~/.volta/tools/image/packages/@earendil-works/pi-coding-agent/lib/node_modules/@earendil-works/pi-coding-agent/dist/bundle/chunks/chunk-OMWWHBTG.js`,
  functions `prepareToolCall`, `executeToolCallsSequential`,
  `finalizeExecutedToolCall`.

**The durable lesson**: strictness expressed in a tool's JSON Schema is
enforced by the runtime *outside* the engine's sight, so a model that trips it
cannot be helped, counted, or stopped by any guard written here. Strictness
that must be observable belongs in the tool's own `execute` path, where a
refusal carries a named cause and reaches both the model and the `isError`
seam. Counts and the recompute: `~/satyrn-smokes/2026-09-06-v13-143343/RESULT.md`.

**A note on scope, preserved as written.** The null says nothing about
whether this engine's design beats bare Pi: five of its twelve cells never
got to try. It is not evidence for or against the mutator, the breaker, or
the handoff.

**Two changes were proposed from this finding — both now resolved.**

1. **Accept the redundant `path`** on the edit item, leaving the top-level
   contract unchanged. **Done** — `f5d1c62`, "Tolerate a redundant edit-item
   path, refuse a contradicting one," 2026-09-06. Of 701 refused `args`
   objects recovered from those transcripts, 701/701 failed for exactly
   `item extras ['path']`, 701/701 passed once tolerated, and the extra key
   equalled the top-level `path` in 701/701 — tolerating it changed no
   semantics. Refusal siblings stayed refused: missing `oldText`, empty
   `oldText`, empty `edits`, missing top-level `path`, a top-level extra.
2. **A refusal-keyed breaker.** **Measured and rejected, not built** —
   `b977941`, "Record why the breaker was blind, and why not to build its
   sibling," 2026-09-06. It cannot see the failure it was for (schema
   refusals never reach a hook, per the durable lesson above — it would have
   observed zero of the 973). And the refusals it *can* see do not
   discriminate: across 24 retained Engine cells, the longest run of
   hook-visible mutator refusals was 0 on every one of the 7 cells that
   failed and 0–3 on the 17 that succeeded — no threshold separates them,
   and any threshold ≤ 3 fires exclusively on known-good work.

## Phases E7–E10: evidence-driven refinement

- **E7 — a model-invocable test runner, off unless the contract asks for
  it.** `bc0434a`, 2026-09-06. `satyrn-evals` V13d found that a restricted
  `read,edit` surface cost 8 of 12 successes against Baseline's
  `read,bash,edit,write` on one repair task (one-sided Fisher p = 0.00067) —
  Baseline's `bash` calls were overwhelmingly `pytest`, run to read the
  failure and then edit. The `bash` tool (renamed from `run_tests` in
  `8f1deb3` once smoke testing showed the model's prior expects a tool that
  takes a `command` argument) restores exactly that one capability: it
  compares the model's supplied command string against the contract's own
  `test_command` (`command_matches`, `runner.py:105`) and, only on a match,
  runs the contract's own argv — never the model's string. A failing suite
  is a result (`RunnerCode.OK`, `result.exit_code` set), not a refusal; only
  a command that cannot start at all is refused. Corrected 2026-09-09
  (`a20caaa`): pi's own system-prompt builder told the model to use `bash`
  for exploration whenever `bash` was selected, contradicting the runner's
  restriction; fixed with an appended (not replacing) system-prompt
  correction. Spec:
  `docs/superpowers/specs/2026-09-06-e7-model-invocable-test-runner-design.md`.
  **What this closed and what it left open**: it gave the model a voluntary
  path to run its own declared test command. It did not make that result
  authoritative anywhere the engine's own caller could see — `run_tests`'
  result reaches only the model's own conversation, never `AttemptResult`.
  That gap is the new phase's starting point.
- **E8 — proposed, then refused by its own acceptance test.** `54ee379`
  proposed an `ANCHOR_MISSING` refusal that names what text is actually
  present when a replacement's anchor cannot be found. `80f1c46` (2026-09-07)
  found the acceptance test itself invalid; `6710075` traced the real
  pathology and closed the phase **not built**, with the reasoning kept
  rather than deleted. Spec (recording the rejected proposal):
  `docs/superpowers/specs/2026-09-07-e8-anchor-hint-design.md`.
- **E9 — tell the model what the file says, not what its hash is.**
  `c6054ee`, 2026-09-07. Across 499 retained transcripts, 2,872 edit calls
  failed; 66% issued an anchor 10+ tool calls after the model's last read of
  that file, and in 17% of failures the file had changed because of the
  model's own earlier edit. The mutator's success message carried only a
  hash (`Replaced app.py; sha256=2e4fc064…`), which is not a textual view of
  anything. Three changes to the mutator's messages close the gap; the
  matching rule itself (one exact, unique anchor) is untouched. Spec:
  `docs/superpowers/specs/2026-09-07-e9-file-state-in-edit-results-design.md`.
- **E10 — create the transcript exclusively, and stream into it.**
  `8b52de9`, 2026-09-07. `satyrn-evals` runs a repeated-call spending rule
  that tails the transcript as it is written; that rule had never applied to
  this arm (`REPEAT_LIMIT` fired on 18/337 Baseline cells, 28/49 Envelope,
  0/147 Engine) because the transcript was spooled and only published after
  the attempt finished. The transcript file is now created exclusively
  before Pi starts and written into directly, so a live tail sees it as it
  happens. Spec:
  `docs/superpowers/specs/2026-09-07-e10-stream-the-transcript-design.md`.

## What carries forward, not lost

- **`Contract.test_command` is declared and never applied in `delivery.py`.**
  Named at HP3-composition time (`ROADMAP.md`, "Composing HP3," 2026-09-10)
  and directly motivates the new phase's first concrete step: E7 gave the
  model a voluntary path to run it; nothing makes the result authoritative.
- **No turn or wall-clock budget is enforced on a model attempt.**
  `attempt.py`'s `SubprocessPiRunner.run()` calls `process.wait()` with no
  timeout. `delivery.py` already proves the pattern to apply
  (`_run_and_commit`'s timeout, `_teardown_process_group`'s signal
  escalation) — just never pointed at the model attempt itself.
  A structurally different remedy for a related problem, the "progress
  rule" (an in-session TypeScript heuristic ending a turn after N
  refusals with nothing landed), was measured and **retired** (`e7b629d`,
  2026-09-09): its margin over the loop breaker was 4 cells net, it never
  fired on the breaker it shipped in, and its own counter recognized only
  Satyrn-mutator edits. A hard, unconditional turn/deadline cap is a
  different mechanism and does not reopen that decision.
- **E6 (packaging outside a source checkout, POSIX and Windows)** was never
  separately verified under its own cycle. If still wanted, it is backlog
  for the new phase, not resumed E work.
