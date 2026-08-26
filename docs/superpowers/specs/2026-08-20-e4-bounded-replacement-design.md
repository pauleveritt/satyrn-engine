# E4 — One bounded replacement (design spec)

**Date:** 2026-08-20
**Phase:** E4 (current)
**Status:** accepted — implementation follows in the companion plan

## Correction, 2026-08-21

The accepted text below made four boundary mistakes that implementation must
not preserve:

1. A path missing from the TypeScript revision map was described as a local
   refusal. That lets TypeScript decide writable-path policy. The wire field is
   instead required and nullable: TypeScript always sends the request with its
   known SHA-256 or `null`; Python first checks `writable_paths`, then returns
   `REVISION_UNAVAILABLE` when an authorized path has no captured revision.
2. Rejecting only symlink escapes is insufficient. A declared in-repository
   symlink can alias an undeclared file. Every target path component, including
   the leaf, must therefore be opened and checked without following symlinks;
   E4 never mutates through a symlink.
3. A mutation transport failure can occur after Python published a replacement
   but before TypeScript received the response. Such a result is indeterminate,
   not proof that the file is unchanged. The mutation context becomes poisoned
   after a timeout, crash, malformed response, or other transport failure and
   accepts no later edit. E5 discards the isolated worktree containing that
   context.
4. The existing one-shot exchange did not yet earn its lifecycle claim. It
   rejected immediately after sending `SIGTERM` and had no asynchronous stdin
   error boundary. The adapter now contains stdin failures and reports timeout
   only after the direct child closes, escalating to `SIGKILL` after a short
   grace period.

These are corrections to the accepted design, not new phase scope. The
normative request, refusal, path, adapter, and test rules below are read with
this section taking precedence where they conflict.

## Goal

Route one exact-text replacement through Pi → TypeScript → Python. The Python
core is the only component that decides whether the target is writable, the
file revision is current, and the anchor occurs exactly once. A successful
request atomically replaces that one occurrence and returns the next SHA-256
revision. A stale revision, undeclared path, missing anchor, or ambiguous
anchor is a named refusal and leaves the file unchanged.

This phase proves the mutation boundary that E5 will run inside E3's detached
worktree. It does not yet start a model or create a candidate.

## Evidence and scope

The pinned design record at `local-ai-pi@8588ba4` names E4's done-when and the
one-shot protocol boundary:

- `docs/superpowers/research/2026-08-16-two-repo-rewrite-and-python-engine.md`,
  “Python core, TypeScript adapter”, “Start with a one-shot protocol”, and the
  E4 roadmap row;
- `docs/superpowers/handoff/satyrn-engine/ROADMAP.md`, “E4 is the scope-blowup
  risk”;
- `extensions/implementer/mutation-engine.ts`, especially its revision and
  unique-anchor evidence.

The old TypeScript mutation engine is evidence, not source. It combined file
creation, whole-file proposals, multiple edits, size limits, newline policy,
symbol preservation, cross-file moves, and no-op handling. E4 re-earns only
the four decisions required by its done-when: writable path, current revision,
anchor present, and anchor unique.

## Non-goals

E4 does not add a human-facing CLI command, a model call, delivery, a
candidate, a transcript, file creation, whole-file write, multiple
replacements, a general edit algebra, fuzzy matching, symbol analysis,
cross-file moves, proposal-size policy, validation, a persistent Python
process, or a generic mutation framework.

The E3.5 loop breaker remains a separate TypeScript extension. E4 does not add
another `tool_call` guard: the mutation decision happens inside the replacement
tool before a write.

## Contract extension

The YAML contract gains one optional field:

| field | shape | meaning |
|---|---|---|
| `writable_paths` | list of non-empty strings | workspace-relative path patterns accepted for mutation |

`Contract.writable_paths` is an immutable tuple. Omitting the field produces
an empty tuple, preserving every E1–E3 contract and check fixture. If the field
is present but is not a list of non-empty strings, contract loading refuses it
as `CONTRACT_MISSING_FIELD`; no new contract exit code is needed.

Python's `fnmatch.fnmatch` is the reference matching rule. In particular, `*`
may cross `/`. This is a recorded design decision, not a TypeScript behavior to
port. The request path is first normalized to one safe POSIX-style relative
path; patterns are matched only in Python.

## Product surfaces

### Human CLI

There is no new human-facing command and no change to `check` or `deliver`.
The existing private `satyrn-engine protocol` command gains the `replace`
operation.

### Pi tool

