# Working in this repository

**Before writing any code for a new phase: stop.** Post a short design
proposal for the phase (CLI surface, exit codes, data shapes, test layout)
and wait for explicit confirmation before implementing anything. This
instruction is self-contained and does not depend on any skill, plugin, or
tool being available — if you were invoked as a subagent and are inclined
to skip an interactive step for that reason, this one still applies; a
one-way task dispatch is the wrong mode for starting a new phase in this
repository. If there is no way to ask and wait, stop and say so instead of
proceeding.

Read `BRIEF.md` and `ROADMAP.md` in full before any work, every session.
**Do not re-brainstorm the project.** The design in those two files is the
output of a long, twice-reviewed session recorded in
`docs/superpowers/research/2026-08-16-harvest-index.md`. Brainstorm only
*within* the current phase — do not reopen the phase list, the
Python-core/TypeScript-adapter split, the one-process-per-operation
decision, or the guards-stay-TypeScript decision. Each has a recorded
reason and a recorded condition that would reopen it, in `BRIEF.md`'s
Backlog section.

## Rules that govern every edit, not just phase kickoff

- **Verify, don't assert.** Claims get demonstrated, not argued. Cite
  `file:line`. Do not write down a number you did not compute yourself;
  carry the command that recomputes it.
- **Default tests use no model, no network, no subprocess.** This is
  enforced mechanically by a planted process-spawning test that fails the
  build — do not weaken or remove it. Process behavior lives in a small,
  explicitly marked integration tier that does not run in CI.
- **A refusal test has a sibling success test.** Most of this code tests
  rejection, and rejection is the default outcome of most failures, so a
  broken test passes silently. Never add one without the other.
- **Product code never imports a laboratory or a later phase's
  scaffolding.** Import direction is one-way. If you are tempted to import
  ahead, that is a sign the phase boundary is wrong, not that the import is
  fine.
- **A test seam is the extension seam.** If you inject something to test
  it, that is the extension point. Do not add a second plugin mechanism.
  Any proposed seam that is not also a test seam should be refused.
- **No framework before three concrete implementations need the same
  shape.** A framework built for one or two consumers is machinery ahead of
  its contract.
- **A correction is recorded, not edited away.** If something you wrote
  earlier turns out wrong, add a note that says so and why; don't silently
  rewrite history.
- **Write modern 3.14 Python.** Favor structural pattern matching
  (`match`/`case`) over `if`/`elif` chains that branch on a value's shape;
  declare type aliases with the `type` statement and a semantic name
  (PEP 695) instead of `TypeVar`/`TypeAlias` assignments; and use the
  walrus operator (`:=`) where it removes a repeated computation from a
  condition. Favor these where they clarify, not as a mandate.

## When something looks like a known failure mode

Check `docs/superpowers/research/2026-08-16-harvest-index.md` before
re-deriving an explanation. It is indexed by symptom (e.g. "the extension
loaded but nothing happened", "the model edited the file and nothing
changed") because a prior cycle spent a full spec, build, and pilot on a
premise two committed documents already refuted — the record existed and
was never retrieved. If you find a new failure mode worth keeping, add it
to this repository's own lessons file the same way, indexed by symptom.

## Provenance

Seeded from `github.com/pauleveritt/local-ai-pi` at commit `c74c31f`.
**That repository is evidence, not source.** Do not transplant its
`harness/` package. Re-earn each behavior from the fixture and incident
named in the harvest index. The one exception, already made: the guards
(`packages/engine/engine.ts`, `tools/replay_guards.mjs`, and
`tests/fixtures/guards/`) were copied in verbatim and must not be
"improved" in transit — they are proven and pinned by replay fixtures.

**Correction, 2026-08-18:** the provenance paragraph above is retained as the
historical claim, but it is wrong. The pinned evidence revision is now
`local-ai-pi@8588ba4`, and the guards were not copied into this repository.
They are phase E3.5: implement them fresh here and use the old implementation
and replay fixtures as evidence, not source. `BRIEF.md` and `ROADMAP.md` carry
the same correction.
