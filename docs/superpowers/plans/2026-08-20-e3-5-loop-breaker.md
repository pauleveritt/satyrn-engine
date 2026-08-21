# E3.5 — Loop Breaker Implementation Plan

> **For agentic workers:** implement the tasks below in order. Consult
> `local-ai-pi@8588ba4` for incidents and expected behavior only. Do not copy
> its TypeScript. The files currently in this repository came from the prior
> project and are the superseded input this phase replaces.

**Goal:** Ship one fresh, self-contained TypeScript loop breaker in the Pi
package. It refuses a sixth exact repeat while five matching admitted calls
remain in a twenty-call window, records `loop_broken`, and never shares state
between registrations or lets a guard failure escape the Pi turn.

**Architecture:** `packages/engine/engine.ts` contains one concrete policy and
one Pi adapter. `createLoopBreaker()` is both its construction and test seam;
there is no general guard framework. Node behavior tests and the replay harness
import that shipped file directly. Python only starts those tools in the marked
integration tier and does not participate in ordinary tool calls.

**Tech stack:** TypeScript executed by the installed Node runtime's type
stripping, Pi's `ExtensionAPI` type, Node built-ins, existing JSON evidence,
pytest for marked process integration, and the existing documentation stack.
No new product dependency.

**Spec:**
`docs/superpowers/specs/2026-08-20-e3-5-loop-breaker-design.md`

> **Correction — 2026-08-21:** Preserve own `__proto__` properties during
> canonicalization and cover distinct/exact top-level and nested inputs. Delete
> per-key blocked telemetry when window eviction removes that key's last
> admitted occurrence. Require `expected.firstBlock` in every retained fixture,
> with `null | positive integer` semantics and a missing-field refusal test.
> Extend the temporary Pi installation proof through manifest resolution,
> shipped extension loading, handler registration, and real handler dispatch.
> Record the final whitespace gate against the phase base with
> `git diff --check aa918b0 --`. These are corrections to the accepted E3.5
> design, not a new framework or phase.

## Phase-size decision

Keep E3.5 to the loop breaker. If implementation asks for a second mutation
guard, a generic guard pipeline, Python transport, a tool budget, a timeout, a
churn detector, or model-driven evidence, stop and defer it. E4 owns
contract-aware mutation; the other mechanisms need their own evidence.

---

### Task 1: Replace the copied policy with the fresh typed seam

**Files:**

- Rewrite: `packages/engine/engine.ts`
- Create: `tests/test_loop_breaker.mjs`

**Produces:**

- the exact 20/5 admitted-call policy;
- recursive stable JSON keys with ordered arrays;
- `JsonValue`, `ToolCall`, `LoopBrokenData`, and `BlockDecision` shapes;
- one `createLoopBreaker()` seam and no general `Guard` abstraction;
- no preserve-symbols code.

**Steps:**

1. Write Node tests for five admitted calls plus the refused sixth and its
   healthy sibling, key ordering, array ordering, tool separation, eviction,
   and refused-call exclusion.
2. Prove the tests fail against the copied module-scope/two-guard bundle where
   applicable, including the registration-isolation regression.
3. Replace `engine.ts` without referring to the old implementation while
   writing it; use only the accepted spec and fixture expectations.
4. Add unsupported/cyclic input tests that prove the canonicalization seam is
   total and admits data it cannot compare.
5. Run the Node behavior tests with type stripping.

**Evidence:** the sixth identical call is the first refusal, twenty admitted
intervening calls evict an old key, and two constructed breakers start empty.

**Commit:** `feat(guards): implement the E3.5 loop breaker`

---

### Task 2: Wire the Pi handler and its exception boundary

**Files:**

- Modify: `packages/engine/engine.ts`
- Modify: `tests/test_loop_breaker.mjs`

**Produces:**

- one breaker per default-extension registration;
- exact `loop_broken` telemetry;
- no exception escaping from inspection or telemetry append;
- a steering refusal with no false "in a row" claim.

