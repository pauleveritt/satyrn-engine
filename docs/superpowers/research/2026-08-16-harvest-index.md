# Harvest index: what goes where

**Purpose.** The valuable output of this repository is the record of plausible
ideas that failed. This index maps each lesson to the repository that needs
it, so the new projects pull rather than guess.

**Indexed by symptom, not by mechanism.** A prior cycle spent a full spec,
build, pilot and research record on a premise two committed documents already
refuted — because the record filed the fact under *what a child inherits*
while the cycle was searching for *how to reach a child*. Keep the symptom in
the heading.

**Nothing here is code to transplant.** Each row names a behavior to re-earn
and the incident that justifies it.

---

## To satyrn-engine

### "The extension loaded but nothing happened"

- `--extension` pointed at a **directory** loads nothing. No error, no stderr,
  exit 0, other extensions still loading. The only symptom was a much later
  "Tool subagent not found", and the run still graded accepted.
- An entry appended during `session_start` is **dropped in print mode**: print
  mode attaches its JSON subscriber only after `bindExtensions()` returns,
  while `bindExtensions()` emits `session_start` before returning. Eighty
  recorded runs produced nothing observable.
- Project-local `.pi/extensions/` is **not loaded by a child-style
  invocation**, and `--approve` does not change it.
- Source: `docs/superpowers/research/2026-08-03-phase3-cycle2-pi-gotchas.md`.

### "The /implement command vanished"

- Installing an extension **globally and passing the same file with `-e`**
  makes pi register the command twice and suffix it (`/implement` becomes
  `/implement:1`), so the **plain name stops dispatching** and the prompt
  reaches the model as text. Verified 2026-08-16: global-only intercepts;
  global plus `-e` does not. The fix is to pick one load path — the
  documented global install (`cp` to `~/.pi/agent/extensions/`), not `-e`
  for a file that is already installed.
- Source: pi docs `extensions.md` (`registerCommand` suffixes duplicates);
  observed live in this repository's E2 adapter.

### "The child behaved differently than the parent"

- A delegated child loads **user-scope** resources unconditionally. Child
  transcripts showed `ls -R` returning the output of a user's own shell
  wrapper. `PI_CODING_AGENT_DIR` is the only seam that reaches it, because the
  spawning extension passes no environment of its own.
- Setting it took timeouts from 2/6 to 0/6, worst repeated command from 178 to
  5, median transcript from 9.63 MB to 0.49 MB, peak context from 2.8M to 91k.

### "The model edited the file and nothing changed"

- The no-op attractor: five consecutive edits whose `oldText` was
  **byte-identical** to `newText`, each matching uniquely, engine reporting
  "changed lines=0" as success, zero bytes written, run killed by the clock.
- The discriminator: handed a change verbatim, the model reproduced 2,031
  characters byte-perfectly 40/40 across two channels. Made to *derive* one,
  it emitted byte-identical no-ops. **It is a derive-versus-apply failure, not
  a fidelity or length limit.**
- Refuted and not to be re-derived: that the attractor lives in the JSON
  tool-argument path (a matched tool-call probe returned 20/20 clean); that
  "keep oldText small" guidance helps; that fuzzy anchor matching would help
  (scores 0 against the real failure — the miss is semantic, not typographic).

### "The model deleted something it was asked to add"

- In a recorded four-run batch, three runs replaced the existing `/about`
  route when asked to *add* `/contact`. Three acceptance tests failed from one
  deletion. The anchor matched perfectly — the failure had moved from
  mechanics to intent.
- The fix belongs in the mutation engine, **not** a pre-edit guard: a
  contract-blind guard refuses contract-authorized renames that the
  contract-aware layer would admit. Such a guard was built, shipped, and
  removed for exactly that reason.

### "It ran the same command hundreds of times"

- One run executed 261 tool calls, **245 of them the identical `ls -R`**,
  against a genuinely empty workspace, and wrote nothing. Every one of those
  calls *succeeded*, so a breaker counting only failures would never fire.
- The loop breaker keys on `(toolName, arguments)`; a blocked call never
  enters the window, so a retrying model stays blocked.
- **Churn is a different concept** and mostly slips past: the same target
  rewritten with differing content (27 versions of one template; 19 and 10 of
  one file). Keep the words separate.

### "The guards did nothing"

- Across 24 recorded runs on two suites, the guards fired **zero** times, with
  loading verified by extension digest. A composite pipeline's 0/16 → 13/16
  improvement does **not** credit them; the effect lived elsewhere.
- Carry this as calibration, not as a reason to drop them: it is why they stay
  TypeScript and out of the roadmap.

### "The install instructions were wrong"