The package adds `mutator.ts`, loaded beside `engine.ts` and
`orchestrator.ts`. It replaces Pi's built-in `edit` tool only when
`SATYRN_MUTATION_CONTEXT` contains a valid explicit context. Ordinary Pi
sessions without that variable keep their built-in tool untouched.

The E4 tool accepts Pi's familiar shape, restricted to exactly one entry:

```json
{
  "path": "src/app.py",
  "edits": [
    {"oldText": "return 1", "newText": "return 2"}
  ]
}
```

The array shape follows the installed Pi edit surface, while `minItems: 1`
and `maxItems: 1` make the E4 operation a single replacement rather than a
premature edit language.

### Mutation context

E5 will construct the context before starting its Pi child. E4 fixes its
versioned shape now so the adapter has no hidden filesystem policy:

```json
{
  "version": 1,
  "repo": "/absolute/disposable/worktree",
  "contract": "/absolute/path/to/contract.yaml",
  "revisions": {"src/app.py": "<64 lowercase hex characters>"}
}
```

The context is data, not ambient engine state. It is parsed once per extension
registration. The adapter never computes a file hash or reads a contract. A
path absent from `revisions` is refused before a subprocess starts. After a
successful replacement, the returned SHA-256 replaces the stored revision so
a later E5 slice can make sequential calls without a long-running Python
process.

Missing or malformed context means no E4 tool is registered. This keeps
ordinary `pi install` behavior safe and avoids overriding a useful built-in
with a tool that cannot run.

## Protocol

The protocol remains version 1 and one-request/one-response. A replacement
request is:

```json
{
  "version": 1,
  "operation": "replace",
  "repo": "/absolute/disposable/worktree",
  "contract": "/absolute/path/to/contract.yaml",
  "path": "src/app.py",
  "expected_sha256": "<64 lowercase hex characters or null>",
  "old_text": "return 1",
  "new_text": "return 2"
}
```

All fields are required. `expected_sha256` is either 64 lowercase hexadecimal
characters or `null`; omitting it is invalid. `old_text` must be non-empty;
`new_text` may be empty. Malformed JSON, wrong field types, an invalid non-null
SHA shape, an unsafe relative path, or an unsupported operation is
`INVALID_REQUEST` and exit 7. Those are transport/input-shape failures, not
mutation-policy refusals.

A successful response extends the existing base response with one result:

```json
{
  "version": 1,
  "ok": true,
  "code": "OK",
  "message": "",
  "result": {"path": "src/app.py", "sha256": "<next revision>"}
}
```

A policy refusal has `ok: false`, one detailed code, a human-readable message,
and `result: null`. The detailed code is authoritative; all five policy
refusals share product process exit 9, `MUTATION_REFUSED`:

| response code | condition |
|---|---|
| `PATH_UNDECLARED` | normalized path matches no contract pattern |
| `REVISION_UNAVAILABLE` | declared path has no captured revision in the mutation context |
| `REVISION_STALE` | current file SHA-256 differs from `expected_sha256` |
| `ANCHOR_MISSING` | `old_text` occurs zero times |
| `ANCHOR_AMBIGUOUS` | `old_text` occurs more than once |

An expected local operational failure after request acceptance, such as an
unreadable target or failed atomic replacement, returns `MUTATION_FAILED`,
`result: null`, and exit 9. It is not one of the five done-when policy
refusals, but it must not become a traceback or a malformed protocol response.

`check` responses keep their existing four-field shape. Protocol parsers must
therefore use an operation-discriminated request type and a replacement-only
result rather than adding nullable mutation fields to every response.

## Python mutation rules

### Path boundary

The request path must use `/`, be relative, contain no NUL, empty segment,
`.` segment, or `..` segment, and name a regular file inside the canonical
repository root. Absolute paths are malformed requests. After shape
acceptance, Python checks every filesystem component without following
symlinks; any symlink or containment failure is `MUTATION_FAILED`. The
declared-path match happens against the normalized relative path before the
filesystem target is opened.

E4 does not create a missing file. A declared but missing/non-regular target is
`MUTATION_FAILED`.

### Revision

SHA-256 is computed from the file's exact bytes. After the path is declared,
a null request revision is `REVISION_UNAVAILABLE`. A non-null revision must be
64 lowercase hexadecimal characters. The engine reads the file once, computes
the actual revision, and refuses `REVISION_STALE` before anchor inspection if
it differs. The response carries the hash of the exact bytes written.

Byte hashing avoids newline normalization and preserves a stable revision for
arbitrary UTF-8 text files. E4 replacement strings are UTF-8 JSON strings; a
target that is not valid UTF-8 is `MUTATION_FAILED`, not silently decoded.

