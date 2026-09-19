# Using satyrn-engine

The {term}`engine` ships a command-line entry point, `satyrn-engine`, that parses
and validates a {term}`contract`. The `check` operation only lints the
repository path: on every path it makes no model calls and starts no processes.
The `deliver` operation deliberately starts Git and one caller-supplied command
inside a separate worktree. It requires a POSIX system and Git 2.36 or newer;
Git 2.36 introduced the NUL-delimited `git worktree list --porcelain -z`
format needed to handle every valid worktree pathname safely.

## CLI Usage

Run the engine from the command line with the `check` subcommand:

```console
satyrn-engine check --repo REPO CONTRACT
```

The `derive` subcommand writes a contract from a developer's request and the
repository, rather than requiring one to be hand-written:

```console
satyrn-engine derive --repo REPO -- REQUEST...
```

The command accepts exactly two things:

| Argument | Kind | Meaning |
|----------|------|---------|
| `--repo REPO` | required option | the {term}`working tree` root. It must exist and be a directory. |
| `CONTRACT` | required positional | path to the contract file. It must exist and be readable YAML. |

A **contract** is a YAML document whose top level is a mapping with two
required fields, both non-empty strings, plus an optional mutation scope:

| Field | Meaning |
|-------|---------|
| `id`   | a stable identifier for the contract (names receipts and candidates in later phases) |
| `task` | the description of the change to make |
| `writable_paths` | optional list of non-empty workspace-relative patterns; an omitted list permits no E4 mutation |
| `preserve` | optional list of tracked test-file paths restored from the base before every `self_test` run and before validation; never writable |
| `checks` | optional list of paths under `checks/` restored and run alongside `preserve`, the same way |
| `token_budget` | optional output-token budget; when declared, `deliver` defaults `token_limit` to it and the receipt's `budget.state` becomes `token_exhausted` if the model spends past it |
| `turn_budget` | optional turn-count budget enforced the same way as `token_budget` |

Patterns use Python `fnmatch` semantics, including `*` crossing `/`. Unknown
extra fields remain ignored, so later phases can extend a contract by adding
keys rather than changing the parser.

### Exit codes

`check` either accepts the contract (exit `0`, no output) or refuses it
with a named cause and a one-line message on stderr:

```text
satyrn-engine: <CAUSE>: <detail>
```

The {term}`exit code`s are a stable contract:

