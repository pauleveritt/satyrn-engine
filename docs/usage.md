# Using satyrn-engine

The {term}`engine` ships a command-line entry point, `satyrn-engine`, that parses
and validates a {term}`contract` and lints the repository path it names. On every
path — accepted or refused — it makes no model calls and starts no
processes.

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
required fields, both non-empty strings:

| Field | Meaning |
|-------|---------|
| `id`   | a stable identifier for the contract (names receipts and candidates in later phases) |
| `task` | the description of the change to make |

Unknown extra fields are ignored, so later phases can extend a contract by
adding keys rather than changing the parser.

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

## The Pi adapter

The {term}`adapter` exposes the engine inside Pi as a command:

```console
/implement CONTRACT
```

The command resolves `CONTRACT` against the current working directory and
runs the engine's `protocol` operation against that directory as the
repository. Acceptance reports `satyrn-engine: OK`; a refusal reports
`satyrn-engine: <CAUSE>: <detail>` — the same named causes as `check`
(`CONTRACT_UNREADABLE` through `REPO_UNAVAILABLE`, plus `INVALID_REQUEST`),
and the adapter's own transport refusals (`ENGINE_START_FAILED`,
`ENGINE_TIMEOUT`, `ENGINE_CRASHED`, `ENGINE_MALFORMED_RESPONSE`) when the
engine process itself cannot serve the request.

Install the adapter next to the guards:

```console
cp packages/engine/engine.ts packages/engine/orchestrator.ts ~/.pi/agent/extensions/
export SATYRN_ENGINE_REPO=/path/to/satyrn-engine-checkout
```

`SATYRN_ENGINE_REPO` names the engine checkout; the adapter starts the
engine with `uv run --project $SATYRN_ENGINE_REPO satyrn-engine protocol`,
so `uv` must be on `PATH`.

Install the adapter **once**, globally. Do not also load it with pi's
`-e` flag: pi then registers `/implement` twice and suffixes the command
(`/implement:1`), so the plain name stops dispatching (recorded in the
harvest index, "The /implement command vanished").