### Anchor and write

`old_text` is encoded as UTF-8 and counted as an exact byte sequence in the
revision-checked content. Zero matches is `ANCHOR_MISSING`; more than one is
`ANCHOR_AMBIGUOUS`. Exactly one match is replaced literally, so `$`, backslash,
and other replacement syntax have no special meaning.

The engine writes a same-directory temporary file, copies the target's mode,
flushes and closes it, then uses `os.replace` to publish atomically. It removes
its temporary on every catchable failure. The caller's supplied path is never
opened for a partial in-place write.

## TypeScript adapter and failure boundary

`mutator.ts` owns only:

- parsing the versioned mutation context;
- validating the one-edit Pi argument shape;
- looking up the current expected revision in its in-memory map;
- building the versioned request;
- calling the existing injected `exchange` seam;
- updating the map on a typed successful result;
- rendering every refusal as a Pi tool error.

It does not read files, match `writable_paths`, count anchors, or compute hashes.
The adapter catches malformed context, malformed engine responses, engine
refusals, timeout/start/crash failures, and unexpected local errors. Its tool
`execute` never rejects. Refusal content is marked as an error through Pi's
tool-result shape; a failed call does not advance the revision map.

The existing exchange has an injected `Spawner`, deadline, response parsing,
and process lifecycle. E4 generalizes its request/response types only as far as
the replacement response requires; it does not create a second subprocess
adapter or persistent transport.

## Types and module layout

Python uses concrete closed types:

- `Contract` gains `writable_paths: tuple[str, ...]`;
- `CheckRequest` and `ReplaceRequest` are frozen dataclasses in a
  discriminated `ProtocolRequest` union;
- `MutationCode` is a `StrEnum` for seven response codes: `OK`, five policy
  refusals, and the operational-failure code `MUTATION_FAILED`;
- `MutationResult` and `MutationReceipt` are frozen dataclasses;
- response payloads use `TypedDict` shapes at the JSON boundary.

TypeScript uses `MutationContext`, `EditInput`, `ReplacementRequest`,
`ReplacementResult`, and `ReplacementResponse` interfaces plus the existing
`Spawner` extension seam. There is no generic tool framework.

New product files:

| file | responsibility |
|---|---|
| `src/satyrn_engine/mutation.py` | path/revision/anchor checks and atomic replacement |
| `packages/engine/mutator.ts` | conditional Pi edit adapter |

Existing `protocol.py`, `contract.py`, `exits.py`, `orchestrator.ts`, and the
package manifest receive only the changes required to connect that vertical
slice.

## Tests

**Default Python tier, no subprocess or network:**

- contract field absent, valid patterns, and malformed pattern list;
- successful literal replacement and exact next revision;
- each of the five named policy refusals with a sibling successful replacement;
- unsafe path, internal/escaping symlinks, missing/non-regular/non-UTF-8
  target, atomic-write failures, mode preservation, literal `$` replacement,
  and temporary cleanup;
- protocol parsing/rendering for `check` and `replace`, including every request
  field and response shape;
- stable exit mappings and 100% statement/branch coverage.

**Node behavior tier, no model or network:**

- valid/malformed context and one-edit argument shape;
- exact replacement request generation;
- revision map advances only on success and a transport failure poisons it;
- each engine/transport refusal becomes an error result and no `execute`
  promise rejects;
- sessions without context do not override Pi's edit tool.

**Marked integration tier:**

- the real console protocol changes one fixture through the Python process;
- the shipped TypeScript adapter uses the real spawner and Python protocol for
  the success plus unavailable, stale, undeclared, missing, ambiguous, and
  symlink siblings;
- a temporary `pi install` loads all three package extensions without touching
  user settings.

No integration test calls a model. Real filesystem and subprocess behavior
remain outside the hermetic default suite.

## Done when

E4 is complete when:

1. one fixture replacement succeeds through the shipped TypeScript adapter and
   real Python protocol;
2. unavailable revision, stale revision, undeclared path, missing anchor, and
   ambiguous anchor each return their exact typed refusal and leave the file
   unchanged;
3. no adapter path rejects or lets an exception escape Pi;
4. old contracts still load and `check` unchanged, while a contract with
   `writable_paths` is enforced only by Python;
5. the package loads the conditional mutator without changing ordinary Pi
   sessions;
6. default, integration, Node, Ruff, Pyrefly, strict Sphinx, coverage, and
   `git diff --check` gates are green;
7. the exact commands and recomputed evidence are recorded in public docs and
   `docs/sdd.md`.
