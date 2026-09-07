# E7 — a model-invocable test runner

**Status: proposed 2026-09-06; the two design forks confirmed by the
maintainer.** Implementation follows this document.

## 1. Why, on evidence

`satyrn-evals` V13d ran three arms on one repair task, `n=12` each,
interleaved (`~/satyrn-smokes/2026-09-06-v13d-232118/RESULT.md`):

| arm | tools | successes |
|---|---|---|
| baseline | `read,bash,edit,write` | **12/12** |
| envelope | `read,edit` (bare Pi) | **4/12** |
| engine | `read,edit` + engine | **6/12** |

**Removing `bash` and `write` costs 8 of 12 successes** (one-sided Fisher
p = 0.00067). The engine's machinery recovers 2 of those 8, which is not
distinguishable from chance at this size. Baseline's `bash` calls are
overwhelmingly `pytest`: it runs the suite, reads the failure, edits.

**What this does not claim.** The read-lock pathology that motivated the
investigation is **unexplained** — V13d's lock measure returned its
predeclared "underpowered" outcome, and on `misleading-locus` the sign was
reversed (Baseline locked 5/12 against Engine's 0/12). A runner is
justified by the outcome cost of the restricted surface on this task, not
by any mechanism claim about locking.

## 2. Surface

A contract may declare an optional `test_command`:

```yaml
id: ...
task: ...
writable_paths: [app.py]
test_command: ["python", "-m", "pytest", "tests/"]   # optional
```

- **Absent** — no tool is registered; the surface is `read,edit`, byte-
  identical to today. This is the default and every existing contract keeps
  working unchanged.
- **Present** — the engine registers a `run_tests` tool alongside `edit`.

**The model chooses nothing.** `run_tests` takes **no parameters**: its
JSON Schema is a closed object with no properties. The command is the
contract's, verbatim. An argument the model could supply is a shell by
another name, and is refused at the schema.

The prompt gains one sentence only when the tool is registered: that the
suite can be run with `run_tests` and that it takes no arguments.

## 3. Data shapes

A third protocol operation beside `check` and `replace`:

```
request  {version, operation: "test", repo, contract}
response {version, ok, code, message, result: {
            exit_code: int, output: str, truncated: bool, timed_out: bool}}
```

`output` is the **last 8192 bytes** of combined stdout/stderr, decoded
lossily, prefixed with an explicit truncation marker when `truncated`.
Execution is `subprocess.run` with a list argv and **no shell**, `cwd` set
to the repo, `stdin` at `DEVNULL`, and a **120 s** timeout; a timeout
returns `timed_out: true` with whatever output was captured, not an
exception.

## 4. Exit codes and refusals

The tool never raises to the model. Every failure is a named result:

| cause | shape |
|---|---|
| command not found / not executable | `ok: false`, `code: TEST_COMMAND_UNAVAILABLE` |
| timeout | `ok: true`, `result.timed_out: true` |
| non-zero exit | `ok: true`, `result.exit_code: N` — a failing suite is a *result*, not an error |

A failing test suite is the normal, useful case; it must not read as a tool
error, or the model will treat it as something to retry rather than act on.

## 5. Non-goals

Network isolation (not enforceable cheaply here — stated as a limit); a
model-supplied command or arguments; running anything other than the
contract's command; parsing pytest output into structured counts; the
`edit` tool's behaviour; file creation (still refused — a separate item).

## 6. Test layout

**Default tier, no subprocess** — contract parsing of `test_command`
(accepted with a valid list; refused for a non-list, an empty list, a
non-string element, an empty string), protocol request/response
parse/serialise round trips, the tail-truncation function as a pure
function, prompt text with and without the field, and `build_pi_command`
proving the tool list is unchanged and the extension is added only when
declared.

**Integration tier, marked and out of CI** — a real temporary repo with a
passing and a failing suite, asserting exit code, truncation on large
output, and the timeout path. `tests/test_runner.mjs` for the TS tool:
registration only when declared, empty parameter schema, and a refusal
carried as a result.

**Every refusal has its sibling success** (`CLAUDE.md`).

## 7. Acceptance

- A contract without `test_command` produces a byte-identical pi argv to
  today's, proven by an assertion on the exact tuple.
- A contract with one registers `run_tests`, whose parameter schema admits
  no properties.
- A failing suite returns `ok: true` with a non-zero `exit_code` and the
  assertion text in `output`.
- The planted process-spawning tripwire is untouched and the default tier
  still spawns nothing.
- `uv run pytest -q`, `uv run pytest -q -m integration`, every `.mjs`
  suite, `uv run ruff check`, and `just docs` all exit 0.

## 9. Correction, 2026-09-06: the parameterless tool was never invoked

A smoke of four uncounted cells found **zero** invocations of `run_tests`,
including one cell that succeeded without ever running the suite. The
wiring was correct — the contract carried `test_command`, the prompt
named the tool, there were no load errors — the model simply never reached
for a tool it had no prior for. A model's prior for a tool named `bash`
is that it takes a `command` argument; a closed, parameterless schema does
not match that prior, so the model apparently didn't try it.

**The fix does not weaken the restriction — it moves where the restriction
is enforced.** The tool is renamed to `bash`, and its schema now accepts a
required `command: string`, satisfying the model's prior at the schema
layer so a well-formed call actually reaches `execute`. What may *run* is
unchanged: `execute` forwards the model's `command` to the Python core,
which compares it against the contract's `test_command` (equality after
stripping leading/trailing whitespace from the model's string, joined by
single spaces on the contract's side — nothing fuzzier) and, on a match,
runs the contract's own argv list exactly as before (`shell=False`, never
the model's string). A mismatch is refused with a new named code,
`TEST_COMMAND_NOT_ALLOWED`, whose message names the one command that is
allowed, verbatim, so the model's next call can succeed.

**Why the check could not stay at the schema.** An incident the same day
showed that a schema-level rejection is invisible to every extension hook:
pi validates a tool call against its JSON Schema in `prepareToolCall`,
*before* `beforeToolCall` runs, and a schema-rejected call takes the
`kind: "immediate"` path, which skips `finalizeExecutedToolCall` — the
only site that fires `afterToolCall`. A model that guesses an argument the
schema forbids is refused in a way no hook, transcript, or grader can see.
Observable strictness has to live inside `execute`, never in the schema —
so the restriction moved from "no parameters accepted" to "only one
command argument value is ever actually run," enforced in code that a
hook can see run.

This section is a correction, not a replacement: sections 2–4 above
describe the original (unused) design and are retained as the record of
what was tried first and why it didn't work.
