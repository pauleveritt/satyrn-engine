# V5 design proposal — a real whole-attempt turn and wall-clock budget

**Status: confirmed 2026-09-11.** `CLAUDE.md`'s gate is satisfied; the two
attachments below are now decided, and a V5 plan follows this proposal. No code
until that plan is written.

**Narrow issue.** A delivered candidate has no whole-attempt spend bound that is engine-owned. `turn_budget` / `tool_call_budget` are packet declarations (route-side), and `deliver --timeout` bounds one command, not the attempt. V5 adds an engine-owned whole-attempt turn limit and wall-clock deadline that retain partial work on exhaustion.

**Motivating evidence (exploratory, from the corrected register).** Phase-4 turn cost: Engine 23, 23 against Baseline's 8, 6 in the screen. This is the one register row tied to the metric TE measured. A budget is a stop rule, not a remedy: V5 makes exhaustion a retained, honest outcome; it does not explain the cost.

## Three things named up front

1. **The `route.run_phases` validation-stop gap.** The composed route stops on a `FAILED` validation only at the grader, not in `run_phases` itself. V5 must not silently depend on the grader to halt an over-budget or failing phase; name whether V5's exhaustion stop is engine-side (`deliver`/`deliver_chain`) or route-side, and make the two agree or record the divergence.
2. **V4 validates the model against the model's own tests.** The contract's `test_command` runs the packet's self-test command, and on this task family those tests are model-authored and model-edited. A weakened or deleted test passes. V4 is "the candidate agrees with itself," not "the candidate is correct." V5 records its budget outcome independently of that suite.
3. **A pre-declared reading for phase-4 budget exhaustion.** The recurring redirect-trap failure and the phase-4 destructive edit are the two concrete candidates explaining the 23-vs-6/8 cost; both remain unclassified. **Decided at confirmation, 2026-09-11: a phase-4 exhaustion is an ordinary failed repair — a counted observation, not an infrastructure stop.** Infrastructure stops are reserved for the harness failing, not for the model spending. V6's pre-run record must state this, and must also state that **a passed validation on an exhausted attempt is not a completion**: exhaustion runs V4's validation on the partial candidate, whose tests are model-authored, so a passing suite there establishes only that the partial candidate agrees with itself.

## Shape

**CLI surface.** `deliver` gains `--turn-limit N` and `--deadline-seconds S` (or reads them from the contract's existing `turn_budget`/`tool_call_budget` if those are the engine's to own — resolve at confirmation). No new subcommand.

**Exit codes.** A new `ExitCode.BUDGET_EXHAUSTED`, distinct from `COMMAND_TIMEOUT` (one command) and `TESTS_FAILED` (validation). Exhaustion is **produced-but-partial**, not a refusal: the partial candidate and its evidence are retained, exactly as `TESTS_FAILED` retains a failing candidate.

**Data shape.** `DeliveryPayload` gains `budget: {turns_used, turn_limit, seconds_used, deadline_seconds, exhausted: bool}`. `None` never means both "no budget declared" and "budget not reached"; a declared-but-unenforced budget is its own state, mirroring V4's `not_requested`/`unavailable` split.

**Enforcement.** The whole-attempt turn count is engine-owned, not read from model prose; the deadline spans the attempt, not one command. On exhaustion the engine stops, commits/retains what exists, validates it if a `test_command` is declared (V4), and reports `BUDGET_EXHAUSTED` with the partial artifact. When a deadline is declared beyond the command timeout, the effective command deadline is `max(timeout, deadline_seconds)` so the budget deadline can fire and retain the partial candidate instead of the command timeout discarding it (recorded at final review, 2026-09-11).

**Test layout.** Default tier drives the clock and turn counter through the existing single seam (no second plugin mechanism); refusal/success siblings for turn-exhaustion, deadline-exhaustion, within-budget, and no-budget-declared; integration tests for a real deadline and a real partial retain. The three decisive cases: turns exceeded with a passing suite; deadline exceeded with a partial patch; a model that under-reports its own turns.

**Not in V5.** Automatic repair, completion-rate improvement, redirect-trap classification, the `route.run_phases` validation-stop itself (named, sequenced separately), V6.

## Sequencing

V3b (evaluator) is done, so the completion figures V6 will be read against are now derived. V5 is engine-side and independent; V5 and V6 both need the evaluator design's 400-line cap resolved first, since the cross-repo revision recording lives in that file.