- Six documentation pages promised a two-file `cp` install after
  `orchestrator.ts` gained a single local import. Any change adding a local
  import to an installable file must re-verify the install docs, not just the
  code.

---

## To satyrn-evals

### "The number looked clean and was fabricated"

Four silent-zero incidents. In none of them did the number look wrong:

| Observed | Read as | Actually |
|---|---|---|
| 900.018s, empty tree, exit −15 | dead server → void and retry | model stalled; server verified up both sides |
| 0/8 accepted, 0.4s each | "authoring is the bottleneck" | HTTP 404 — the model was not served |
| 19s, no writes | "failed fast and cleanly" | burned all 16 turns on blocked calls, which are nearly free |
| `wrote=False` every run | "no difference between arms" | the runner watched a hardcoded file the task never writes |

**The rule:** a suspiciously clean zero, a suspiciously round duration, or a
result arriving faster than the mechanism allows is an instrument fault until
the instrument is checked. No error path may emit a verdict — raise, and
record "unavailable" with a distinct exit code.

### "The model server was up but the model was gone"

- `/v1/models` enumerates from configuration and advertises models whose
  weights are absent. Verify with a **real completion**, not a model listing.
- A model that authored the best artifacts later ceased to exist on the
  machine, leaving a permanent confound in everything measured against it.

### "The grader accepted a broken solution"

- Predecessor graders were defeated by `addopts = --collect-only` and an
  import-time `os._exit(0)`. The verdict must come from a test hook, not
  stdout and not an exit code.
- A model once copied a target implementation out of a **stale sibling
  workspace** and deleted the traces. Detect leftover workspaces; keep a
  cheap transcript tripwire; record patch-versus-reference overlap without
  ever rejecting on it.

### "The grader rejected a correct solution"

- The task spec told the model to write a smoke test, so a *correct* solution
  produced extra test files and broke an executed-versus-expected count.
  Copying only allowlisted paths into a fresh grading directory fixes it.
- When a target diff **changes** an existing assertion, the base test is
  guaranteed to fail against any correct fix, because the model may only write
  source while tests stay at base. This caused a false 0/4.

### "The comparison showed nothing"

- Of four tasks in a 64-attempt pre-registered batch, **one** discriminated;
  two were ceiling-tied and one floor-tied. Validity is not discriminating
  power — hence the baseline probe.
- Two prompt variants of one suite differ by 27 words and by 0/16 versus
  15/16. **Facts work; rules of conduct do not** — across five interventions,
  the three supplying a missing fact worked and the two supplying a rule of
  conduct did not.

### "The timing number was wrong"

- Two published wall-clock figures were retracted in one night, both because
  arms ran as contiguous blocks on a machine whose load varied. One batch
  swung 3× internally. **Counts survive; seconds do not.** Interleave arms.
- A separate cost headline was published inverted — "cheaper" when the truth
  was 8.24× context — because telemetry read only the parent's events.

### "The framework outgrew its purpose"

- A checker framework reached 862 lines hosting five criteria; **one
  fifteen-line rule survived** pre-registered delete rules. Two criteria died
  from assuming a single reference patch is *the* solution, refuted on disk by
  a task solved correctly a different way. One died from instability: a
  known-good packet was rejected in three of four prompt configurations, each
  time for a different pretext.
- **A content criterion needs a stability-under-perturbation test as a gate on
  itself** before it earns model time.

---

## To both

- **Verify, don't assert.** Six numbers were published wrong in a single day,
  in prose no test read. Ask: *did this number come from a command whose
  output I can point to?* Memory is not a source.
- **Non-vacuity.** Ask what else could make this test pass. Most of this code
  tests a rejection mechanism, and rejection is the default outcome of most
  failures.
- **Concept budget**, and the recorded lapse: it fell twelve cycles behind and
  was paid in one lump. The budget's own test deliberately does *not* demand
  it keep pace, because that would pressure people to invent vocabulary to
  satisfy a test.
- **Repository weight.** A 104.7 MiB corpus that no supported code path read
  made a first checkout 123 MiB to reach the 10 MiB that was the project.
- **Docs are excluded from formatters.** Reformatting them edits preserved
  research records.
- **A correction is recorded, not edited away.** Superseding banners over
  rewrites; retractions kept beneath the text they retract.

---

## Source

All of the above is recorded in `github.com/pauleveritt/local-ai-pi` at commit
`c74c31f`. The design conversation is
`docs/superpowers/research/2026-08-16-two-repo-rewrite-and-python-engine.md`;
the failure-mode catalogue is `docs/evals/slm-struggles.md`; the Pi behaviors
are `docs/superpowers/research/2026-08-03-phase3-cycle2-pi-gotchas.md`.

That repository is **evidence, not source**.
