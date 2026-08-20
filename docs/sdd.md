# How we work

This repository runs on **spec-driven development**: every feature cycle
produces a design spec (what we're building and why) and an implementation
plan (the task-by-task decomposition), both committed before the code.

The cycle shape, from the superpowers workflow:

1. **Brainstorm** — clarify the idea into a design, present it, get
   approval.
2. **Spec** — write the validated design to
   `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`, commit it.
3. **Plan** — write the implementation plan to
   `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`, commit it.
4. **Implement** — work the plan in reviewable cycles, each ending with a
   command a contributor can run and evidence that names a success fixture
   and a failure fixture.
5. **Record** — completed phases, and the withdrawn framings and retracted
   figures found along the way, move to the archive section of
   `ROADMAP.md` rather than being edited away.

## E3 verification record — 2026-08-19

The completed delivery phase was checked with its repository gates:

```console
uv run pytest
# 103 passed, 44 deselected

uv run pytest -m integration tests/test_integration_delivery.py -q
# 38 passed, 1 platform skip for the non-UTF-8 filename fixture

uv run pytest -m "" --cov
# 146 passed, 1 platform skip; 100% statements and 100% branches

uv run ruff check .
uv run pyrefly check
uv run --group docs sphinx-build -W -b html docs docs/_build/html
git diff --check
# all passed
```

Named evidence includes:

- candidate creation: `test_success_creates_candidate_with_exact_parent_and_paths`;
- discarded outcomes: `test_clean_root_reaches_no_changes_without_touching_source`,
  `test_failed_attempt_is_discarded_without_candidate`, and
  `test_timeout_kills_same_process_group_descendant`;
- refusal and cleanup precedence:
  `test_dirty_source_is_refused_before_worktree` and
  `test_locked_worktree_reports_retained_path_for_manual_recovery`;
- atomic publication:
  `test_two_delivery_processes_publish_exactly_one_candidate`;
- lifecycle edges:
  `test_registration_interrupt_after_real_add_leaves_no_stale_worktree`,
  `test_attached_head_at_base_is_discarded_without_candidate`, and
  `test_caller_head_can_advance_after_preflight_without_moving_candidate_base`;
- environment and receipt transport:
  `test_engine_git_ignores_caller_date_overrides_but_command_keeps_them` and
  `test_closed_receipt_pipe_exits_one_without_hiding_published_candidate`.

The timeout fixture starts a same-process-group descendant with a delayed
write. The absent sentinel after delivery returns is the evidence that teardown
finishes before Git cleanup. The registration-interrupt fixture compares the
complete pre/post `git worktree list --porcelain -z` state, so it also rejects a
stale prunable registration.

## E3.5 verification record — 2026-08-20

The completed loop-breaker phase was checked against the Python repository
gates and an independent Node coverage gate for the shipped TypeScript:

```console
.venv/bin/pytest -q
# 103 passed, 51 deselected

.venv/bin/pytest -m integration -q
# 50 passed, 1 platform skip, 103 deselected

.venv/bin/pytest -m "" --cov -q
# 153 passed, 1 platform skip; 100% Python statements and branches

node --test --experimental-strip-types --experimental-test-coverage \
  --test-coverage-lines=100 --test-coverage-branches=100 \
  --test-coverage-functions=100 \
  --test-coverage-include=packages/engine/engine.ts tests/test_loop_breaker.mjs
# 14 passed; engine.ts 100% lines, branches, and functions

node --experimental-strip-types tools/replay_guards.mjs
# 6 fixtures matched

.venv/bin/ruff check .
.venv/bin/pyrefly check
uv run --group docs sphinx-build -W -b html docs docs/_build/html
git diff --check
# all passed
```

Named evidence includes:

- threshold and steering: `the sixth identical admitted call is refused with
  typed telemetry`, with `a varied sixth call is admitted` as its sibling;
- rolling state: `twenty newer admitted calls evict an older key` and `blocked
  calls never enter the admitted window`;
- registration lifetime: `each extension registration owns an empty breaker`;
- Pi blast radius: `telemetry failure cannot escape or admit an already
  blocked call`, `unexpected canonicalization errors cannot escape the Pi
  handler`, and `unexpected Pi event access errors cannot escape the handler`;
- real artifact replay:
  `test_all_evidence_fixtures_replay_in_one_process`, including the 60-call
  anchor-mismatch fixture (46 blocks, first at call 14), its healthy siblings,
  and the six-call runaway excerpt (one block at call 6);
- package surface: `test_pi_installs_package_only_in_temporary_settings`.

Before replacement, the copied bundle failed six of the first eleven Node
behavior tests. Its module-scope state also made an all-fixture replay report
two runaway blocks instead of the committed expectation of one. Those failures
are the non-vacuity evidence for the fresh implementation and its
registration-local state.

### E3.5 correction verification — 2026-08-21

The accepted E3.5 design received a bounded correction for canonical JSON,
telemetry lifetime, replay schema strictness, and package-dispatch evidence.
The corrected tree was measured with the same gates:

```console
.venv/bin/pytest -q
# 103 passed, 52 deselected

.venv/bin/pytest -m integration -q
# 51 passed, 1 platform skip, 103 deselected

.venv/bin/pytest -m "" --cov -q
# 154 passed, 1 platform skip; 100% Python statements and branches

node --test --experimental-strip-types --experimental-test-coverage \
  --test-coverage-lines=100 --test-coverage-branches=100 \
  --test-coverage-functions=100 \
  --test-coverage-include=packages/engine/engine.ts tests/test_loop_breaker.mjs
# 16 passed; engine.ts 100% lines, branches, and functions

node --experimental-strip-types tools/replay_guards.mjs
# 6 fixtures matched

.venv/bin/ruff check .
.venv/bin/pyrefly check
uv run --group docs sphinx-build -W -b html docs docs/_build/html
git diff --check aa918b0 --
# all passed
```

Named correction evidence is `a top-level __proto__ key is canonicalized as
JSON data`, `a nested __proto__ key is canonicalized as JSON data`, and the
post-eviction telemetry reset in `twenty newer admitted calls evict an older
key`. `test_fixture_without_required_first_block_is_rejected` fixes the replay
schema refusal, while the two excerpt fixtures now carry explicit
`firstBlock` values. Finally,
`test_pi_installs_and_dispatches_package_extension_in_temporary_settings`
resolves `engine.ts` from the installed package manifest, loads it, observes
registration, and dispatches six calls through its real handler.

## E4 verification record — 2026-08-21

The corrected bounded-replacement phase was checked through the hermetic
Python core, the shipped TypeScript policies, and the real TypeScript → Python
process path:

```console
.venv/bin/pytest -q
# 161 passed, 61 deselected

.venv/bin/pytest -m integration -q
# 60 passed, 1 platform skip, 161 deselected

.venv/bin/pytest -m "" --cov --cov-report=term -q
# 221 passed, 1 platform skip; 827 statements and 214 branches, 100%

node --test --experimental-strip-types --experimental-test-coverage \
  --test-coverage-lines=100 --test-coverage-branches=100 \
  --test-coverage-functions=100 \
  --test-coverage-include=packages/engine/mutator.ts tests/test_mutator.mjs
# 17 passed; mutator.ts 100% lines, branches, and functions

node --test --experimental-strip-types tests/test_transport.mjs
# 3 passed

.venv/bin/ruff check .
.venv/bin/pyrefly check
uv run --group docs sphinx-build -W -b html docs docs/_build/html
git diff --check aa918b0 --
# all passed
```

Named E4 evidence includes:

- Python success and next revision:
  `test_replaces_one_unique_anchor_and_returns_next_revision`;
- the five policy refusals and unchanged-file siblings:
  `test_refuses_undeclared_path_without_changing_file`,
  `test_refuses_unavailable_revision_after_path_and_target_checks`,
  `test_refuses_stale_revision_without_changing_file`,
  `test_refuses_missing_anchor_without_changing_file`, and
  `test_refuses_ambiguous_anchor_without_changing_file`;
- filesystem boundary and atomicity:
  `test_refuses_symlink_escape_without_changing_external_file`, the internal
  symlink leaf/component siblings,
  `test_atomic_replace_failure_is_named_and_removes_temporary`, and
  `test_crlf_bytes_are_preserved_outside_replacement`;
- revision-map behavior in the shipped adapter: `success advances the revision
  used by the next request`, `a refusal does not advance the revision`, and
  `missing revision reaches the engine as an explicit null`;
- Pi exception containment: `indeterminate engine outcomes poison the mutation
  context`, `base response parser rejects non-object JSON without leaking a
  type error`, and `a mismatched successful path is a contained malformed
  response`;
- ordinary-session safety: `default extension leaves built-in edit alone
  without explicit context`;
- real vertical slice:
  `test_shipped_adapter_replaces_one_anchor_through_real_engine` and its
  parameterized unavailable, stale, undeclared, missing, and ambiguous refusal
  siblings plus the real internal-symlink refusal/regular-file success pair;
- package surface:
  `test_pi_installs_and_dispatches_package_extension_in_temporary_settings`,
  pinning all three extension paths and dispatching the shipped loop breaker.

The real refusal fixture snapshots the target before each process run and
compares its bytes afterward. The success fixture computes the expected next
SHA independently from the bytes on disk. Together they prove that the JSON
code, revision map, and filesystem result agree rather than merely exercising
three implementations of the same assertion.

## E5 verification record — 2026-08-20

The one-real-attempt phase was checked through the hermetic Python core, real
Git and subprocess integration, all three shipped TypeScript policies, and one
recorded local model run:

```console
uv run pytest -q
# 177 passed, 63 deselected

uv run pytest -m integration -q
# 62 passed, 1 platform skip, 177 deselected

uv run pytest -m "" --cov --cov-report=term -q
# 239 passed, 1 platform skip; 1051 statements and 254 branches, 100%

node --experimental-strip-types --test \
  tests/test_loop_breaker.mjs tests/test_mutator.mjs tests/test_orchestrator.mjs
# 38 passed

node --test --experimental-strip-types --experimental-test-coverage \
  --test-coverage-lines=100 --test-coverage-branches=100 \
  --test-coverage-functions=100 \
  --test-coverage-include=packages/engine/orchestrator.ts \
  tests/test_orchestrator.mjs
# 8 passed; orchestrator.ts 100% lines, branches, and functions

node --experimental-strip-types tools/replay_guards.mjs
node --experimental-strip-types tools/replay_orchestrator.mjs
# 6 guard fixtures and all adapter replay cases matched

uv run ruff check .
uv run pyrefly check
uv run --group docs sphinx-build -W -b html docs docs/_build/html
git diff --check
# all passed
```

Named E5 evidence includes:

- hermetic child shape: `test_prompt_and_pi_command_are_small_and_hermetic`;
- exact artifact and mutation-context transport:
  `test_attempt_success_exports_exact_artifacts_and_context`;
- refusal and artifact preservation:
  `test_pi_nonzero_preserves_artifacts_then_refuses` and
  `test_attempt_preserves_transcript_for_no_change_failure_and_refusal`;
- the real E4 path under a fake Pi:
  `test_attempt_uses_shipped_e4_mutator_and_exports_artifacts`;
- the complete isolation boundary:
  `test_e3_delivery_wraps_same_attempt_and_keeps_source_clean`;
- TypeScript transport containment: `delivery converts every transport
  failure` and `implement handler reports configuration, success, refusal,
  and crash`.

The recorded live run used Pi 0.84.1 and
`omlx/gemma-4-12B-it-MLX-8bit`. From base
`77ec376381ce7a41d2365166b3035de7b60fcecf`, the model read `app.py` and used
one bounded edit to change its return value. Delivery published candidate
`465431a480a21c72cb39274475afceb39eaac99b` at
`refs/satyrn/candidates/e5-live-answer/head`; the source remained clean at the
original base. The exact 50-line, 13,967-byte transcript had SHA-256
`db7958d09b9d35d35872158e69ae05f4729d539fdd894706acaae8c9efc12f77`.
The transcript itself remains an external evidence artifact and is not copied
into the repository.

The disciplines review holds you to:

- **Concept budget** — new jargon is a cost against a 5–10 h/wk
  contributor's ability to hold the design in mind.
- **Non-vacuity** — a refusal test has a sibling success test.
- **Verify, don't assert** — demonstrate a claim, don't state it.
