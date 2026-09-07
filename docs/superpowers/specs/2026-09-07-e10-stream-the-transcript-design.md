# E10 — publish the transcript by exclusive creation, and stream into it

**Status: proposed 2026-09-07, option 1 confirmed by the maintainer.**

## 1. Why

`satyrn-evals` runs a repeated-call spending rule that tails the transcript
as it is written (`workspace.py:755-780`). It has **never applied to the
Engine arm**: measured, `REPEAT_LIMIT` fired on 18 of 337 Baseline cells,
28 of 49 Envelope cells, and **0 of 147 Engine cells**. This engine spools
to `temporary_parent/transcript.jsonl` and publishes to the artifact path
only after pi exits (`attempt.py:655,687`), so the file does not exist
while the tripwire watches.

Consequences already on record: every batch claiming the rule was "on for
every arm" was wrong for one arm, and an Engine loop cell runs to the 900 s
deadline emitting hundreds of calls where a pi loop cell is cut at ten —
so cross-arm call and token comparisons are confounded, differentially by
arm. Verdict counts are unaffected; grading reads the patch.

An agent that runs for fifteen minutes should be observable while it runs,
by anything, not only by this tripwire.

## 2. The change, and what happens to the guarantee

Today the transcript is written to a spool and published with `os.link`
from a temporary inside the pinned parent (`attempt.py:982-996`). `link`
is atomic and **fails if the destination exists**, which is what enforces
"a destination created after preparation is never overwritten"
(`tests/test_attempt.py:1327`).

**The guarantee moves earlier; it is not weakened.** At preparation, create
the transcript destination through the already-pinned parent descriptor
with `O_CREAT | O_EXCL | O_WRONLY`. Exclusive creation fails if anything
else made that path, which is the same proof `link` was giving — obtained
before the run rather than after it. pi then writes directly into that
descriptor, so the file is complete-as-of-now at every moment.

Unchanged: the parent is still validated and pinned, its identity is still
re-checked against the held descriptor, symlinks are still never followed,
and **every other artifact still publishes by link**. Only the transcript,
which is the only streamed artifact, changes.

## 3. Consequences to state plainly

- **A crashed or failed run now leaves a partial transcript** where it
  previously left none. Downstream this reads as `TRANSCRIPT_EMPTY` rather
  than `TRANSCRIPT_MISSING` for a run that produced nothing, and as a
  partial transcript rather than nothing for a run that died mid-way. Both
  are refusals in `satyrn-evals`, and a partial transcript is more
  diagnostic than an absent one — but it **is** a change in what a failed
  Engine cell records, and any comparison spanning it must say so.
- stdout still receives the transcript, now copied from the destination
  after the run rather than from a spool.

## 4. Test layout

Default tier where possible; the streaming property needs the marked
integration tier because it needs a real child process.

- **Refusal:** a transcript destination that already exists at preparation
  is refused, and the run does not start — the guarantee, moved earlier.
  Its **sibling success**: an absent destination is created and written.
- **The streaming property, which is the whole point:** with a child that
  writes a line and then blocks, the destination is readable and contains
  that line **while the child is still running**. A test asserting only the
  final content would pass today and prove nothing.
- The parent-identity re-check, symlink refusal, and every other artifact's
  link-publication are pinned unchanged.
- A child that exits without writing leaves an empty transcript, not a
  missing one; recorded as the §3 change.

## 5. Acceptance

Unit and integration gates green, plus an end-to-end check in
`satyrn-evals`: run one Engine cell with `--max-repeated-calls 2` and
confirm the cell records `REPEAT_LIMIT`. Under today's engine that is
impossible for any limit, so it is a direct test of the defect being
closed — not a proxy for it.
