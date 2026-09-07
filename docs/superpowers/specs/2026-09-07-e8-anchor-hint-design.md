# E8 — an `ANCHOR_MISSING` refusal that says what is actually there

**Status: REFUSED BY ITS OWN ACCEPTANCE TEST, 2026-09-07. Not built.**
The replay in §4 was run before any engine code was written, and the idea
failed it. The document is kept in full — the reasoning, the evidence, and
the refusal — because a correction is recorded, not edited away. See §6.

## 1. The evidence

Across the 499 transcripts `satyrn-evals` retains, **68.4% of every edit
call ever made produced no change** — 3,873 of 5,661, over 454 cells. By
arm: baseline 69.0%, envelope 90.8%, engine 60.9%. Recompute with
`satyrn-evals census RUNS_ROOT`.

What the model is told when an edit fails, verbatim and in the observed
proportions:

```
307x  "No changes made to models.py. The replacement produced identical content."   (pi's edit)
 89x  "Could not find the exact text in templates/complaints.html."                 (pi's edit)
 25x  "ANCHOR_MISSING: old_text was not found in app.py"                            (ours)
```

**None of them says what the file contains.** The model guesses again.

This engine already gets the *ambiguous* case right —
`old_text matches {count} locations in {path}; it must be unique`
(`src/satyrn_engine/mutation.py:157-159`) names the number, so the model
knows what to do next. Only the missing case is uninformative.

## 2. Why a hint should work, measured before building it

For every `ANCHOR_MISSING` in the retained Engine transcripts, I found the
next **successful** edit on the same file in the same cell and compared the
two `old_text` values:

```
ANCHOR_MISSING events with a later successful edit on that file:  133
median similarity of the failed anchor to the one that worked:   0.81
                                          >= 0.5 similar:      125/133  (94%)
75 further events never recovered on that file at all
```

**The model is already close; it is not byte-exact.** Whitespace, an
ellipsis, a paraphrased line. Handing back the nearest real text lets it
copy exact bytes.

The precedent is one day old and from this repository: renaming the
runner's refusal to name the allowed command verbatim
(`TEST_COMMAND_NOT_ALLOWED: only this exact command is allowed: "..."`)
took the model from never recovering to correcting **on its next call in
12 of 12 cells**. An informative refusal converts a loop into one retry.

## 3. The change

At `mutation.py:150-153`, the `count == 0` branch, before returning:

- Split the file into lines. Slide a window of the same line count as
  `old_text` across it. Score each with
  `difflib.SequenceMatcher(None, window, old_text).ratio()`.
- If the best score is **>= 0.5**, append to the refusal message the best
  window **verbatim** with its 1-based start line. Below the floor, append
  nothing and say so — never invent a hint.
- Bound the hint: at most **20 lines** and **2,000 bytes**, truncated with
  an explicit marker. A hint that floods the context is a new pathology.

The message becomes, for example:

```
ANCHOR_MISSING: old_text was not found in app.py.
The closest text in that file, at line 42, is exactly:
<<<
    if request.method == "POST":
        return redirect(url_for("complaints"), code=307)
>>>
Copy it byte for byte if it is the text you meant.
```

**No leak risk:** the file is already in the contract's `writable_paths`
and the model can `read` it. The hint tells it nothing `read` would not.

**Not proposed:** changing what counts as a match, fuzzy application of the
edit, or touching `ANCHOR_AMBIGUOUS`, which already works. The engine still
applies only an exact unique anchor. This changes a *message*.

## 4. Acceptance, without spending inference

The task base trees are in `satyrn-evals`
(`src/satyrn_evals/tasks/*/base/`), and the retained transcripts hold both
the failed `old_text` and the `old_text` that later worked. So:

- **Replay.** For each `ANCHOR_MISSING` that was a cell's *first* edit
  failure — the file is still at base then — run the hint against the base
  file. **The returned window must equal the anchor that later worked**, in
  the majority of the 133 recovered cases. Report the rate; do not tune the
  floor to improve it after seeing it. If the rate is low, the idea is
  wrong and the change should not ship.
- **Refusal sibling:** a file with nothing similar (best score below the
  floor) produces a refusal with no hint and says so.
- **Success sibling:** an exact unique anchor still applies, byte-identical
  to today, and `ANCHOR_AMBIGUOUS` is untouched.
- Bounds: a 5,000-line file yields a hint within both caps, marked
  truncated.
- Default tier only — this is string work on bytes already in hand, no
  subprocess.

## 5. What it will not fix

The 307 "identical content" no-ops are **pi's own edit tool**, in the
Baseline and Envelope arms, not ours. This change cannot reach them. Nor
does it touch the unexplained read lock. It addresses the engine's own
share: `anchor_refusal` was 12 events in V13f's Engine arm and 25 in
V11c's, alongside a 60.9% no-change rate on Engine edit calls overall.

## 6. Outcome: the replay refused it

Run before writing any engine code, exactly as §4 specifies.

```
first-failure cases replayable against a base file:   35
  hint == the anchor that later worked (exact):        1/35   (3%)
  hint >= 0.8 similar to the anchor that worked:      13/35
  no hint offered (best window below the 0.5 floor):   1/35
  hint would have handed over usable text:            14/35   (40%)
```

§4 required the returned window to equal the anchor that later worked **in
the majority** of cases. It does so in **3%**, and reaches merely-usable in
40%. **The change does not ship.** The floor was not tuned, the window
heuristic was not adjusted, and no second criterion was invented after
seeing the number — that is the move this project's discipline exists to
prevent.

### Where the reasoning went wrong

The motivating statistic was that a failed anchor is **median 0.81 similar
to the anchor that later worked**. That is a fact about two strings the
*model* produced. It does not imply that **the file's nearest window to the
failed anchor is that successful anchor** — a source file contains many
similar-looking regions, and the nearest window is usually a different one.
Two different claims; §2 treated the first as evidence for the second.

### What is not concluded

That informative refusals do not help — `TEST_COMMAND_NOT_ALLOWED` moved
12 of 12 cells and that stands. Only *this* way of computing the hint, on
*this* corpus, under *this* criterion, is refused.

Three limits of the test itself, recorded so a future proposal can improve
on it rather than re-run it: only 35 of the 133 recovered events had a base
file and a first-failure position; the criterion assumes the model's later
successful anchor is what it originally meant, which is an assumption; and
a same-line-count window cannot find a match of a different height. **A
future proposal must predeclare a different criterion before measuring, not
adopt one of these after reading this page.**
