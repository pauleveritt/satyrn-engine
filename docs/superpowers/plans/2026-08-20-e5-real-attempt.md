# E5 — One real attempt: implementation plan

> Companion to the accepted design spec
> `docs/superpowers/specs/2026-08-20-e5-real-attempt-design.md`.

**Goal:** Ship `satyrn-engine attempt CONTRACT` and make `/implement CONTRACT`
run that command inside E3 delivery, using E4 for every write.

**Architecture:** Python prepares one frozen contract and revision map, starts
one hermetic Pi print-mode child, preserves transcript and Git patch artifacts,
and returns a typed result. The TypeScript slash command owns only command
construction, transport conversion, and receipt display. E3 owns isolation,
timeout, cleanup, commit, and publication. satyrn-evals remains an executable
consumer and imports no engine code.

**Scope guard:** no packaging, Windows proof, file creation, shell, subagent,
validation, grading, retry, batching, or performance claim.

## Task 1: Freeze the public command and typed result

Files:

- Create `src/satyrn_engine/attempt.py`
- Modify `src/satyrn_engine/exits.py`
- Modify `src/satyrn_engine/cli.py`
- Create `tests/test_attempt.py`
- Modify `tests/test_exits.py`
- Modify `tests/test_delivery.py`

Steps:

1. Add `ExitCode.ATTEMPT_FAILED = 10` without renumbering existing exits.
2. Add `AttemptCode`, frozen/slotted `AttemptResult`, `AttemptContext`, and
   `AttemptArtifacts`, plus an explicit exhaustive code-to-exit map.
3. Add `attempt [--model MODEL] CONTRACT`; resolve the model from the flag,
   then `SATYRN_MODEL`, otherwise return usage 2.
4. Keep `deliver`'s literal `--` parser unchanged and add parser siblings for
   attempt help, model precedence, malformed arguments, and refusal output.
5. Prove every detailed result maps to one stable process exit and exit 1 is
   still unassigned.

Commit after the tests pass.

## Task 2: Prepare one immutable mutation context

Files:

- Modify `src/satyrn_engine/attempt.py`
- Modify `tests/test_attempt.py`
- Create `tests/test_integration_attempt.py`

Steps:

1. Check `cwd` and the contract through E1; require a real Git worktree and
   capture exact `HEAD` once.
2. Copy accepted contract bytes to an engine-owned temporary file outside the
   worktree and point E4 at that copy.
3. Enumerate tracked paths with NUL-safe Git output, normalize safe POSIX
   names, apply E4's `fnmatch` rule, and hash exact bytes.
4. Refuse before Pi when no existing regular tracked file is writable.
5. Build the version-1 mutation context and deterministic prompt from the
   sorted path list.
6. Give every Git refusal a successful real-Git sibling. Include spaces,
   control characters where supported, a contract path inside the repo, an
   external contract, and an attempted contract self-edit.

Commit after default tests and focused integration pass.

## Task 3: Run one Pi child and preserve artifacts

Files:

- Modify `src/satyrn_engine/attempt.py`
- Modify `tests/test_attempt.py`
- Modify `tests/test_integration_attempt.py`
- Create `tests/fixtures/attempt/fake_pi.py`

Steps:

1. Define the `PiRunner` protocol and production subprocess implementation.
2. Build exact Pi argv with print JSON, no session, explicit E3.5/E4
   extensions, all discovery disabled, no project approval, and only
   `read,edit`.
3. Sanitize Git routing variables, `VIRTUAL_ENV`, and `SSH_AUTH_SOCK`; set the
   frozen mutation context and explicit engine checkout.
4. Spool Pi stdout to a safe regular temporary file, forward stderr, reap the
   child, export exact transcript bytes, and replay them to stdout.
5. Produce a raw binary-capable Git diff against captured HEAD and atomically
   export it only when non-empty.
6. On nonzero Pi exit, preserve both artifacts first, then return
   `ATTEMPT_FAILED`. On any artifact failure, that failure takes precedence.
7. Cover start failure, nonzero, empty output, no diff, binary-like diff,
   in-repository/pre-existing/symlink artifact paths, and write failures with
   success siblings.

Commit after default and integration evidence passes.

## Task 4: Turn `/implement` into E3 around E5

Files:

- Modify `packages/engine/orchestrator.ts`
- Modify `packages/engine/package.json` only if the final extension split needs it
- Modify `tools/replay_orchestrator.mjs`
- Modify `tests/test_mutator.mjs` only for shared transport types
- Create or modify `tests/test_orchestrator.mjs`
- Modify `tests/test_integration_protocol.py` or add a focused integration file

Steps:

1. Keep the one-shot E2/E4 exchange helper used by `mutator.ts`.
2. Add typed E3 receipt parsing and a delivery-spawn seam with stdout, stderr,
   close, error, deadline, and best-effort termination.
3. Resolve an in-repository contract to a worktree-relative inner argument;
   retain an external contract as absolute.
4. Build the exact outer E3 command and inner E5 command with
   `SATYRN_ENGINE_REPO` and `SATYRN_MODEL` explicit.
5. Replace the check-only `/implement` handler with delivery. Notify success
   with candidate ref/commit and refusal with exact receipt or adapter code.
6. Contain missing model/repo, start failure, timeout, malformed/extra output,
   nonzero without receipt, closed streams, and handler exceptions.
7. Replay the real package through an isolated fake engine, then exercise real
   E3 delivery around the fake Pi attempt.

Commit after Node behavior/coverage and Python integration pass.

## Task 5: Prove the complete slice

Files:

- Modify `tests/test_integration_attempt.py`
- Add only the minimal fixture contract/repository data needed
- Modify `tools/` only for a reusable live-gated command if necessary

Steps:

1. Start fake Pi in a real detached E3 worktree and make its one edit travel
   through the shipped TypeScript mutator and Python protocol.
2. Assert candidate parent, tree, declared changed path, transcript bytes,
   receipt, and unchanged caller HEAD/index/status.
3. Add a refusal sibling where E4 rejects a stale/undeclared replacement and
   E3 publishes no candidate.
4. Run a separately gated real model with an exact model string; record Pi
   version, base, contract, candidate ref, receipt, and transcript evidence in
   `docs/sdd.md` without committing model-generated project output.

Commit after the recorded proof is reproducible.

## Task 6: Close E5 documentation and gates

Files:

- Modify `README.md`
- Modify `ROADMAP.md`
- Modify `docs/architecture.md`
- Modify `docs/contributing.md`
- Modify `docs/glossary.md` only if one E5 term earns its place
- Modify `docs/index.md`
- Modify `docs/sdd.md`
- Modify `docs/usage.md`
- Modify `src/satyrn_engine/__init__.py`

Steps:

1. Document direct-attempt danger and `/implement` safety without implying
   container isolation or grading.
2. Show exact model configuration, source-checkout command, artifact seam,
   candidate inspection, and recovery/refusal behavior.
3. Mark E5 complete and E6 current only after live evidence exists.
4. Run and record measured commands, not copied counts:

```text
uv run pytest -q
uv run pytest -m integration -q
uv run pytest -m '' --cov --cov-report=term -q
node --experimental-strip-types --test --experimental-test-coverage ...
uv run ruff check .
uv run pyrefly check
uv run sphinx-build -W -b html docs docs/_build/html
git diff --check
```

5. Require 100% Python statement/branch coverage and 100% lines/branches/
   functions for E5-owned TypeScript.
6. Split the final history into design, implementation, and documentation
   branches so each stacked PR is understandable and independently checked.
