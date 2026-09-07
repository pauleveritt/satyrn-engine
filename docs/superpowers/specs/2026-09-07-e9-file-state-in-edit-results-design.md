# E9 — tell the model what the file says, not what its hash is

**Status: proposed 2026-09-07, confirmed by the maintainer.** Three changes
to the mutator's messages. The engine's matching rule is untouched: still
one exact, unique anchor.

## 1. The evidence

Across 499 retained transcripts, **2,872 edit calls failed**. Where the
model's view of the file came from, measured:

```
file never read in that cell:                          0   (0%)
edit issued 10+ tool calls after the last read:    1,896  (66%)
edit issued within 2 calls of a read:                647  (23%)
the model's own edit landed more recently than
  its last read of that file:                        499  (17%)
```

The model always reads first. **It is working from a stale picture**, and
in a sixth of cases the file changed because of its own earlier edit.

The tool causes this. On success the mutator returns
`Replaced app.py; sha256=2e4fc064…` (`packages/engine/mutator.ts:262`) —
a hash. That is the model's most recent information about the file, and it
carries no file state at all, so its freshest *textual* view stays the
pre-edit `read`.

Two more facts the mutator currently withholds or misstates:

- **182 of 208** `ANCHOR_MISSING` events are an edit that **already
  landed**, re-sent with its original anchor. All 208 follow a successful
  `OK` on that same file; none is first-on-file.
- **386 edits across 42 cells** had `oldText == newText` and were reported
  **`OK`, "Replaced"** — `mutation.py:145-165` has no identity check, so
  the engine tells the model it changed something when it wrote identical
  bytes.

## 2. The three changes

**(a) A successful edit returns the post-edit region.** Replace the hash
line with the changed text and **3 lines of context** either side, each
line prefixed with its 1-based number, capped at **40 lines / 4,000 bytes**
with an explicit truncation marker. Keep the sha256 in `details` for the
protocol; it leaves the model-facing text.

**(b) A missing anchor says whether the edit already landed.** In the
`count == 0` branch, test whether `new_text` occurs in the file. If it
does, refuse with a new code `ANCHOR_ALREADY_APPLIED` naming the 1-based
line where it starts. If it does not, the message is unchanged. This is a
substring test — a **fact**, not a similarity guess. Fuzzy anchor matching
is recorded as refuted in the harvest index and is not proposed.

**(c) An identity replacement is refused.** `old_text == new_text` returns
a new code `NO_CHANGE_REQUESTED` before the file is touched. Reporting a
no-op as `Replaced` is active misinformation.

## 3. Why messages and not guidance

The harvest index records that guidance did not move this attractor, and
that "the miss is semantic, not typographic". All three changes state
**facts about file state**: here is the text now, your change is already
there, you asked for nothing. The one intervention that has worked in this
repository — `TEST_COMMAND_NOT_ALLOWED` naming the allowed command
verbatim, which took 12 of 12 cells from failing to correcting on the next
call — is the same category.

## 4. Non-goals

Fuzzy or nearest-window matching (refused as E8); changing what counts as a
match; a line-range or diff-based primitive (a larger question, not this);
pi's own edit tool, which serves the Baseline and Envelope arms and is out
of reach.

## 5. Test layout

Default tier, no subprocess. Each refusal with its sibling success:

- (a) the success message contains the post-edit region with line numbers
  and context; a large file is truncated within both caps and marked; the
  sha256 is still in `details`.
- (b) an anchor absent while `new_text` **is** present returns
  `ANCHOR_ALREADY_APPLIED` with the correct line; an anchor absent while
  `new_text` is **also** absent returns the unchanged `ANCHOR_MISSING`.
- (c) `old_text == new_text` returns `NO_CHANGE_REQUESTED` and the file is
  **unmodified**; a genuine replacement still applies byte-identically.
- `ANCHOR_AMBIGUOUS` and the revision checks are untouched, pinned.
- Both new codes round-trip the protocol and appear in
  `ENGINE_REFUSAL_CODES`.

## 6. Acceptance

Unit gates as above, plus: replaying the corpus, **(c) refuses all 386
identity edits and (b) reclassifies 182 of 208 `ANCHOR_MISSING`**. That is
true *by construction* and is a wiring check, **not evidence the change
helps**.

**Whether it helps is behavioural and must be predeclared before any
batch:** the criterion is the **re-issue rate of already-applied edits**
per cell, on the same task and `n`, against the pre-change rate. It cannot
run until the repeated-call tripwire is fixed to see the Engine arm
(`satyrn-evals` `BACKLOG.md`) — that blocks the next batch by the
fix-round rule.
