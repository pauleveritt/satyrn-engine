# E3.5 — The loop breaker, written here (design spec)

**Date:** 2026-08-20
**Phase:** E3.5 (current)
**Status:** accepted — implementation follows in the companion plan

## Goal

Ship one user-visible behavior in the Pi package: when a model tries the same
tool call for a sixth time while five identical admitted calls remain in the
last twenty, Satyrn refuses that call with a steering message and records one
`loop_broken` telemetry entry.

The implementation is written fresh in this repository. The behavior is
re-earned from `local-ai-pi@8588ba4`'s incident record and replay fixtures; its
TypeScript is evidence, not source. The currently checked-in bundle came from
commit `565e652` and is superseded by this phase.

## Evidence and scope

The motivating run made 261 tool calls, 245 of them the same successful
`ls -R`. A failure-only circuit breaker cannot catch it because every call
succeeded. The recorded policy therefore observes the pre-execution Pi
`tool_call` hook and keys on tool name plus arguments, independent of command
success.

The evidence also establishes two limits:

- only schema-valid calls that reach `tool_call` are visible; Pi rejects a
  malformed tool call before this guard can inspect it;
- churn is different from repetition. Rewriting the same target with different
  content does not produce an identical key and is outside E3.5.

Sources at the pinned evidence revision:

- `docs/superpowers/handoff/HARVEST-INDEX.md`, "It ran the same command
  hundreds of times";
- `docs/superpowers/specs/2026-08-04-phase5-cycle6-loop-breaker-design.md`;
- `docs/superpowers/research/2026-08-04-phase5-cycle6-loop-breaker.md`;
- the six JSON fixtures now retained under `tests/fixtures/guards/`.

## Non-goals

E3.5 does not add a Python command, process, protocol method, model call, tool
budget, turn cap, timeout, churn detector, mutation rule, or general guard
framework. It does not copy the prior preserve-symbols guard. Symbol and path
preservation need the task contract and belong to E4's mutation engine.

## Product surface

There is no new `satyrn-engine` CLI surface and no new process exit code. The
existing Pi package continues to list `engine.ts` and `orchestrator.ts` in
`packages/engine/package.json`:

- `orchestrator.ts` remains the `/implement` adapter;
- `engine.ts` registers exactly one `tool_call` handler for the loop breaker.

Installing the package with `pi install /path/to/packages/engine` must load
both extensions without a local source import from `engine.ts`. Keeping the
guard self-contained preserves the package and one-file extension loading
paths and avoids repeating the prior broken-install incident.

The developer-only replay command is:

```console
node --experimental-strip-types tools/replay_guards.mjs [FIXTURE ...]
```

With no fixture arguments it replays all committed guard fixtures. Explicit
paths replay only those fixtures. It exits `0` when every observed result
matches, `1` for malformed evidence or a mismatch, and `2` for invalid command
usage. These are harness statuses, not engine product exit codes.

## Loop policy

The default policy is fixed by the evidence:

- keep the keys of the last 20 **admitted** tool calls;
- a key is the tool name plus a deterministic representation of its input;
- object property order is ignored recursively; array order remains
  significant;
- if five matching keys already exist in the window, refuse the current call;
- a refused call does not enter the window, so repeated retries remain blocked;
- different tools or different input pass independently;
- state belongs to one Pi extension registration and is never shared with a
  later registration or replay fixture.

"Admitted" is load-bearing. Adding refused calls would eventually evict the
successful repetitions and let an unchanged retry through. Registration-local
state is also load-bearing: the copied bundle currently stores its guard at
module scope, and replaying all six fixtures in one process makes the final
fixture report two blocks where its committed expectation is one.

## Types and extension seam

The implementation uses small, specific TypeScript shapes:

- `JsonValue` describes JSON-compatible tool input;
- `ToolCall` contains a readonly `toolName` and `input`;
- `LoopBrokenData` fixes the telemetry payload
  (`tool`, `repeats`, `blockedSoFar`);
- `BlockDecision` fixes the refusal shape (`block: true`, `reason`, telemetry);
- `createLoopBreaker()` is both the production construction point and the test
  seam.

