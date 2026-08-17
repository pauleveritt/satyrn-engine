# Architecture

**One Python process per operation.** The {term}`engine` never lingers:
the {term}`adapter` starts it, hands it one request, reads one response,
and the process exits. No sidecar, no session, no supervisor — a
deliberate cut, argued in `BRIEF.md`, with the measured cost as the only
condition that reopens it.

## The shape

```text
┌─────────────────┐   one JSON request (stdin)   ┌───────────────────────────────┐
│      Pi         │ ───────────────────────────▶ │ uv run --project $SATYRN_ENGINE_REPO │
│  /implement     │                              │     satyrn-engine protocol     │
│    adapter      │ ◀─────────────────────────── │                               │
└─────────────────┘   one JSON response (stdout) └───────────────────────────────┘
```

The flow, for a `/implement CONTRACT`:

1. The adapter resolves `CONTRACT` against `ctx.cwd`; the repository is
   `ctx.cwd`. Nothing else is asked of the user.
2. It builds one JSON request — `{"version": 1, "operation": "check",
   "repo": ..., "contract": ...}` — and starts the engine with
   `uv run --project $SATYRN_ENGINE_REPO satyrn-engine protocol`, stdio
   piped. It writes the request, closes stdin, and waits.
3. The engine parses and validates the request, calls the same
   {term}`check` seam E1 exposed, writes one JSON response, and exits with
   the verdict's exit code. stderr stays empty on every protocol path.
4. The adapter reads the response. The JSON is authoritative — the exit
   code is only the fallback when the response is unreadable. It reports
   the verdict as `satyrn-engine: <CAUSE>: <detail>`.

## Who owns what

| side | owns |
|------|------|
| {term}`engine` | contract semantics: parsing, validation, path linting; the `check` seam, the {term}`protocol` surface, exit codes `0`–`7` |
| {term}`adapter` | transport — spawning, the deadline, refusal conversion — and the command surface. It never reinterprets an engine refusal |

The split is what keeps the adapter thin: Pi-specific concerns (no host
deadline, uncaught errors escaping the turn) live in TypeScript; contract
semantics live once, in Python, shared with the CLI and any future
caller.

## Why one process per operation

`BRIEF.md` settled this. A process per operation is slower and much
smaller: no session lifetime, no lifecycle question, nothing to clean up.
If startup cost is later *measured* as material, the same request and
response objects gain a persistent transport then — not before.

## Why the adapter carries its own deadline and exception boundary

Verified Pi facts (v0.84.2) from `BRIEF.md`: extension handlers are
awaited sequentially with no host deadline, and an uncaught adapter error
escapes the turn. So the adapter enforces a 30-second deadline of its own
and converts every transport failure — spawn error, timeout, crash,
malformed response — into a named refusal instead of letting it escape.

## Refusals, split by side

Engine causes (`2`–`7`): `USAGE`, `CONTRACT_UNREADABLE`,
`CONTRACT_INVALID_YAML`, `CONTRACT_MISSING_FIELD`, `REPO_UNAVAILABLE`,
`INVALID_REQUEST`. Adapter causes: `ENGINE_START_FAILED`,
`ENGINE_TIMEOUT`, `ENGINE_CRASHED`, `ENGINE_MALFORMED_RESPONSE`. The
adapter's are transport failures the engine never sees; the engine's pass
through verbatim.

The design record — arguments considered and rejected — is the E2 spec
and plan under `docs/superpowers/`.
