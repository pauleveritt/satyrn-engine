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

## Run one model attempt

Run `attempt` from the root of a clean, disposable Git worktree:

```console
SATYRN_ATTEMPT_TRANSCRIPT=/output/transcript.jsonl \
SATYRN_ATTEMPT_PATCH=/output/patch.diff \
satyrn-engine attempt --model omlx/gemma-4-12B-it-MLX-8bit CONTRACT
```

The model is explicit: `--model` wins, then `SATYRN_MODEL`; omitting both is a
usage error. The command freezes the parsed contract, records exact revisions
for its tracked writable files, and starts Pi with only `read` and E4's bounded
`edit`. Pi skills, prompt templates, themes, context files, sessions, and
ambient extensions are disabled. The current worktree remains the model's
workspace, so direct `attempt` is intended for E3's disposable worktree rather
than a developer's checkout.

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

The {term}`adapter` exposes the engine inside Pi as a command:

```console
/implement CONTRACT
```

The command resolves `CONTRACT` against the current working directory and
starts E3 `deliver` there. Delivery runs the same E5 `attempt` once in a
detached worktree. A success reports the candidate ref and commit; a refusal
reports the exact delivery receipt code and detail. A start failure, deadline,
crash, or malformed receipt is contained and reported by the adapter rather
than escaping the Pi turn.

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

With context, exactly one `edits[]` entry is sent over the existing one-shot
protocol. Python normalizes the workspace-relative path, matches
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
`loop_broken` entry.

The state is local to one Pi registration and never carries into another
session or replay. The guard only sees schema-valid calls that reach
`tool_call`; it cannot stop a loop in Pi's earlier argument validation. It also
does not detect churn where calls keep changing their content. Contract-aware
path and revision enforcement belongs to E4's bounded replacement rather than
this always-on check; symbol analysis remains deferred.
