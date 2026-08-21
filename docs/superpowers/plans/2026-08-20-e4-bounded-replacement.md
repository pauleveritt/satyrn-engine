# E4 — One Bounded Replacement Implementation Plan

> **For agentic workers:** implement the tasks below in order. Use
> `local-ai-pi@8588ba4` only for the incidents and guarantees cited in the
> spec. Do not copy its TypeScript mutation engine.

**Goal:** Route one exact, unique file replacement through Pi → TypeScript →
Python. Python alone enforces the contract path, prior SHA-256 revision, and
anchor cardinality; every refusal is typed and leaves the file unchanged.

**Architecture:** A conditional Pi `edit` override translates one edit entry
to the existing one-shot JSON transport. `protocol.py` dispatches a typed
`ReplaceRequest` to a new `mutation.py` core. The core reads and atomically
replaces the file, then returns the next revision. E5 will supply the same
versioned mutation context inside E3's worktree.

**Tech stack:** Python 3.14 standard library plus the existing PyYAML contract
loader; TypeScript with erased Pi types and a JSON-schema literal; the existing
Node/Pi installation; pytest and Node's built-in test runner. No new runtime
dependency and no model call.

**Spec:**
`docs/superpowers/specs/2026-08-20-e4-bounded-replacement-design.md`

## Correction, 2026-08-21

The accepted plan is implemented with the design correction recorded at the
top of the spec. In particular, `expected_sha256` is a required nullable wire
field and Python returns `REVISION_UNAVAILABLE`; every target component is
checked without following symlinks; transport failures poison the mutation
context because publication may already have happened; and the shared
one-shot exchange contains asynchronous stdin failure and waits for child
close after bounded TERM/KILL teardown. Tests must prove each corrected rule
with a successful sibling. This note supersedes the narrower revision lookup,
symlink-escape, and inherited-lifecycle steps below.

## Phase-size decision

Keep E4 to one existing-file replacement. If implementation asks for file
creation, whole-file writes, two replacement entries, fuzzy anchors, symbol
analysis, size policy, validation, delivery, transcript handling, a persistent
process, or a generic mutation abstraction, stop and defer it. E5 consumes
this seam; it does not justify building its orchestration here.

---

### Task 1: Extend the contract without breaking E1–E3

**Files:**

- Modify: `src/satyrn_engine/contract.py`
- Modify: `tests/test_contract.py`
- Add: `tests/fixtures/contracts/writable.yaml`

**Produces:**

- immutable `Contract.writable_paths`;
- omission-compatible empty tuple;
- shape validation for a list of non-empty string patterns;
- no new contract exit code and no behavior change for existing fixtures.

**Steps:**

1. Add failing tests for one valid pattern list, omission, non-list, empty
   item, and non-string item.
2. Add `writable_paths: tuple[str, ...] = ()` to the frozen dataclass.
3. Parse only the optional field and retain the existing unknown-field policy.
4. Run the contract, check, and CLI suites under the no-process tripwire.

**Evidence:** the E1 valid fixture still equals the same contract plus an empty
tuple; the E4 fixture exposes its exact patterns.

**Commit:** fold into Task 3; the contract field has no E4 user behavior alone.

---

### Task 2: Implement the Python mutation core

**Files:**

- Create: `src/satyrn_engine/mutation.py`
- Create: `tests/test_mutation.py`

**Produces:**

- `MutationCode` `StrEnum`;
- frozen `MutationResult` and `MutationReceipt`;
- safe normalized relative paths and Python `fnmatch` enforcement;
- exact-byte SHA-256 revision checks;
- unique literal UTF-8 anchor replacement;
- same-directory atomic replacement with mode preservation.

**Steps:**

1. Write the success test first: replace one unique anchor and assert exact
   content, mode, result path, and next hash.
2. Add one refusal test and one success sibling for undeclared path,
   unavailable revision, stale revision, missing anchor, and ambiguous anchor.
   Assert unchanged bytes where the result is determinate.
3. Add malformed-path tests for absolute, parent, empty/dot segments, and NUL.
   Add operational refusal tests for internal and escaping symlink components;
   no target path component may be followed.
4. Add operational failure tests for missing/non-regular/non-UTF-8 targets and
   same-directory write/publish failures. Mocks are restricted to failures the
   real filesystem cannot safely force.
5. Add literal replacement tests for `$`, backslash, empty `new_text`, and
   CRLF-preserving exact bytes.
6. Implement with small functions: normalize, contain, hash, count, atomic
   replace. Do not add a class hierarchy or mutation registry.

**Evidence:** every refused operation leaves both file bytes and mode equal to
the pre-call snapshot; success changes one occurrence and returns the hash of
the bytes on disk.

**Commit:** `feat: implement one bounded replacement`

---

### Task 3: Add the typed replacement protocol

**Files:**

- Modify: `src/satyrn_engine/protocol.py`
- Modify: `src/satyrn_engine/exits.py`
- Modify: `tests/test_protocol.py`
- Modify: `tests/test_exits.py`
- Add: `tests/fixtures/protocol/request-replace-valid.json`
- Add: `tests/fixtures/protocol/response-replace-ok.json`

**Produces:**

- frozen `CheckRequest` and `ReplaceRequest` in a closed union;
- operation-specific parsing rather than one shared field tuple;
- typed replacement response payloads;
- `MUTATION_REFUSED = 9`, with detailed `MutationCode` in JSON;
- unchanged check request and response fixtures.

