# HP3 — chained isolation: implementation plan

> Companion to the design spec
> `docs/superpowers/specs/2026-09-09-hp3-chained-isolation-design.md`, which
> governs where the two disagree.

**Goal:** phase N of a multi-request workload runs in a worktree branched
from phase N-1's **accepted commit**, and a refused phase stops the chain
with no candidate ref.

**Architecture:** one added keyword on `deliver`, and one function that loops
it. No new subsystem, no orchestrator, no new exit code. `deliver` already
turns one contract into one candidate; HP3 composes those.

**Scope guard:** no pool, no parallel dispatch, no retry or repair, no
routing, no packet authoring, no change to the mutator, the runner or the
contract shape, and no rewriting of existing receipts.

## Slice order, and why

1. **The base parameter** — the one fact that makes chaining possible.
2. **The chain, offline** — decision logic against scripted phase results.
3. **The chain, for real** — Git, refs, and the property this cycle exists
   for.

Slice 1 first because everything else is vacuous without it: a chain that
ignores its base still produces three receipts and a final ref, and every
test but one would pass.

## Slice 1 — `deliver(..., base=None)`

**Files:** `src/satyrn_engine/delivery.py`; `tests/test_delivery_base.py`;
`tests/test_integration_delivery_chain.py`. *(Corrected after review: this
plan first named `tests/integration/...` paths, a layout this repository does
not use -- its integration tier is `tests/test_integration_*.py`.)*

`_preflight` takes the base and resolves it with
`rev-parse --verify <base>^{commit}`, in place of today's unconditional
`HEAD^{commit}` (`delivery.py:308`). `None` keeps the current behaviour
**byte for byte**, so every existing caller and every recorded receipt is
unaffected.

An unresolvable base is a refusal, never a fall back to `HEAD`. It reuses the
existing code for a base that cannot be resolved rather than inventing one.

*Default tier:* `None` still resolves `HEAD` — asserted by the argv the
resolver is asked to run, since spawning belongs to the other tier.
*Integration:* a real commit-ish resolves and is recorded as `base_commit` in
the receipt; a garbage base refuses **and the receipt names the base it could
not resolve**, so a chain failure says which phase and which commit.

**Done when** the existing delivery tests pass untouched, which is the
evidence that `None` changed nothing.

## Slice 2 — `deliver_chain`, offline

**Files:** `src/satyrn_engine/delivery.py`; `tests/test_delivery_chain.py`.

```
deliver_chain(repo, phases, *, timeout, deliver=deliver) -> ChainReceipt
```

The `deliver` parameter is the **test seam, and therefore the extension
seam** (`CLAUDE.md`): the default tier passes a scripted stand-in, and no
second injection mechanism is added anywhere.

`ChainReceipt` carries the ordered receipts, the final `candidate_ref` or
`None`, the accepted intermediate refs, and the stopping phase's code.
`candidate_ref` is `None` whenever any phase refused, so a caller cannot read
a usable ref off a broken chain by accident.

*Tests, each refusal with its sibling:*
- three phases accepted in order, and **each call after the first receives
  the previous phase's candidate commit as its base** — asserted on the
  recorded calls, not inferred from the result;
- a refusal at phase 2 stops the chain: two receipts, `candidate_ref is
  None`, one accepted ref retained;
- a refusal at phase 1 retains no refs at all;
- the reported code is the stopping phase's own, not a new one;
- an empty phase list is refused rather than returning an empty success.

**Done when** a chain whose scripted phases all succeed differs observably
from one whose second phase refuses, in ref count as well as in code.

## Slice 3 — the chain, for real

**Files:** `tests/test_integration_delivery_chain.py`.

Marked `integration`: real Git, real refs, excluded from the default run and
from CI.

**Write this test first within the slice.** Phase 3's worktree contains phase
1's and phase 2's committed files. It is the only check in the whole cycle
that fails if the base parameter is ignored entirely — every other test
passes against a chain that quietly bases every phase on `HEAD`.

Then:
- a second run of the same chain refuses with `CANDIDATE_EXISTS`, and the
  first run's refs are **still there afterwards**, asserted rather than
  assumed;
- a mid-chain refusal leaves the earlier accepted ref resolvable, which is
  what makes a mid-chain regression reproducible without re-running.

**Done when** the phase-3 check has been shown to fail against a build that
ignores `base`. Run it that way once, on purpose, before trusting it.

## Verification

`just gates`, exit code read directly, never piped. It excludes the
integration tier, so this cycle is not done until the following has been run
by hand and its exit code read:

```
uv run pytest -q -m integration tests/test_integration_delivery_chain.py
```
