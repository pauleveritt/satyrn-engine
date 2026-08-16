# E2 — The adapter reaches E1 (design spec)

**Date:** 2026-08-16
**Phase:** E2 (current)
**Status:** accepted — implementation follows in the companion plan

## Goal

`/implement CONTRACT` in the TypeScript adapter reaches the same refusal as
E1's `check`: the adapter starts the engine executable as a subprocess,
sends one versioned JSON request, reads one JSON response, and converts
every transport failure into a named refusal — on POSIX and Windows. This
is the architecture gate: it proves the one-process-per-operation design
from `BRIEF.md`.

The engine side gains a `protocol` subcommand that serves the same
accept/refuse verdicts as `check` over stdin/stdout JSON. The default test
tier stays process-free; process behavior lives in the integration tier,
which gains its first tests here.

## Non-goals (explicitly out of E2)

Delivery, mutation, receipts, worktrees, packaging, the model, and the
engine outside a source checkout. `/implement` reports a verdict and
stops; it does not create a candidate (E3), does not run a model (E5), and
does not need to find the engine without `SATYRN_ENGINE_REPO` (E6).
`deliver`/`attempt` subcommands do not exist yet. The guards
(`packages/engine/engine.ts`) are untouched — they stay TypeScript and out
of the roadmap.

## Decisions from brainstorming

### The engine-side protocol surface

A new subcommand, `satyrn-engine protocol`, reads **one** JSON object from
stdin (all of stdin, whitespace tolerated, nothing after the object),
writes **one** JSON object to stdout, and exits. It is a one-shot request
surface, not a server: one process per operation, per `BRIEF.md`.

Request:

```json
{"version": 1, "operation": "check", "repo": "/abs/path", "contract": "/abs/path/contract.yaml"}
```

Response on acceptance:

```json
{"version": 1, "ok": true, "code": "OK", "message": ""}
```

Response on refusal (engine side):

```json
{"version": 1, "ok": false, "code": "CONTRACT_UNREADABLE", "message": "cannot read contract ..."}
```

- The verdict travels in the JSON, on stdout. stderr stays empty on every
  protocol path — the protocol surface is not a human surface, so it does
  not print the `satyrn-engine: <CAUSE>: <detail>` line; the adapter prints
  that in the Pi session.
- The process exit code mirrors the verdict (`0` for OK, `3`–`7` for
  refusals), so the adapter can fall back to it when the response is
  missing or unparseable. `uv` propagates the child's exit code (verified:
  `uv run --project . python -c "import sys; sys.exit(5)"` exits 5).
- The request carries **paths**, not contract content. The engine owns
  contract reading, parsing, and validation (data-over-code); the adapter
  resolves `CONTRACT` and `repo` to absolute paths against `ctx.cwd`.
- The protocol handler calls the same `check()` seam E1 exposed, so the
  order of checks (contract first, then repo) is inherited, not re-derived.

### Request validation and a new exit code

A malformed request — not valid JSON, a top level that is not a mapping, a
missing or wrong-typed `version`/`operation`/`repo`/`contract`, an unknown
`operation`, or an unsupported `version` — is refused as a new `ExitCode`
member, `INVALID_REQUEST = 7`. The response names the problem in
`message`. Unknown extra request fields are **ignored**, matching the
contract's own forward-compatibility rule.

This deliberately changes the E1 pinned table: the test
`test_exit_codes_are_distinct_and_stable` asserts `[0, 2, 3, 4, 5, 6]` and
must become `[0, 2, 3, 4, 5, 6, 7]`. One member is added; nothing is
renumbered; `1` stays reserved for crashes (a crash is not a refusal).

Transport failures are **not** engine exit codes. They happen around the
process — `uv` missing, a spawn error, a timeout, a malformed response —
and the adapter owns their names (below). The engine has nothing to say
about a process it never became.

### The adapter (`packages/engine/orchestrator.ts`)

`package.json` already declares `"./orchestrator.ts"` as an extension;
this phase creates the file that reference points at. The adapter:

- Registers a Pi command `/implement` taking one positional argument,
  `CONTRACT`. The repo is `ctx.cwd` — the roadmap's `/implement CONTRACT`
  shape, and the tree a later phase would apply a change to.
- Starts the engine with `uv run --project <repo> satyrn-engine protocol`,
  where `<repo>` is `process.env.SATYRN_ENGINE_REPO`. The env var is the
  configuration surface; the **spawner is the injected test seam** (tests
  inject a fake spawner or a stub engine checkout; production defaults to
  `node:child_process`).
- Spawns **without a shell**, stdio piped: write the request, `end()` the
  stdin stream, read stdout to EOF, await exit. `spawn("uv", [...])` lets
  the OS resolve the executable on both POSIX and Windows (`uv.exe`), and
  argv path arguments need no shell quoting on either platform.
