# Backlog

Parked 2026-09-14. Deferred items and their reopen conditions live in the
release-one design's "Deferred, deliberately" section in `satyrn-evals`. The
entries this file held are on the tag (`git show
pre-release-one-2026-09-13:BACKLOG.md`); the two dated 2026-09-06 (file
creation through the mutator; a model-invocable test runner) are done in
Phase 1 as native `write` under guard 3 and the `self_test` tool.

Added 2026-10-02, from the `ac-demo` walkthrough outside Evals: **`/implement`
defaults its own environment.** `orchestrator.ts` refuses with
`ENGINE_START_FAILED` unless `SATYRN_ENGINE_REPO` and `SATYRN_MODEL` are set,
and Pi settings cannot set environment variables, so a developer needs a
launcher or a shim. Default the repo from the extension's own location
(`packages/engine` → `../..`) and the model from the session's `ctx.model`
(following `model_select`); a set variable still wins. `ac-demo` carries the
shim as `.pi/extensions/satyrn-defaults.ts`. Reopens when `/implement` is
installed anywhere other than that demo.
