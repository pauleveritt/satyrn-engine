# HP3 — chained isolation

**Status: proposed 2026-09-09; scope confirmed by the maintainer.**
Implementation follows this document. It authorizes no inference.

HP3 is the one cycle of `satyrn-evals`' **Phase HP** that lands in this
repository. Ownership was settled along the line `BRIEF.md` already draws:
this repository owns packet execution, chained isolation and candidate
production (`BRIEF.md:15`), and contract authoring stays a main-agent skill
(`BRIEF.md:29`). See `ROADMAP.md`'s HP3 section.

## 1. The property, in one line

Each phase of a multi-request workload runs in a disposable worktree
branched from **the previous phase's accepted commit**, not from `HEAD`.
Committed code folds forward through the checkout while context folds forward
through the packet. A refused phase stops the chain: no candidate ref, no
partial chain.

SwiftStar states it in its own docstring
(`swiftstar/Sources/SwiftStarAppKit/WorktreeTransaction.swift:4-12`).
**Borrowed as behaviour, not as code**, per this repository's provenance rule.

## 2. Why `deliver` cannot already do this

`deliver` derives its base from the caller's `HEAD`
(`src/satyrn_engine/delivery.py:308`, `git rev-parse --verify HEAD^{commit}`)
and there is no parameter to override it. So a second `deliver` on the same
repository starts from the same base as the first, and phase 2 would never
see phase 1's code. That single fact is the whole of HP3's necessity, and it
is why the fix is a parameter and a loop rather than a new subsystem.

## 3. CLI surface

**No new subcommand.** `deliver` already turns one contract into one
candidate; HP3 composes those.

- `deliver(..., base: str | None = None)` — an added keyword. `None` keeps
  today's behaviour exactly, so every existing caller and every recorded
  receipt is unaffected. A non-`None` value is a commit-ish resolved with
  `rev-parse --verify <base>^{commit}` before use; an unresolvable base is a
  refusal, never a silent fall back to `HEAD`, because falling back would
  make a broken chain look like a working one.
- `deliver_chain(repo, phases, *, timeout) -> ChainReceipt` — where `phases`
  is an ordered sequence of `(contract_path, command)`. Phase 1 uses the
  repository `HEAD`; phase N uses the commit that phase N-1's candidate ref
  points at.

## 4. Exit codes

**Unchanged, and deliberately so.** `deliver_chain` reports the
`DeliveryCode` of the phase that stopped it, so an existing consumer reads a
chain refusal exactly as it reads a single one. No new code is added: every
way a chain can fail is a way a phase can fail, and inventing a
`CHAIN_FAILED` would hide which phase failed behind a label.

One existing code becomes load-bearing in a new way. `CANDIDATE_EXISTS`
(`delivery.py:371-377`) refuses when `refs/satyrn/candidates/<id>/head`
already exists. Phases carry distinct contract ids, so a chain does not
collide with itself — but **re-running the same chain does**, and that
refusal is correct rather than inconvenient: silently overwriting a candidate
would destroy the evidence a prior run produced.

## 5. Data shapes

```
ChainReceipt:
    phases: tuple[DeliveryReceipt, ...]   # in order, stopping at the refusal
    candidate_ref: str | None             # the final phase's, else None
    accepted_refs: tuple[str, ...]        # each accepted intermediate, in order
    code: DeliveryCode                    # the stopping phase's code
```

`candidate_ref` is `None` whenever any phase refused. That is the
no-partial-chain rule expressed in the type rather than in a convention:
a caller cannot read a usable ref off a broken chain by accident.

`accepted_refs` is retained rather than discarded. SwiftStar's transaction
deletes intermediate worktrees and keeps only the last; this repository
grades from retained evidence, so the **refs** stay even though the
worktrees do not. Deleting them would make a mid-chain regression
unreproducible without re-running.

## 6. Test layout

Split on the line `CLAUDE.md` draws: the default tier uses no model, no
network, no subprocess, and Git spawns.

**Default tier** — the chain's decision logic against scripted phase results,
injected at the same seam the tests use, since a test seam is the extension
seam:
- three phases accepted in order, each basing on the previous accepted ref;
- a refusal at phase 2 stops the chain, with `candidate_ref is None` and one
  accepted ref retained;
- a refusal at phase 1 retains nothing;
- the chain's reported code is the stopping phase's, not a new one.

**Integration tier** (`@pytest.mark.integration`) — real Git, real refs:
- phase 3's worktree contains phase 1's and phase 2's committed files, which
  is the property this whole cycle exists for, and is the check that would
  pass vacuously if the chain silently based every phase on `HEAD`;
- an unresolvable `base` refuses rather than falling back to `HEAD`;
- a second run of the same chain refuses with `CANDIDATE_EXISTS`, leaving the
  first run's refs intact.

Every refusal above ships with the sibling success that proves it can pass.
The phase-3-contains-phase-1 check is the one to write first, because it is
the only one that fails if the base parameter is ignored entirely.

## 7. Out of scope

An orchestrator, a worker pool, parallel dispatch, automatic retry or repair,
generalized routing. Packet authoring, which is a main-agent skill and, for
Phase HP, measured as not established autonomously — 3/8 against 8/8 by hand,
with a remediated authoring prompt collapsing to 0/8, all no-op
(`local-ai-pi/ROADMAP.md:386-402`). Any change to the mutator, the runner, or
the contract shape. Deleting or rewriting existing receipts.

## 8. What HP3 will not have proved

That an implementer delivers anything useful, that chaining improves quality
or cost, or that the packet's declared scope matches the enforced scope —
that gap is `satyrn-evals`' HP4. HP3 proves one property: code folds forward
and a refusal stops the chain.
