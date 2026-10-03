# Backlog

Parked 2026-09-14. Deferred items and their reopen conditions live in the
release-one design's "Deferred, deliberately" section in `satyrn-evals`. The
entries this file held are on the tag (`git show
pre-release-one-2026-09-13:BACKLOG.md`); the two dated 2026-09-06 (file
creation through the mutator; a model-invocable test runner) are done in
Phase 1 as native `write` under guard 3 and the `self_test` tool.

**Added 2026-10-02, from the cleanup audit**
(`satyrn-evals/evidence/2026-10-02-cleanup-audit/README.md`, sections 2, 6,
7). Items that cross into satyrn-evals are ordered by
`satyrn-evals/docs/superpowers/plans/2026-10-03-cross-repo-cleanup-coordination.md`:
every engine commit the Engine arms use is a new Engine condition, so they
land on `main` together, before one re-pin.

- **Receipts miss red-stop firings.** `packages/engine/runner.ts` emits
  `self_test_red_stop`; `src/satyrn_engine/budget.py` `GUARD_KINDS` counts
  the retired `self_test_redirected` instead. Built 2026-10-03 as `d979cba`
  on branch `eb-confinement-parity`; closes when evals re-pins (evals plan
  `2026-10-03-engine-confinement-and-edit-parity.md`, Task 5), which also
  updates evals' own guard vocabulary.
- **Vendored task manifests.** `tests/fixtures/derive_size/selfhost-preflight-quiet.json`
  declares ten symbols where the live evals task declares nine, and
  `tests/test_derive_size.py` finds the evals tree by an absolute path on one
  machine. Coordination plan Task 4; on `main` before the re-pin.
- **Path admission differs between `mutation.py` (fnmatch, honours `[seq]`)
  and `scope.ts` (`*` and `?` only).** Pin one behaviour with a bracket
  fixture when any contract pattern uses brackets; until then, a known gap.
- **Docs re-earned on `main`:** `AGENTS.md`, `BRIEF.md`, `ROADMAP.md`,
  `README.md` describe phases and paths this tree does not have; `docs/usage.md`
  and `docs/glossary.md` omit `--base`, the budget flags, `protocol`,
  `test_command`, `deadline_seconds`, `SATYRN_EXTRA_EXTENSIONS` and exit codes
  11-14, and are MyST with no Sphinx here. Reopens with the first engine
  change after evals C4. A change to the README, usage or glossary needs
  `just sync-engine` in evals at the next re-pin, and decides whether evals'
  MyST transform stays.
- **Fossils:** `tools/replay_orchestrator.mjs` and three unread
  `tests/fixtures/protocol/*.json`; duplicate tags
  `archive/hp3-chained-isolation-2026-09-11` and
  `pre-release-one-main-2026-09-09`. Delete at the next instrument-free
  window, before a re-pin rather than after; one provenance row each.
- **Gates that nothing runs:** `pyrefly`, `pytest-cov`, `types-pyyaml` and
  the coverage and pyrefly sections. Gate them or remove them; decided once
  for both repositories (evals `ROADMAP.md`, "Gates that nothing runs").