- Runs under **its own deadline and exception boundary**, per the Pi facts
  in `BRIEF.md`: Pi awaits extension handlers sequentially with no host
  deadline, and an uncaught adapter error escapes the turn. The deadline
  defaults to 30 seconds, injectable alongside the spawner (a `check` is
  single-digit seconds; 30 s is headroom, not a promise). On deadline,
  the adapter kills the child and refuses `ENGINE_TIMEOUT`.
- Prints refusals as `satyrn-engine: <CAUSE>: <detail>` — the same line
  format as the CLI — and reports acceptance as `satyrn-engine: OK`.

The adapter is thin: it owns transport and the command surface, nothing
else. Contract semantics stay in Python.

### Refusal vocabulary

| code | side | when |
|------|------|------|
| `ENGINE_START_FAILED` | adapter | spawn error, `uv` not found, `SATYRN_ENGINE_REPO` missing/invalid |
| `ENGINE_TIMEOUT` | adapter | no response before the deadline |
| `ENGINE_CRASHED` | adapter | nonzero exit with no parseable response (incl. exit 1) |
| `ENGINE_MALFORMED_RESPONSE` | adapter | response is JSON but wrong shape, or version mismatch |
| `USAGE` | adapter | `/implement` called with no `CONTRACT` argument (reuses the engine's `USAGE` name for the command surface) |
| `INVALID_REQUEST` | engine | malformed request (the only new engine code) |
| `CONTRACT_UNREADABLE` … `REPO_UNAVAILABLE` | engine | pass through verbatim from `check()` |

The engine's refusals pass through unchanged with their E1 names; the
adapter never reinterprets them.

### Testing

**Default tier (no process, no network, no subprocess — tripwire
enforced):** the pure functions behind the protocol — request parsing,
response rendering, and a `handle_protocol(text) -> (response_text,
exit_code)` that drives `check()` — unit-tested with sibling
success/refusal pairs. Reading stdin itself is not tested here (it is a
piped byte stream, exercised in the integration tier).

**Integration tier (`-m integration`, marked, not in CI — first tests in
this phase):** spawns the real console script and pipes the JSON, covering
accept, every engine refusal, and `INVALID_REQUEST`, each refusal with a
sibling success. Also proves `uv run --project` forwards stdin and the
exit code on this machine, one time, recorded.

**Node replay harness (pattern of `tools/replay_guards.mjs`):** an
ExtensionAPI double and an injected fake spawner drive
`orchestrator.ts`'s request building, response parsing, deadline, and all
four transport conversions, against a stub engine. No real Pi, no model.

**One manual proof, recorded:** a live `/implement` in a running Pi
against a real contract and a real refusal — the E2 analogue of E1's
planted-tripwire proof — plus one recorded run on Windows, since the phase
names POSIX and Windows and the integration tier does not run in CI.

### Protocol compatibility fixtures

Committed JSON request/response pairs live in
`tests/fixtures/protocol/` (the "internal Pi-adapter protocol and its
compatibility fixtures" `README.md` already claims ownership of). They are
the versioned contract between the adapter and the engine, reused by the
Python protocol tests and the Node harness.

## Error handling

- The protocol handler never lets an exception escape as a traceback to
  the adapter: every input problem is a named `INVALID_REQUEST` response;
  an unexpected internal error propagates and the interpreter exits 1,
  which the adapter reads as `ENGINE_CRASHED` (no response) — a transport
  failure, never a mislabeled refusal.
- The adapter wraps the whole exchange in try/catch; an adapter bug
  surfaces as a named adapter refusal, not an escape from the Pi turn.
- Unknown `version` in a response → `ENGINE_MALFORMED_RESPONSE`; a future
  engine that still speaks the same request shape is forward-compatible,
  and a future that changes `version` is detected, not guessed at.

## Dependencies

No new runtime dependency for the engine (`pyyaml` remains the only one).
The adapter imports Pi's `ExtensionAPI` types and Node built-ins only
(`node:child_process`, `node:path`). The harvest index warns that any
change to an installable file forces an install-doc re-verification: the
guards' documented one-file `cp` install becomes a two-file install
(`engine.ts` + `orchestrator.ts` plus the `package.json`), and the
`SATYRN_ENGINE_REPO` requirement is new — both must be re-verified in the
docs as part of this phase.

## Testing layout

```
tests/
  conftest.py                         # autouse tripwire (kept; now also covers protocol tests)
  test_protocol.py                    # default tier: parse/render/handle unit tests (sibling pairs)
  test_integration_protocol.py        # -m integration: real subprocess, accept + refusals + INVALID_REQUEST
  fixtures/protocol/                  # versioned request/response compatibility fixtures
tools/
  replay_orchestrator.mjs             # Node harness: ExtensionAPI double + fake spawner
packages/engine/
  orchestrator.ts                     # the adapter: /implement command, transport, conversion
```
