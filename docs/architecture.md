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

The first flow, for a `/implement CONTRACT` check:

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
| {term}`engine` | contract and mutation semantics plus delivery: parsing, path linting, writable-path matching, revision and anchor checks, isolated Git execution, candidate publication, receipts; the `check` seam, the {term}`protocol` surface, and stable exit codes |
| {term}`adapter` | transport — spawning, the deadline, refusal conversion — and Pi's command/tool surfaces. It never reinterprets an engine refusal or duplicates path policy |

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

## Why the loop breaker stays in TypeScript

The loop breaker runs on Pi's ordinary `tool_call` hook, not only during a
deliberate `/implement`. Sending every tool call through Python would make a
missing or broken sidecar capable of breaking an otherwise ordinary Pi
session. The guard therefore remains a zero-dependency TypeScript check beside
the adapter.

One extension registration owns one rolling window of twenty admitted call
keys. Five matching keys are allowed; the next exact repeat is blocked and
recorded as `loop_broken`. Blocked calls do not enter the window. Input is
canonical JSON, so recursively reordered object keys compare equal while array
order remains meaningful. Unsupported or cyclic input is admitted rather than
guessed at.

Pi does not catch an exception escaping tool-call dispatch. Inspection and
telemetry therefore have separate exception boundaries: an inspection error
admits the call without breaking Pi, while a telemetry failure cannot reverse
an already-made block decision. This mechanism only handles exact repetition.
Schema-validation loops happen before the hook, and content churn produces
different keys. Contract-aware mutation enforcement is the separate E4 path
below.

## Why bounded replacement crosses into Python

E4 conditionally overrides Pi's `edit` tool only when a parent supplies a
versioned mutation context. The context fixes a disposable workspace, contract,
and SHA-256 revision map. One exact replacement then follows this path:

```text
Pi edit(path, one oldText/newText)
       │
       ▼
TypeScript: look up prior revision and translate JSON
       │ one replace request
       ▼
Python: normalize path → match writable_paths → check revision
        → require one anchor → atomically replace → return next revision
```

Python owns every permission and mutation decision. Its `fnmatch` behavior is
the reference contract, it reads the exact file bytes, and it returns the next
{term}`revision`. TypeScript neither reads a file nor hashes one; it retains the
revision returned by Python only after a typed success. A refusal leaves both
the file and that in-memory map unchanged.

The replacement is intentionally one anchor in one existing UTF-8 file. File
creation, whole-file writes, multiple edits, fuzzy matching, symbol analysis,
validation, and model orchestration are not hidden behind an abstraction. E5
will supply this context inside E3's worktree and consume the same seam.

## Refusals, split by side

Check and protocol engine causes (`2`–`7`): `USAGE`, `CONTRACT_UNREADABLE`,
`CONTRACT_INVALID_YAML`, `CONTRACT_MISSING_FIELD`, `REPO_UNAVAILABLE`,
`INVALID_REQUEST`. Adapter causes: `ENGINE_START_FAILED`,
`ENGINE_TIMEOUT`, `ENGINE_CRASHED`, `ENGINE_MALFORMED_RESPONSE`. The
adapter's are transport failures the engine never sees; the engine's pass
through verbatim. Delivery preserves contract and repository-path refusal
codes `3`–`6`. Its delivery-specific handled results that publish no candidate
use exit `8`, and the receipt carries the specific cause. CLI usage remains
`2`; an uncaught bug remains `1`. E4 replacement refusals use exit `9`; their
JSON `code` distinguishes `PATH_UNDECLARED`, `REVISION_STALE`,
`ANCHOR_MISSING`, `ANCHOR_AMBIGUOUS`, and `MUTATION_FAILED`.

The design record — arguments considered and rejected — is the E2 spec
and plan under `docs/superpowers/`.

## Why delivery uses a detached worktree

`deliver` is the layer below model and mutation policy. It accepts one trusted
command and gives it a real directory in which to write, while keeping those
writes physically separate from the caller's checkout:

```text
clean caller root -- capture exact HEAD --> detached temporary worktree
                                              |
                                         run COMMAND once
                                              |
                       discard <--- no acceptable change ---> commit tree
                                                              |
                     remove temporary worktree -- CAS-create candidate ref
```

The exact base commit is captured once before the command starts. This is the
Git equivalent of snapshot isolation: if the caller commits on the real branch
while delivery is running, the attempt still sees and reports a diff against
the state it actually started from. A separate directory protects the caller's
files and index; detached `HEAD` avoids creating or checking out a branch in the
user's branch namespace. After the command returns, the engine verifies both
that `HEAD` still resolves to the captured base and that it is still detached;
attaching that same commit to a branch is therefore discarded as
`COMMAND_CHANGED_HEAD`.

The result is a commit under
`refs/satyrn/candidates/<contract-id>/head`, not a branch and not an automatic
merge. The ref lives in Git's shared ref namespace, so the same repository
reached through a symlink or another linked worktree still has one identity for
that contract id. Publication happens only after cleanup, using Git's
create-if-absent compare-and-swap, so concurrent attempts can produce at most
one published candidate.

This is not a security sandbox. The command can write absolute paths, mutate
the repository's shared Git state, or deliberately escape its POSIX process
group; E3 does not prevent those actions. It isolates ordinary file writes and
bounds ordinary descendants on timeout, so it accepts only a trusted,
synchronous command. E4 now supplies mutation rules when its tool is used; E5
connects them to delivery, validation, and a real attempt.

Worktree isolation is established practice in agent tooling. E3's specific use
is single-attempt safety, rather than parallelism, plus a dedicated non-branch
candidate namespace. The detailed decisions, prior work, and Git references
are recorded in the {doc}`E3 design spec
<superpowers/specs/2026-08-18-e3-delivery-design>`.

## Delivery result and cleanup order

Once the command succeeds, the engine stages the isolated files, writes a tree,
and compares it with the captured base. An unchanged tree is discarded before
commit creation. For a changed tree, the engine creates a commit with the base
as its only parent, derives `changed_paths`, removes the worktree and temporary
parent, and only then publishes the ref. A cleanup failure supersedes the
pending result and reports the retained path; an unexpected Python exception
attempts the same cleanup and is re-raised as exit `1` without fabricating a
receipt.

The cleanup guard has two independent axes: whether Git may still have a linked
worktree registered, and whether process teardown is known safe. The Git axis
becomes uncertain before `git worktree add` starts, because an interruption can
arrive after Git mutates shared metadata but before Python sees the return. The
process axis closes before command creation and reopens only after normal child
completion or confirmed timeout teardown. If either axis remains uncertain,
the engine retains the path instead of deleting files beneath a possibly live
process or leaving an invisible stale registration.

Receipt `code` is a closed typed vocabulary. The coarser `outcome` and numeric
shell exit are derived from it, so those three representations cannot drift.
