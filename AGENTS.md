# Working in this repository

Read `BRIEF.md`, then the release-one design in `satyrn-evals`
(`docs/superpowers/specs/2026-09-13-release-one-design.md`) and the plan for the
current phase. The tag `pre-release-one-2026-09-13` on `main` is evidence, not
guidance.

Default tests use no model, network, or subprocess (`tests/conftest.py`
enforces it); process behaviour is the `integration` tier. A refusal test has
a sibling success test. Guards are proven by replay over recorded tool-call
sequences before they run live (`tools/replay_guards.mjs`, `tests/fixtures/guards`).
Product code never imports a laboratory. One process per operation; no
sidecar. Commit at plan-task boundaries; never merge or push; never run a
model unless the plan's step names it. Every file has a row in `PROVENANCE.md`.
