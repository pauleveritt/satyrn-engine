# V4 — engine-owned final validation on the composed delivery route

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use `- [ ]`.

**Status: confirmed 2026-09-11.** `CLAUDE.md`'s gate is satisfied; implementation
authorized. The result-code decision is resolved above.

**Goal:** make the contract's own test result authoritative on the delivered candidate, independent of model text and of any model-invoked test call, and carry it into the delivery payload.

**Architecture:** change the engine's `deliver` route (`src/satyrn_engine/delivery.py`), not `attempt.py`. The measured packet route is `satyrn-engine deliver` with the Pi implementer. It already produces a candidate commit; V4 adds an engine-owned validation step against that exact commit and a structured outcome on the result.

**Spec:** `satyrn-evals` Phase V design (`docs/current/phase-v-design.md` at `satyrn-evals@310f9a3`), the exploratory engine gap register (`phase-v-engine-gap-register.md`), and this repository's `CLAUDE.md`.

## Why `attempt.py` is the wrong seam

`AttemptResult` (`attempt.py:78`) carries no contract-test verdict, and the accepted-attempt verdict is `AttemptResult(AttemptCode.OK, …, command_exit=0)` — derived from the **model process exit**. That is a parallel gap. But the measured route is `deliver`, and the satyrn-evals adapter already runs an *independent* self-test against the delivered candidate and returns "delivered" regardless (`satyrn-evals` `adapters/engine_delivery.py:138,157`). Fixing `attempt.py` would not change the route that produced the finding.

Also: `runner.run_tests` (`runner.py:124`) is **model-invoked**. Its existence guarantees no final test, and its last successful receipt may precede later edits.

## Global constraints

- Default tier: no model, no network, no subprocess; the planted process-spawn tripwire stays armed. Real Git lives in the marked integration tier.
- A refusal test has a sibling success test.
- A correction is recorded, not edited away.
- Product code does not import laboratory or later-phase scaffolding.
- V4 does not start until this design is confirmed.

## Data shape (the one interface later cycles read)

```python
class ValidationOutcome(StrEnum):
    """What the engine's own run of the contract's test command established."""

    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"        # declared, but not runnable
    NOT_REQUESTED = "not_requested"    # no test_command declared
    NOT_APPLICABLE = "not_applicable"  # no candidate was created

class DeliveryPayload(TypedDict):
    ...
    command_exit: int | None
    validation: ValidationOutcome      # NEW; never ambiguous None
    validation_exit: int | None        # NEW; the test command's own exit
    validation_output: str | None      # NEW; tail, bounded
```

`None` must never stand in for a missing verdict. `NOT_REQUESTED` means "no
`test_command` declared", `UNAVAILABLE` means "declared but not runnable", and
`NOT_APPLICABLE` means "no candidate was created, so there is nothing to
validate".

## Behaviour

1. After the candidate commit exists (`_run_and_commit` → `_publish`), run `contract.test_command` **verbatim, shell-free, in isolation against that exact commit** — not the model's command, not the model's transcript.
2. Record the outcome on the receipt and payload. Test failure is **produced-but-failing**, not a preflight refusal: the candidate and its evidence are retained.
3. Keep execution failure independent. `command_exit != 0` keeps its current code; passing validation must not upgrade a failed implementer process, and successful delivery must not imply passing tests.
4. A contract without `test_command` records `NOT_REQUESTED` (unchanged behaviour). A declared but unrunnable command records `UNAVAILABLE`.
5. No automatic repair, no retry, no prose inspection. V4 establishes a truthful boundary only; it does not improve completion, reduce turns, or detect fabricated prose.

## Tasks

### Task 1: the validation vocabulary and payload field

**Files:** `src/satyrn_engine/delivery.py`, `tests/test_delivery.py` (default tier)

- [ ] **Step 1:** failing tests asserting `ValidationOutcome` has exactly the six members and that `DeliveryPayload` requires `validation`.
- [ ] **Step 2:** add `ValidationOutcome` and the three payload keys; every existing payload constructor sets `validation=NOT_APPLICABLE` (no candidate yet) until Task 2 wires the real run, and a candidate-created receipt with no declared test records `NOT_REQUESTED`. Run `uv run pytest -q`.
- [ ] **Step 3:** commit.

### Task 2: engine-owned run against the candidate commit

**Files:** `src/satyrn_engine/delivery.py`, `tests/test_delivery.py`, `tests/test_integration_delivery.py`

- [ ] **Step 1:** default-tier tests drive a fake command-runner seam (the existing test seam — do not add a second plugin mechanism): `PASSED`, `FAILED`, `TIMED_OUT`, `UNAVAILABLE`, `NOT_REQUESTED`, each with its sibling in the other direction.
- [ ] **Step 2:** run the declared command against the candidate commit before cleanup, recording outcome/exit/output; a `FAILED` validation does not change `candidate_commit`/`changed_paths` and does not discard.
- [ ] **Step 3:** marked integration test: model exits successfully while tests fail; tests pass before a later breaking edit; model never invokes tests; timeout; unavailable; failure evidence survives cleanup.
- [ ] **Step 4:** `just gates`; commit.

### Task 3: the result code and exit

**Files:** `src/satyrn_engine/delivery.py`, `src/satyrn_engine/exits.py`, `tests/test_exit_codes.py`

- [ ] **Step 1:** failing tests for the chosen mapping (open decision below): a `TESTS_FAILED` `DeliveryCode` with `DeliveryOutcome.CANDIDATE_CREATED` and a distinct exit, OR `DeliveryCode.OK` with `validation=FAILED`. Whichever is chosen, the payload truth is the same; only the coarse status differs.
- [ ] **Step 2:** implement the mapping; keep `command_exit` semantics untouched.
- [ ] **Step 3:** commit.

### Task 4: propagate to the composed route

**Files:** `satyrn-evals` `src/satyrn_evals/adapters/engine_delivery.py` (separate repository, separate commit; record both revisions)

- [ ] **Step 1:** the adapter stops being the only validator: it reads `validation` from the engine payload and carries it into the packet result, instead of running its own self-test as the sole authority.
- [ ] **Step 2:** the existing "runs an independent self-test and returns delivered regardless" behaviour is reconciled: the engine's outcome becomes the recorded authority; the adapter's run is removed or becomes a cross-check, with the reason recorded.
- [ ] **Step 3:** two-way revision recording, per the satyrn-evals design's cross-repo rule: the forward leg records `satyrn-engine@fe63d8b` in `satyrn-evals`; the reverse leg records in this repository's `ROADMAP.md` Phase V row that V4's propagation landed in `satyrn-evals` (`uncommitted working tree, hash to be recorded at commit`).

## Resolved decision (confirmed 2026-09-11)

**`DeliveryCode.TESTS_FAILED`** → `DeliveryOutcome.CANDIDATE_CREATED` → a distinct
`ExitCode.TESTS_FAILED`, so no caller can read `OK` and infer passing tests. The
candidate is still created and retained; only the coarse status differs from a
clean pass. `validation` on the payload is the same truth either way.

## Self-review

**Scope:** engine-owned validation on the composed route (Tasks 1–3) and its propagation (Task 4). `attempt.py`, budgets (V5), live proof (V6), and automatic repair are explicitly out.

**Evidence:** the register's corrected rows motivate V4 (verification_claim 1 contradicted / 4 supported / 3 undecidable of 8; last model-invoked self-test 8 failed / 7 passed of 15). One retained candidate with independently failing tests is sufficient; no new measurement campaign is required.

**Honest limit:** passing the declared public suite does not establish hidden correctness or test preservation. V4 records a truthful validation boundary; it does not claim more.
