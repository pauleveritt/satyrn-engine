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

- **Receipts count red-stop firings: done.** `d979cba`, merged as `5b681b0`;
  evals has pinned it since `23a0ef6`.
- **Vendored task manifests: done** on branch `cleanup-derive-size-fixture`:
  `selfhost-preflight-quiet` re-vendored at nine symbols, and the evals tree
  found through `SATYRN_EVALS_TASKS` or the sibling checkout. It lands with
  `eb-head-tolerance` in one merge, which evals pins in its ledger entry
  "Engine re-pin for head tolerance".
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
- **A model `git commit` no longer discards the candidate**
  (`COMMAND_CHANGED_HEAD` now means an attached HEAD only); evals ledger
  2026-10-06 ruling 2. The receipt records `head_moved`. Built 2026-10-06 on
  branch `eb-head-tolerance`; closes when evals re-pins. A branch the model
  creates still refuses, because linked worktrees share refs with the source
  repository.
- **`source_snapshot` in the integration tests omits `for-each-ref`.** Model
  ref writes outside HEAD (`git branch` without switching, tags, stash,
  `update-ref`) go undetected by `assert_source_unchanged`. Pre-existing.
- **Three receipts in `delivery.py` are rebuilt from `pending` field by
  field** (CLEANUP_FAILED in `_attempt`; CANDIDATE_EXISTS and GIT_FAILED in
  `_publish`), so a new receipt field can be dropped silently, as
  `head_moved` was until the 2026-10-06 fix. Consider `dataclasses.replace`
  at a later re-pin.