| Code | Name | Meaning |
|------|------|---------|
| `0` | `OK` | the contract was accepted |
| `2` | `USAGE` | malformed command line (argparse's own error) |
| `3` | `CONTRACT_UNREADABLE` | `CONTRACT` path is missing or not a readable file |
| `4` | `CONTRACT_INVALID_YAML` | `CONTRACT` is not valid YAML |
| `5` | `CONTRACT_MISSING_FIELD` | a required field is absent, empty, or the wrong type |
| `6` | `REPO_UNAVAILABLE` | `--repo` is missing or not a directory |
| `7` | `INVALID_REQUEST` | malformed versioned protocol input |
| `8` | `NO_CANDIDATE` | accepted delivery produced no candidate |
| `9` | `MUTATION_REFUSED` | accepted replacement was safely refused; JSON carries the exact cause |
| `10` | `ATTEMPT_FAILED` | accepted model attempt failed after preparation; artifacts are preserved when possible |
| `15` | `ATTEMPT_OK_FORWARD_LOST` | (`attempt` only) the model attempt succeeded, but its live budget tee (`forward`) was lost for good mid-run; `deliver` treats this the same as exit `0` for candidate creation and marks the receipt's `budget.live_counter` as `"lost"` |

Exit code `1` is deliberately unused: Python reports an uncaught internal
error as `1`, so reserving it keeps a crash distinguishable from a {term}`refusal`.

### Example

From a checkout, `uv sync` installs the engine into the project
environment; `uv run satyrn-engine ...` then runs it with no further
install step. Write a contract:

```yaml
# greeting.yaml
id: greeting
task: Replace the greeting text
writable_paths:
  - greeting.py
```

Run `check` against the current checkout:

```console
$ uv run satyrn-engine check --repo . greeting.yaml
$ echo $?
0
```

A valid contract is accepted silently. A refusal names its cause and
returns its code:

```console
$ uv run satyrn-engine check --repo . missing.yaml
satyrn-engine: CONTRACT_UNREADABLE: cannot read contract missing.yaml: [Errno 2] No such file or directory: 'missing.yaml'
$ echo $?
3
```

### Every exit code

The committed fixtures exercise the distinct {term}`exit code`s in one
pass:

```console
$ uv run satyrn-engine check --repo . tests/fixtures/contracts/valid.yaml; echo "valid -> $?"
valid -> 0
$ uv run satyrn-engine check --repo . tests/fixtures/contracts/invalid.yaml; echo "invalid -> $?"
satyrn-engine: CONTRACT_INVALID_YAML: invalid YAML in tests/fixtures/contracts/invalid.yaml: ...
invalid -> 4
$ uv run satyrn-engine check --repo /nonexistent tests/fixtures/contracts/valid.yaml; echo "repo -> $?"
satyrn-engine: REPO_UNAVAILABLE: repo is not a directory: /nonexistent
repo -> 6
$ uv run satyrn-engine check --repo . tests/fixtures/contracts/missing-field.yaml; echo "field -> $?"
satyrn-engine: CONTRACT_MISSING_FIELD: missing required field 'task'
field -> 5
```

## Deliver one candidate

On POSIX systems, `deliver` runs one command in a detached
{term}`worktree isolation` directory at the repository's exact `HEAD`:

```console
satyrn-engine deliver --repo REPO [--timeout SECONDS] CONTRACT -- COMMAND [ARG ...]
```

`REPO` must be the clean root of a normal non-bare Git working tree with at
least one commit. A symlink to the root and a linked-worktree root are accepted;
a subdirectory is not. `COMMAND` is an exact argument vector after the literal
`--`, not a shell string. The default timeout is 30 seconds.

For example, with the `greeting.yaml` contract above:

```console
$ uv run satyrn-engine deliver --repo . greeting.yaml -- \
    python -c 'from pathlib import Path; Path("greeting.txt").write_text("hello\n")'
{"version":1,"outcome":"candidate-created","code":"OK",...}
$ git show refs/satyrn/candidates/greeting/head
```

The engine never merges or applies the result. After review, remove this E3
candidate explicitly with:

```console
git update-ref -d refs/satyrn/candidates/greeting/head
```

### Receipt and exit status

Every accepted delivery operation writes exactly one UTF-8 JSON {term}`receipt`
and newline to stdout. Command stdout and stderr go to the engine's stderr, so
stdout stays machine-readable. The receipt always contains these fields:

| Field | Meaning |
|-------|---------|
| `version` | receipt schema version; E3 emits `1` |
| `outcome` | `candidate-created`, `discarded`, or `refused`; derived from `code` |
| `code` | authoritative closed vocabulary, such as `OK`, `NO_CHANGES`, or `REPO_DIRTY` |
| `message` | always-present human-readable detail; `code` remains authoritative |
| `contract_id` | parsed contract id, or null when parsing failed |
| `repository` / `base_commit` | normalized input path and captured Git commit |
| `candidate_ref` / `candidate_commit` | proposed ref and created commit when available |
| `changed_paths` | UTF-8 paths sorted by raw bytes; `[]` means known empty, null means unavailable |
| `command_exit` | direct command status; null when it never started or timed out |
| `worktree_path` | retained cleanup path requiring operator action, otherwise null |
| `size_refusal` | the advisory medium-class refusal text `derive` printed, or null when the request was within class or no request was measured |
| `validation_output_bytes` | byte length of the self-test output captured for this delivery, or null when none ran |

Success exits `0`. Contract refusals retain codes `3`–`6`. All other handled
results that publish no candidate exit `8` (`NO_CANDIDATE`); automation reads
the receipt's `code` for the exact reason. CLI syntax errors still exit `2`,
and an uncaught engine error remains exit `1` with no invented receipt.
If the caller closes stdout before reading the receipt, the engine also exits
with the reserved abnormal status `1` and suppresses a broken-pipe traceback;
candidate publication may already have completed, so callers must inspect the
candidate ref before retrying.

The complete delivery result-code vocabulary is:

| Code | Meaning |
|------|---------|
| `CONTRACT_UNREADABLE`, `CONTRACT_INVALID_YAML`, `CONTRACT_MISSING_FIELD`, `REPO_UNAVAILABLE` | inherited contract or repository-path refusal |
| `REPO_NOT_GIT`, `REPO_DIRTY` | source state cannot be used |
| `INVALID_CANDIDATE_ID`, `CANDIDATE_EXISTS` | candidate identity cannot be created |
| `COMMAND_UNAVAILABLE`, `COMMAND_TIMEOUT`, `COMMAND_FAILED` | command did not complete successfully |
| `COMMAND_CHANGED_HEAD`, `NO_CHANGES` | command did not yield an acceptable changed tree |
| `GIT_FAILED`, `CLEANUP_FAILED` | engine-owned Git or cleanup operation failed |
| `OK` | candidate ref was created atomically |

`outcome` and the numeric exit status are derived from `code`; callers cannot
construct contradictory combinations such as `OK` plus `refused`.

If `CLEANUP_FAILED` reports a registered or conservatively retained worktree,
recover it with:

```console
git worktree unlock PATH
git worktree remove --force PATH
git worktree prune
```

If the retained path is only the temporary parent after Git already removed
the worktree, delete that directory after inspecting it.

Git cleanup is deliberately withheld when timeout teardown cannot confirm both
that the process group is gone and that the direct child was reaped. This keeps
a possibly live command from racing worktree removal; the receipt reports the
retained worktree for operator inspection.

### Trust boundary

Worktree isolation protects the caller's working tree, index, branch, and
`HEAD` from ordinary command writes. It is not a container or filesystem
sandbox. `COMMAND` can write arbitrary absolute paths, mutate shared Git state,
or deliberately escape its POSIX process group; E3 does not prevent those
actions. Therefore it accepts only a trusted command expected to run
synchronously. Git is an explicit runtime requirement. E4's writable-path and
revision rules apply only when the attempt uses its bounded replacement tool;
E5 connects that tool to one model attempt and E3 delivery. Command output is
spooled to temporary storage
to bound engine memory and avoid a descendant-held pipe; E3 does not impose a
byte quota on that storage, just as it does not limit files written by the
trusted command itself.

### Justification status (2026-09-15)

The loop breaker, scope guard, symbol preservation, command bounds (guard 4),
carried tests, `self_test` (its output detection and the completion gate) were
designed from evidence gathered before release one's clean harness. On live
isolated cells, guard 4 fired as designed and the `self_test` detection
replaced ad-hoc pytest runs, but none of these components has been shown to
change outcomes, and the completion gate never fired in cells that end at the
budget rather than stopping early. Their justification is re-opened for
release two; see
`satyrn-evals/docs/superpowers/specs/2026-09-15-release-one-outcome.md`.

## `/implement`

Inside Pi with the package installed (`pi install <engine>/packages/engine`,
`SATYRN_ENGINE_REPO` and `SATYRN_MODEL` set):

    /implement add --check to src/app/cli.py

derives a contract from the request and the repository — `writable_paths`
from the files, directories and new files named in the request's `Files:`
block alone, when the request has one; only a request with no `Files:` block
falls back to tokenizing the whole request (naming a non-test `.py` file
also makes its test file, `tests/test_<stem>.py`, writable, unless that test
already exists — it is then preserved instead). Restricting to the `Files:`
block keeps derive from admitting a path the grader would reject just
because the word appeared later in the prompt.
When the named tokens yield no writable path other than test files or test
directories (including naming nothing at all), `writable_paths` falls back
to the repository's top-level entries instead: every top-level tracked file
that `select_carried` would not carry (`preserve`, `checks`, a tracked
`conftest.py` at any depth, or a tracked infrastructure file such as
`pyproject.toml`), plus `<dir>/*` for every top-level tracked directory
except `checks` — so a request that only names `tests/` (or a preserved
test file) still leaves the model free to touch source. A directory whose
only tracked files are test files and test-support files (`conftest.py`,
`__init__.py`, a pytest config) still counts as naming nothing but tests.
A named source file or directory behaves exactly as before, with no
fallback; an empty repository (no tracked files at all) is still refused.
`test_command` from `[tool.satyrn] self_test` in `pyproject.toml`
or the default `uv run python -m pytest -q`, `preserve` (tracked test files
under `tests/`) and `checks` (`checks/`), budgets of 32,000 output tokens and
48 turns — writes it under `.git/satyrn/contracts/<id>.yaml`, and shows it. A
repository with no `pyproject.toml` is refused.

`derive` also measures the request against the medium class: at most two
non-test paths in the `Files:` block, and at most ten symbols named in an
`Interfaces:` block's `Produces:` lines. Above either bound it still writes
the contract — the refusal is advisory, not a gate — and prints a refusal to
stderr naming the count and what was over, exits `0`, and records the
refusal text on the receipt's `size_refusal` field. The refusal text never
enters the model's prompt. Because a developer can under-declare symbols in
`Produces:`, this is a guide for splitting a request, not an enforced limit.
In the TUI, answer the confirmation to dispatch; in print mode run:

    /implement --go implement-0123456789ab

One fresh Pi runs in a worktree branched from `HEAD` with the guards loaded
(the loop breaker runs in every Pi session; the rest register only in that
child): the loop breaker; `edit`/`write` refused outside `writable_paths`; an
`edit` or `write` that would remove a symbol the base defines refused with
what to do instead; bash `timeout` set to 120 s when absent and clamped at
300 s, with the bound and the self-test named in a fenced note -- `[satyrn-engine
note -- not part of the command's output]` on its own line, then the
sentence -- appended only to the first bash result of the session and to any
result whose command timed out, so an unfenced trailing sentence is never
mistaken for a file's own last line by a model reading it back through
`cat`/`tail`/`sed -n`, and ordinary results carry no reminder at all; a bash
command left
exactly as the model wrote it, with pytest's summary line in its output
detected so the Engine runs its own self-test once when a source mutation has
landed since the last one (`self_test_detected`), its own note fenced the
same way as the bound's; and, when the model stops
with no self-test since its last `edit` or `write`, one run by the Engine
whose failure goes back to the model as a single follow-up message
(`self_test_enforced`). When a self-test that completed inside a turn -- the
model's own `self_test` call, or the Engine's run after a detected bash test
run, never the enforced gate -- passes, and a source file has changed since
the last pass, the Engine sends one message saying the change
may be complete and that commits, provenance rows, repository-wide suites,
linters and test-count edits are the developer's. When a turn hits the
per-turn output cap with no tool call, the Engine asks once for a concrete
next step, at most twice per session; it stays silent when the completion
gate has just sent its own follow-up on that turn. `preserve`, `checks`,
tracked `conftest.py` files and tracked pytest configuration
(`pyproject.toml`, `pytest.ini`, `setup.cfg`, `tox.ini`) are restored from the
base into the worktree before every `self_test` run and before validation, so
the model's edits to them never count; `carried.tampered` names any of those
paths the candidate commit changed anyway (a new `tests/conftest.py` the
model added counts). The receipt is written to stdout and, verbatim, to
`.git/satyrn/receipts/<id>.json`; it adds `turns`, `tool_calls`, `tokens_in`,
`tokens_out`, `guard_firings` and `carried`; `validation` is the engine's own
run and is authoritative; `budget.state` is `token_exhausted` when the model
spent past its token budget and the candidate is kept — because Pi's stdout
is forwarded to `attempt`'s own stdout live, line by line, while it runs
(not copied over only after it exits), a declared token or turn budget can
trip and end the attempt during the run rather than only label it
afterwards. The `/implement` notification reads as `error` unless the
receipt's `code` is `OK`, `validation` is `passed`, and `carried.tampered` is
empty — `OK` with validation unavailable, timed out, not run, or failed, or
with a non-empty `tampered` list, still reads as an error (and names the
tampered paths), since a developer must never mistake any of those for a
clean pass. `self_test` itself refuses with
`TEST_COMMAND_UNAVAILABLE` when the carried `preserve`/`checks` set cannot be
read or restored from the base.

## Run one model attempt

Run `attempt` from the root of a clean, disposable Git worktree:

```console
SATYRN_ATTEMPT_TRANSCRIPT=/output/transcript.jsonl \
SATYRN_ATTEMPT_PATCH=/output/patch.diff \
satyrn-engine attempt --model omlx/gemma-4-12B-it-MLX-8bit CONTRACT
```

The model is explicit: `--model` wins, then `SATYRN_MODEL`; omitting both is a
usage error. The command freezes the parsed contract, records exact revisions
for its tracked writable files, and starts Pi with `read`, native `bash`,
`edit`, `write`, and — only when the contract declares a `test_command` —
`self_test` (`attempt.build_pi_command`), alongside the loaded guards (the
loop breaker, the scope, symbol and bash-bound checks, and the `self_test`
runner). The bash-bound guard's fenced reminder is announced once per Pi
process (on its first bash result) and again on any bash command that times
out; every other result carries no reminder at all. Pi skills, prompt
templates, themes, context files, sessions, and
ambient extensions are disabled. The current worktree remains the model's
workspace, so direct `attempt` is intended for E3's disposable worktree rather
than a developer's checkout. Pi's own stdout is forwarded to `attempt`'s
stdout line by line while Pi runs, not copied over only after it exits, so a
caller streaming that output (E3's budget enforcement) can act on it during
the run.

Both artifact paths are optional and must be absent. Their parents must be real
directories outside every registered worktree and outside Git's worktree and
common administrative directories. During preparation, attempt opens and pins
each accepted parent by filesystem identity without following symlinks; every
later publication is relative to that descriptor, and every result path tries
to close it exactly once. The transcript is Pi's exact
JSONL output. The patch is written only when the tracked tree changed. Neither
artifact is a grading verdict; they record what happened. A Pi start or
nonzero-exit failure returns
`ATTEMPT_FAILED` with exit `10` after preserving any requested artifacts. The
Engine forwards a direct POSIX `SIGTERM` (and `SIGHUP`, where supported) to
the active Pi child, waits for it to exit, then publishes the partial
transcript before its own failure result. This makes an outer timeout
diagnosable without treating its partial patch or transcript as a verdict.
`/implement` wrapper additionally gives E3 fifteen minutes to complete the
attempt. If the adapter's backstop deadline expires, it reports
`ENGINE_TIMEOUT` on POSIX only after the direct delivery child closes and a
signal-0 probe reports that the detached outer delivery group is gone. E3
first reaps its separately-sessioned attempt group and cleans or explicitly
retains its worktree. The adapter does not force the POSIX outer group with
`SIGKILL`, because doing so could interrupt that inner cleanup; an unknown or
still-present outer group leaves the refusal pending. Windows retains the
direct-child TERM/KILL/close fallback and is outside the E5 platform proof.
Tracked symlinks never enter the immutable revision map. Adapter stdin,
stdout, stderr, and diagnostic-forwarding failures are named refusals rather
than uncaught Node exceptions.

## The Pi adapter

The {term}`adapter` exposes the engine inside Pi as the `/implement` command
described in the section above (`/implement <request>`, confirmed in place or
dispatched with `--go <id>` in print mode). Once dispatched, it starts E3
`deliver` against the derived contract's absolute path under
`.git/satyrn/contracts/`. Delivery runs the same E5 `attempt` once in a
detached worktree. A success reports the candidate ref and commit; a refusal
reports the exact delivery receipt code and detail. A start failure,
deadline, crash, or malformed receipt is contained and reported by the
adapter rather than escaping the Pi turn.

Install the Pi package from the engine checkout:

```console
pi install /path/to/satyrn-engine/packages/engine
export SATYRN_ENGINE_REPO=/path/to/satyrn-engine-checkout
export SATYRN_MODEL=omlx/gemma-4-12B-it-MLX-8bit
```

`SATYRN_ENGINE_REPO` names the engine checkout; the adapter starts the
engine with `uv run --project $SATYRN_ENGINE_REPO satyrn-engine deliver`,
passing `SATYRN_MODEL` to the nested attempt. Therefore `uv`, `pi`, and the
selected model provider must be installed and configured.

Install the package **once**, globally. Do not also load any extension with pi's
`-e` flag: pi then registers `/implement` twice and suffixes the command
(`/implement:1`), so the plain name stops dispatching (recorded in the
harvest index, "The /implement command vanished").

### Bounded replacement

E4 adds a conditional replacement for Pi's `edit` tool. A normal package
install alone does not enable it: E5 supplies a versioned
`SATYRN_MUTATION_CONTEXT` containing the disposable workspace, contract, and
captured revisions. Without that context, Pi keeps its built-in `edit` tool.

With context, up to sixteen `edits[]` entries can travel in one exchange.
They are applied in order, all-or-nothing: the file is written exactly once,
after every replacement in the sequence has succeeded, or not at all, and a
refusal names the 1-based failing index. The single `old_text`/`new_text`
form remains accepted for one replacement. Python normalizes the
workspace-relative path, matches
`writable_paths`, rejects every symlink component, checks the exact-byte
SHA-256 {term}`revision`, and requires `oldText` to occur once. A success
atomically publishes the replacement and returns the next revision. A refusal
returns one of `PATH_UNDECLARED`, `REVISION_UNAVAILABLE`, `REVISION_STALE`,
`ANCHOR_MISSING`, `ANCHOR_AMBIGUOUS`, or
`MUTATION_FAILED`, with protocol exit `9`; the TypeScript tool reports the
error and does not advance its revision map. A transport failure has an
indeterminate write result, so the adapter poisons that mutation context and
refuses later edits until E5 discards the isolated worktree.

The marked `tests/test_integration_mutator.py` fixture and
`tools/exercise_mutator.mjs` prove the mutation path without calling a model.
E5 creates the context and runs that same path inside E3's disposable
worktree.

### Repeated-call protection

The same package installs a TypeScript {term}`guard` on Pi's ordinary
`tool_call` hook. It remembers the last twenty admitted calls. When five calls
in that window have the same tool name and structurally identical JSON input,
the sixth is refused with a message asking the model to take a different
action. Object key order does not matter; array order does. A refused retry is
not added to the window, so repeating it remains blocked. Each block appends a
`loop_broken` entry. The third consecutive blocked call also ends the current
Pi turn, preventing a print-mode session from retrying the same refusal until
an outer timeout.

The state is local to one Pi registration and never carries into another
session or replay. The guard only sees schema-valid calls that reach
`tool_call`; it cannot stop a loop in Pi's earlier argument validation. It also
does not detect churn where calls keep changing their content. Contract-aware
path and revision enforcement belongs to E4's bounded replacement rather than
this always-on check; symbol preservation is built there (an `edit` or
`write` that would remove a symbol the accepted base defines is refused —
see `/implement` above).