**Steps:**

1. Split the existing request dataclass into operation-specific frozen types;
   keep `parse_request` as the single parser seam.
2. Add exact field/type/SHA/path/anchor validation for `replace`, including a
   required nullable revision, and prove each malformed sibling returns
   `INVALID_REQUEST` without touching the file.
3. Dispatch with `match` over the parsed request type.
4. Render `result` only for replacement responses; pin exact success and
   refusal JSON shapes.
5. Map every `MutationCode` refusal/operational result to process exit 9 and
   keep exit 1 reserved for unexpected bugs.
6. Re-run every old protocol/check fixture unchanged.

**Evidence:** the real Python seam returns exact response code plus process
status for all five mutation outcomes, and the E2 check compatibility fixture
is byte-identical.

**Commit:** fold into Task 2; the protocol and core form one Python behavior.

---

### Task 4: Route Pi's one-edit tool through the engine

**Files:**

- Create: `packages/engine/mutator.ts`
- Modify: `packages/engine/orchestrator.ts`
- Modify: `packages/engine/package.json`
- Create: `tests/test_mutator.mjs`

**Produces:**

- versioned `MutationContext` parsing;
- one conditional `edit` override with a one-entry JSON schema;
- operation-specific request and response types;
- revision-map advancement only after a successful engine response;
- a total Pi `execute` boundary that always resolves to a tool result.

**Steps:**

1. Generalize the existing exchange response parser so check responses stay
   compatible and replacement responses can carry a typed result. Do not add a
   second spawner.
2. Add Node tests for valid and invalid contexts, absent-context no-op,
   one-entry request generation, successful map advancement, unavailable and
   stale revisions, malformed engine output, start failure, timeout, and an
   unexpected local error. A transport failure poisons the context and the
   next call starts no process. Each refusal has a successful sibling.
3. Define the Pi edit JSON schema locally as data so the package gains no
   runtime schema dependency. Require exactly one `{oldText,newText}` entry.
4. Implement `createMutator(exchange, context)` as the test/extension seam and
   keep all filesystem and contract policy out of TypeScript.
5. Register the tool only from a valid explicit
   `SATYRN_MUTATION_CONTEXT`. Preserve the built-in tool otherwise.
6. Add `mutator.ts` to the package manifest and keep E3.5 registration-local
   state unchanged.

**Evidence:** a refused engine response returns a Pi error result and the next
call uses the old revision; a success returns the new revision and the next
call sends it.

**Commit:** `feat: route bounded replacement through Pi`

---

### Task 5: Prove the vertical slice with real processes

**Files:**

- Modify: `tests/test_integration_protocol.py`
- Add: `tests/test_integration_mutator.py`
- Add or modify: E4 fixtures under `tests/fixtures/`

**Produces:**

- real console-protocol mutation evidence;
- shipped TypeScript adapter → real spawner → real Python evidence;
- exact success plus four named refusal siblings;
- temporary package installation with all extensions loaded and no user
  settings mutation.

**Steps:**

1. Add a real protocol success fixture and compare its response shape while
   computing the dynamic SHA independently.
2. Run the shipped TypeScript adapter against the real Python engine in a
   temporary repository; prove one replacement changes exact bytes.
3. Repeat with unavailable revision, stale revision, undeclared path, missing
   anchor, ambiguous anchor, and an internal symlink alias. Snapshot bytes and
   assert no determinate refusal mutates the target.
4. Install the package into a temporary `PI_CODING_AGENT_DIR`; assert the
   settings list `engine.ts`, `orchestrator.ts`, and `mutator.ts`, and that a
   session without context does not replace `edit`.
5. Keep every process-marked test out of default CI and prove the tripwire still
   catches accidental process use in the default tier.

**Evidence:** the one fixture crosses the full TypeScript/Python boundary and
returns the new SHA; all four refused fixtures return the exact detailed code
and unchanged file.

**Commit:** `test: prove the E4 replacement path`

---

### Task 6: Record the phase and its boundaries

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

- E4 usage and exact request/response examples;
- a clear statement that E4 mutates only a disposable workspace supplied by
  its caller and E5 has not yet wrapped it in delivery;
- the five E4 outcomes and revision semantics;
- recomputed test, coverage, and fixture evidence;
- E4 complete and E5 current without changing phase order.

**Steps:**

1. Update public docs only after implementation and data shapes settle.
2. Explain why Python owns policy and TypeScript owns translation.
3. Record success plus every sibling refusal by test name in `docs/sdd.md`.
4. Run the final gates below from the final tree and record only measured
   counts.
5. Check repository weight and concept budget; add only the term `revision` if
   the public docs actually need it.

**Commit:** `docs: document and verify E4`

## Final verification

Run from a clean checkout of the completed phase:

```console
uv run pytest
uv run pytest -m integration
uv run pytest -m "" --cov
node --test --experimental-strip-types tests/test_loop_breaker.mjs tests/test_transport.mjs tests/test_mutator.mjs
node --experimental-strip-types tools/replay_guards.mjs
uv run ruff check .
uv run pyrefly check
uv run --group docs sphinx-build -W -b html docs docs/_build/html
git diff --check
```

Record the actual counts from these commands. A changed file on any refusal,
an advanced revision after failure, an exception escaping the Pi tool, a
TypeScript path-policy decision, a default-tier subprocess, or an ordinary Pi
session whose built-in `edit` was replaced without context blocks phase
completion.