No `Guard` interface, registry, composition layer, abstract base, or generic
result framework is introduced for one implementation. The default extension
creates one breaker inside each registration, adapts Pi's event to `ToolCall`,
and returns only Pi's `{block: true, reason}` shape.

## Canonical input and failure boundary

Tool inputs that pass Pi's schema are JSON data. The canonical encoder sorts
object keys recursively without reordering arrays. It rejects unsupported or
cyclic values rather than calling an unsafe `JSON.stringify` path. An input
that cannot be canonicalized is admitted without entering the repetition
window: E3.5 does not invent equality for data outside its contract.

Pi awaits extension handlers sequentially and does not wrap `emitToolCall` in
`try/catch`, so no guard exception may escape and break an ordinary tool call.
The handler therefore contains its own exception boundary:

- an unexpected inspection error admits the call rather than breaking Pi;
- a telemetry append error is contained, but the already-made repetition
  decision still blocks the repeated call;
- the steering reason remains authoritative even when telemetry storage is
  unavailable.

The reason names the exact repetition, says that another identical call will
not change the result, and asks the model to use existing information and take
a different concrete action. It does not claim the repeats were consecutive;
the policy counts occurrences anywhere in the rolling window.

## Replay contract

The six retained fixtures are evidence data, not executable product source:

| fixture | calls | blocked | first block | meaning |
|---|---:|---:|---:|---|
| accepted-contract | 5 | 0 | — | accepted run remains untouched |
| accepted-magicmock | 22 | 0 | — | longer healthy run remains untouched |
| anchor-mismatch | 60 | 46 | 14 | reachable repeated edit loop is stopped |
| edit-schema-retry | 5 | 0 | — | schema-invalid attempts are absent from the hook |
| healthy excerpt | 6 | 0 | — | sibling success for the refusal fixture |
| runaway excerpt | 6 | 1 | 6 | the sixth identical call is refused |

The replay harness imports the shipped `packages/engine/engine.ts`, registers
it against a minimal Pi API double, and observes the actual handler. It never
reimplements the policy. Every fixture gets a fresh extension registration,
and one run replays all fixtures in the same process so registration leakage is
detectable.

## Tests

**Node behavior tests, no model or network:** exercise canonical object order,
array order, tool-name separation, the five-admitted/sixth-blocked boundary,
window eviction, refused-call exclusion, per-registration isolation, telemetry
shape and count, unsupported input, inspection containment, and telemetry
failure containment. Every refusal case has a sibling admitted case.

**Replay evidence:** run all six fixtures through the shipped extension and
match exact call, block, first-block, and entry counts.

**Marked Python integration tier:** spawn Node to run the behavior/replay
commands and spawn the installed `pi` executable against a temporary Pi config
and project-local package installation. This tier proves the package manifest
loads the real extension without touching the user's Pi settings. It remains
outside the hermetic default suite and CI.

**Manual smoke:** with a temporary Pi configuration, force six identical valid
tool calls and record one `loop_broken` entry if the available Pi surface can
do so deterministically without a model. Installation and handler dispatch are
mandatory; a model-dependent live run is not.

## Dependencies and compatibility

The product adds no runtime dependency. `engine.ts` imports only Pi's
`ExtensionAPI` type. Tests use the installed Node runtime and Node built-ins;
the repository's Python integration tier already treats Node/Pi as local
process dependencies. The implementation remains compatible with the Pi
`tool_call` and `appendEntry` shapes already used by the package.

## Done when

E3.5 is complete when:

1. the copied preserve-symbols implementation and generic two-guard machinery
   are absent from the shipped package;
2. the fresh registration-local loop breaker passes its behavior tests;
3. all six evidence fixtures pass together against the shipped extension;
4. a temporary `pi install` loads the package without modifying user settings;
5. the default Python suite, marked integration tier, Node tests, Ruff,
   Pyrefly, strict Sphinx build, and `git diff --check` are green;
6. the phase and exact verification commands are recorded in `ROADMAP.md`,
   public docs, and `docs/sdd.md`.

