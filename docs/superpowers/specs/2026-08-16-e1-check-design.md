# E1 — `check`: it installs and refuses (design spec)

**Date:** 2026-08-16
**Phase:** E1 (current)
**Status:** accepted — implementation follows in the companion plan

## Goal

Deliver `satyrn-engine check --repo REPO CONTRACT`: parse and validate a
contract, lint the paths it names, and either accept it (exit 0) or refuse
it with a named cause and a stable, documented exit code. On every path —
accepted or refused — the engine makes zero model calls and starts zero
processes.

## Non-goals (explicitly out of E1)

Pi, the adapter, delivery, mutation, packaging. `check` does not create a
worktree, does not run a model or any executable, does not touch the working
tree, and does not build a wheel. Those are later phases (E2–E6). A "checkout
install" for E1 means `uv sync` in a fresh checkout, which produces an
editable install exposing the `satyrn-engine` console script — not a
published package.

## Decisions from brainstorming

### CLI surface

- Console script `satyrn-engine` → `satyrn_engine.cli:main`.
- `check` is an argparse subcommand with one required option and one required
  positional:
  - `--repo REPO` — the working-tree root; must exist and be a directory.
  - `CONTRACT` — path to the contract YAML file; must exist and be readable.
- Subparsers are used now (one subcommand) rather than flat flags so that
  `deliver` (E3) and `attempt` (E5) join as sibling subcommands without a
  breaking CLI change. This is the standard argparse shape, not machinery
  ahead of its contract: the roadmap already names the future subcommands.

`main(argv)` returns an `int` exit code. The console-script wrapper applies
`sys.exit(main())`. argparse's own usage errors still raise `SystemExit(2)`;
that is exit code `USAGE`, not a refusal.

### Contract format

A contract is a YAML document whose top level is a mapping with exactly two
**required** fields, both non-empty strings:

| field | meaning |
|-------|---------|
| `id`   | stable identifier for the contract (names receipts and candidates in later phases) |
| `task` | the change description |

Unknown extra fields are **ignored**, not refused: contracts will grow fields
in later phases, and data-over-code means extending is adding a key, not
subclassing. A top level that is not a mapping, a field that is absent, empty,
or not a string is refused as `CONTRACT_MISSING_FIELD` (the message names the
field and the problem).

### Exit codes

A single `ExitCode` IntEnum in `satyrn_engine/exits.py` is the source of
truth. The numeric values are the stable contract, pinned by a test.

| code | name | when |
|------|------|------|
| 0 | `OK` | contract accepted |
| 2 | `USAGE` | argparse usage error (bad flags, missing CLI args) |
| 3 | `CONTRACT_UNREADABLE` | CONTRACT path missing or not a readable file |
| 4 | `CONTRACT_INVALID_YAML` | CONTRACT is not valid YAML |
| 5 | `CONTRACT_MISSING_FIELD` | required field absent, empty, or wrong type (incl. top level not a mapping) |
| 6 | `REPO_UNAVAILABLE` | `--repo` missing or not a directory |

Exit code 1 is reserved for unexpected internal errors (an uncaught
exception, which Python reports with exit 1). It is **not** a refusal: a
refusal is a deliberate, named verdict; a crash is a bug. No refusal code may
ever equal 1, 0, or another refusal code.

Each refusal prints one line to stderr:

```
satyrn-engine: <CAUSE>: <detail>
```

where `<CAUSE>` is the `ExitCode` member name (the "named cause"). Success
prints nothing to stdout or stderr.

### Order of checks

`check` runs its steps in this order and returns the first refusal:

1. read the contract file → `CONTRACT_UNREADABLE`
2. parse it as YAML → `CONTRACT_INVALID_YAML`
3. validate required fields → `CONTRACT_MISSING_FIELD`
4. lint `--repo` (exists and is a directory) → `REPO_UNAVAILABLE`
5. accept → `OK`

The contract is checked before the repo because the contract is the thing
being refused or accepted; the repo is the target it will later be applied
to. The order is documented here so a later phase that changes it does so
deliberately.

### Zero model calls and zero processes

