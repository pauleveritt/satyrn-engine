# Brief: satyrn-engine

**Read this first. Do not re-brainstorm the project.** The design in this file
and in `ROADMAP.md` is the output of a long, twice-reviewed design session. The
cuts were deliberate. Brainstorm *within* a phase; do not reopen the phase list
or the architecture.

The prior project recorded a specific failure: a cycle spent a full spec,
build, pilot and research record on a premise that two committed documents
already refuted. The record existed and was never retrieved. Re-deriving this
design is that failure in a new repository.

## What we are building

**satyrn-engine** turns a bounded contract into a candidate change, without
modifying the caller's working tree.

It is a **Python library and CLI**, plus a **thin TypeScript adapter** that
makes it available inside Pi. Python is the core because the engine must be
usable outside Pi — as a library, a CLI, in CI, and by Python tooling — and
because the audience is Python developers.

It owns: contract parsing and validation; writable-path and revision
enforcement; candidate worktree, validation, commit-or-discard and receipt
behavior; the Pi package and its adapter; the engine protocol and its
compatibility fixtures.

It does **not** own: workloads, grading, repeated runs, comparison statistics,
or contract authoring. Contract authoring is a main-agent skill, not engine
machinery.

## Provenance

This repository is seeded from research at
`github.com/pauleveritt/local-ai-pi`, commit `8588ba4`, specifically
`docs/superpowers/research/2026-08-16-two-repo-rewrite-and-python-engine.md`
and `docs/superpowers/handoff/HARVEST-INDEX.md` (the harvest index, in a
sibling directory, not beside it).

**That repository is evidence, not source.** Nothing crosses over except by
explicit decision, argued at the moment the need arises. Do not transplant its
`harness/` package. Re-earn behavior from the named fixture and incident.

The guards are the one piece of prior work worth reusing — but as reference
material for a real phase (`ROADMAP.md`, phase E3.5), not a verbatim copy.
Their behavior is proven (the loop breaker's window/threshold, its replay
fixtures against a recorded 245-call loop) and should be matched; the
TypeScript implementing it is written fresh in this repository, through the
same spec → plan → code loop as every other phase.

**Correction, recorded rather than edited away:** this section previously
said the guards were "copied verbatim" and treated that as already done. They
were not. No TypeScript existed in this repository when that line was
written or for some time after. `ROADMAP.md`'s "Now" section carries the same
correction.

## The trap we are avoiding

Three prior attempts became engineering efforts about orchestration —
hangs, timeouts, gating, graders, cardinal rules — until the machinery
outgrew anyone's ability to hold it in their head. A fourth produced two
workloads, six arms, five violation classes and three amendment chains in a
single day: correct output, exploding surface area.

The most recent attempt ended at 10,901 lines of non-test source, with a
6,065-line eval harness measuring a 340-line engine.

Consequences:

- **One phase at a time.** Tangents go to a backlog, never into the current
  phase.
- **No machinery ahead of the contract it serves.** A framework arrives when
  three concrete implementations need the same shape, not before. The prior
  project built a 380-line checker framework to host five criteria; one
  fifteen-line rule survived.
- **Concept budget.** Every term is a cost against a 5–10 h/wk contributor's
  ability to hold the design in mind. If a doc needs a term they cannot
  absorb, the term goes — not the contributor. Keep the budget from phase one.
- **Repository-weight budget.** The prior project tracked a 104.7 MiB corpus
  that no supported code path read, making a first checkout 123 MiB. Check
  weight from phase one.

## Binding rules

1. **Verify, don't assert.** Claims get demonstrated, not argued. Cite
   `file:line`. Do not transcribe a number you did not compute; carry the
   command that recomputes it.
2. **Every phase ships one user-visible behavior** and names what it excludes.
3. **Default tests use no model, no network, no subprocess.** Process behavior
   lives in a small, explicitly marked integration tier that does not run in
   CI. This is **enforced mechanically**: a planted process-spawning test must
   fail the build, and that tripwire is itself proven once and kept.
4. **A refusal test has a sibling success test.** Most of this code tests
   rejection, and rejection is the default outcome of most failures — so a
   broken test passes silently.
5. **Product code never imports a laboratory.** Import direction is one-way
   and mechanically checked. The prior project shipped a module that dragged a
   35 KB research module onto the product path, and had to surgically extract
   another for the same reason.
6. **A test seam is the extension seam.** If you inject it to test it, that is
   the extension point. Do not add a second plugin system. Any proposed seam
   that is not also a test seam is refused.
7. **Data over code for extension.** A contract is a file. A fixture is
   committed JSON. Extending means adding a file, not subclassing.

## Architecture, already decided

**One Python process per operation.** The adapter starts the engine
executable, writes one versioned JSON request to stdin, closes stdin, reads
one JSON response, waits for exit, and converts every transport failure into a
named refusal. No persistent sidecar, no pool, no supervisor, no circuit
breaker, no subinterpreters, no correlation IDs, no general JSON-RPC layer.

This is slower and much smaller. It has no session lifetime and no lifecycle
question. If startup cost is later *measured* as material, the same request
and response objects gain a persistent transport then.

**The guards stay TypeScript.** They run inside `emitToolCall`, which Pi
neither times out nor wraps in `try/catch`, on every ordinary tool call in a
live session — not only during a deliberate `/implement`. A Python sidecar
there would make an always-on, zero-dependency check depend on a spawned
process succeeding before every single tool call, for a component that fired
zero times across a recorded 24-run comparison. This is not a latency
argument — tool calls are seconds apart, a subprocess hop is invisible at
that scale — it is blast radius: a broken or missing sidecar here breaks
every tool call in an ordinary session, where the Python core's own sidecar
(E3–E6) only runs during an operation the user deliberately started. See
phase E3.5 in `ROADMAP.md` for how they land in this repository.

**Facts about Pi that constrain the adapter** (verified against Pi v0.84.2):
extension handlers are awaited sequentially with no host deadline;
`emitToolCall()` has no `try/catch`, so an adapter error escapes the turn;
print mode emits `session_shutdown` on normal completion, errors, SIGTERM and
SIGHUP, but installs no SIGINT handler; `ctx.shutdown()` is a no-op in print
mode. These require the adapter's own deadline and exception boundary. They do
not justify a session-scoped process.

## Where to start

`ROADMAP.md`, phase E1. Brainstorm E1's details — CLI surface, exit-code
semantics, contract file format, test layout — treating this brief and the
phase list as settled. Build the fast-tier tripwire first; it constrains every
later phase's test design.