**Steps:**

1. Add a minimal ExtensionAPI double that records the registered handler and
   appended entries.
2. Add failing tests that register the extension twice, drive one registration
   to its threshold, and prove the second is empty.
3. Add sibling tests for normal dispatch and repeated dispatch.
4. Inject unsupported input and a throwing `appendEntry`; prove the former is
   admitted and the latter still returns the block decision without escaping.
5. Implement the thin adapter around `createLoopBreaker()` and rerun the Node
   tests.

**Evidence:** two registrations in one imported module do not share state; one
blocked call produces one typed entry; a telemetry failure produces the same
block result and no rejected promise.

**Commit:** fold into Task 1; it is one shipped behavior, not a separate PR.

---

### Task 3: Replace the replay harness and prove the package

**Files:**

- Rewrite: `tools/replay_guards.mjs`
- Retain: `tests/fixtures/guards/*.json`
- Create: `tests/test_integration_guards.py`

**Produces:**

- a fresh replay harness that validates fixture shape and imports the shipped
  extension rather than reimplementing the policy;
- no-argument replay of all six fixtures and explicit-path replay;
- exact mismatch diagnostics and harness statuses;
- a temporary, user-settings-free `pi install` smoke.

**Steps:**

1. Add a marked integration test that runs all fixtures in one Node process;
   verify the copied bundle fails the final runaway expectation because its
   module-scope state leaks.
2. Rewrite the harness from the spec. Give every fixture a fresh extension
   registration and validate `name`, `calls`, and `expected` before replay.
3. Assert exact summaries for the six fixtures, including first-block values.
4. Add sibling malformed/mismatch fixtures in temporary paths and assert
   nonzero diagnostics without modifying committed evidence.
5. Run `pi install` from a temporary working directory with a temporary
   `PI_CODING_AGENT_DIR`, install the real `packages/engine`, and prove the
   temporary settings name both extensions while the user's settings stay
   untouched.

**Evidence:** all six committed fixtures pass together; runaway blocks once at
call six, anchor mismatch blocks 46 times first at call 14, and both healthy
fixtures remain at zero.

**Commit:** `test(guards): replay the E3.5 evidence`

---

### Task 4: Record installation, scope, and verification

**Files:**

- Modify: `README.md`
- Modify: `ROADMAP.md`
- Modify: `docs/architecture.md`
- Modify: `docs/contributing.md`
- Modify: `docs/glossary.md`
- Modify: `docs/index.md`
- Modify: `docs/usage.md`
- Modify: `docs/sdd.md`

**Produces:**

- correct `pi install` instructions and the loop-breaker policy;
- an E3.5 completion entry and concept-budget definition for `guard`;
- explicit limitations: schema-invalid calls and churn remain invisible;
- commands and measured counts recomputed from the final tree.

**Steps:**

1. Update public docs only after the shipped behavior is final.
2. Run the no-argument replay and copy only figures emitted by that command.
3. Run the complete repository gate set below.
4. Record named refusal/success, isolation, fixture, and install evidence in
   `docs/sdd.md`.
5. Mark E3.5 done and E4 current without changing the settled phase order.

**Evidence:** docs describe one guard, not the copied two-guard bundle, and the
install command is exercised by the integration fixture it documents.

**Commit:** `docs: document and verify E3.5`

## Final verification

Run from a clean checkout of the completed phase:

```console
uv run pytest
uv run pytest -m integration
uv run pytest -m "" --cov
node --test --experimental-strip-types tests/test_loop_breaker.mjs
node --experimental-strip-types tools/replay_guards.mjs
uv run ruff check .
uv run pyrefly check
uv run --group docs sphinx-build -W -b html docs docs/_build/html
git diff --check aa918b0 --
```

Record the actual test, fixture, and coverage counts from these commands. Do
not predict them in documentation. A failing real Pi install, a replay mismatch,
shared registration state, a preserve-symbols export, or an escaping handler
exception blocks phase completion.