Enforced **mechanically**, per binding rule 3, by a global autouse tripwire
in `tests/conftest.py` that patches the process- and network-entry points of
the standard library to raise `AssertionError`:

- process: `subprocess.Popen`, `run`, `call`, `check_call`, `check_output`,
  `getoutput`, `getstatusoutput`; `os.system`, `os.popen*`, `os.spawn*`,
  `os.exec*`, `os.fork`, `os.forkpty`, `os.posix_spawn*`;
- network (model-call proxy): `socket.socket`.

A "model call" at E1 can only happen two ways — spawning a model process
(caught by the subprocess guard) or opening a network connection to a model
server (caught by the socket guard) — because the package has zero runtime
dependencies other than `pyyaml` and no model client. The tripwire is
**proven once**: a deliberately planted process-spawning test must fail the
run, and that failure is recorded before the planted test is removed. The
tripwire then stays, constraining every later phase's test design.

### Module layout

| file | responsibility |
|------|----------------|
| `src/satyrn_engine/exits.py` | `ExitCode` IntEnum — the single source of truth for exit codes |
| `src/satyrn_engine/contract.py` | `Contract` (frozen dataclass), `ContractError` (named refusal), `load_contract(path)` |
| `src/satyrn_engine/check.py` | `CheckResult` (code + message + optional contract), `check(repo, contract_path)` |
| `src/satyrn_engine/cli.py` | `build_parser()`, `main(argv) -> int` |
| `src/satyrn_engine/__init__.py` | `__version__` (unchanged) |

`check()` is the library seam: it returns a `CheckResult` carrying the
`ExitCode`, the human-readable detail, and (on success) the parsed
`Contract`. The CLI is a thin wrapper that prints the refusal and returns the
code. The seam used by tests is the same seam later phases extend — no second
plugin mechanism.

## Error handling

- `load_contract` raises `ContractError` carrying the `ExitCode` and a
  one-line `message`. It converts `OSError` on read into
  `CONTRACT_UNREADABLE` and `yaml.YAMLError` on parse into
  `CONTRACT_INVALID_YAML`, chaining the original exception.
- `check` catches `ContractError` and folds it into a `CheckResult`; it does
  not let it escape to the caller's stderr as a traceback.
- An unexpected exception in `main` is **not** caught: it propagates and the
  interpreter exits 1, keeping crashes distinct from refusals.

## Dependencies

`pyyaml>=6.0.3` joins `[project.dependencies]`. It is the only runtime
dependency. (YAML is not in the Python standard library, and re-implementing
a YAML subset would be a trap the brief warns against.)

## Testing

Layout:

```
tests/
  conftest.py                         # autouse tripwire (no process / no network)
  test_toolchain.py                   # existing, kept
  test_contract.py                    # load_contract unit tests (sibling success + refusal)
  test_check_cli.py                   # end-to-end through main(): accept + every refusal
  fixtures/contracts/
    valid.yaml                        # id + task
    invalid.yaml                      # malformed YAML
    missing-field.yaml                # valid YAML, no task
```

Coverage, mapping each done-when item to a test:

1. **accepts one valid contract** — `test_valid_contract_accepted` asserts
   `main([...]) == ExitCode.OK` and empty stderr.
2. **refuses invalid YAML / impossible path / missing field, each with a
   distinct stable exit code** — one test per refusal, asserting both the
   exact `ExitCode` and that stderr names the cause; plus
   `test_refusal_codes_are_distinct_and_stable` pins the numeric values to
   `0, 2, 3, 4, 5, 6` so renumbering breaks the build.
3. **zero model calls and zero processes on any path** — the autouse
   tripwire plus `test_check_paths_make_no_process_or_model_calls`, which
   drives every path (valid + all four refusals) under the tripwire.
4. **default tier in single-digit seconds** — no subprocess, no network,
   ~10 tests; verified with `time uv run pytest`.
5. **planted process-spawning test fails the build** — proven first (before
   product code) and recorded; the planted test is then removed and the
   tripwire kept.

Non-vacuity (binding rule 4): every refusal test has the valid-contract
acceptance test as its sibling success, and `test_contract.py` pairs each
`load_contract` refusal with a success case.
