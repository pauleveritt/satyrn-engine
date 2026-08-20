# Architecture

**One bounded attempt, delivered as Git state.** The {term}`adapter` starts
one E3 delivery process. Inside its detached worktree, E5 starts one explicit
Pi model with only `read` and E4's bounded `edit`. The result is either a
reviewable candidate ref or a named refusal; the caller's checkout is never the
model workspace.

## The shape

```text
parent Pi /implement
        │
        ▼
satyrn-engine deliver ── detached worktree at exact HEAD
        │
        ▼
satyrn-engine attempt ── explicit child Pi model
        │                       │
        │                       ├── read
        │                       └── bounded edit
        │                              │ one JSON request/response
        │                              ▼
        │                     Python mutation protocol
        ▼
candidate ref or named delivery refusal
```

The E5 `/implement CONTRACT` flow is:

1. The parent adapter resolves `CONTRACT` against `ctx.cwd` and requires an
   explicit `SATYRN_MODEL`.
2. It starts `satyrn-engine deliver`, whose trusted command is
   `satyrn-engine attempt --model MODEL CONTRACT` with transcript and patch
   destinations outside the repository.
3. Delivery captures the caller's exact `HEAD`, creates a detached linked
   worktree, and runs the command once there.
4. Attempt freezes the contract and exact writable-file revisions, then starts
   Pi with only the shipped `engine.ts` and `mutator.ts` extensions and the
   `read,edit` tool set.
5. Every edit crosses the one-shot JSON protocol into Python. The bounded
   mutation seam owns path, revision, and unique-anchor checks.
6. Delivery cleans the worktree and either atomically publishes a candidate
   ref or returns one named refusal. The adapter reports that receipt without
   reinterpreting it.

## Who owns what

| side | owns |
|------|------|
| {term}`engine` | contract and mutation semantics plus delivery: parsing, path linting, writable-path matching, revision and anchor checks, isolated Git execution, one model attempt, candidate publication, receipts; the `check` seam, the {term}`protocol` surface, and stable exit codes |
| {term}`adapter` | transport — spawning, the deadline, refusal conversion — and Pi's command/tool surfaces. It never reinterprets an engine refusal or duplicates path policy |

The split keeps the adapter thin: Pi-specific concerns live in TypeScript;
contract, mutation, Git, and attempt semantics live in Python, shared with the
CLI and any future caller.

## Why one process per operation

`BRIEF.md` settled this. A process per operation is slower and much
smaller: no session lifetime, no lifecycle question, nothing to clean up.
If startup cost is later *measured* as material, the same request and
response objects gain a persistent transport then — not before.

## Why the adapter carries its own deadline and exception boundary

Verified Pi facts (v0.84.2) from `BRIEF.md`: extension handlers are awaited
sequentially with no host deadline, and an uncaught adapter error escapes the
turn. The check/protocol transport therefore keeps its short deadline, while
E5 gives the nested model attempt fifteen minutes plus a small delivery margin.
Both paths convert spawn errors, timeouts, crashes, and malformed responses
into contained results instead of letting them escape.

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
TypeScript: translate JSON with the known revision or explicit null
       │ one replace request
       ▼
Python: normalize path → match writable_paths → check revision
        → require one anchor → atomically replace → return next revision
```

Python owns every permission and mutation decision. Its `fnmatch` behavior is
the reference contract, it reads the exact file bytes, and it returns the next
{term}`revision`. TypeScript neither reads a file nor hashes one; even a path
missing from its map reaches Python, which checks contract authorization before
returning `REVISION_UNAVAILABLE`. Python opens every target component without
following symlinks. A determinate engine refusal leaves both the file and the
in-memory map unchanged. A transport failure is different: publication may
already have happened, so TypeScript poisons that mutation context and permits
no later edit; E5 discards its isolated worktree.

The replacement is intentionally one anchor in one existing UTF-8 file. File
creation, whole-file writes, multiple edits, fuzzy matching, symbol analysis,
and validation are not hidden behind an abstraction. E5 supplies the context
inside E3's worktree and consumes the same seam.

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
JSON `code` distinguishes `PATH_UNDECLARED`, `REVISION_UNAVAILABLE`, `REVISION_STALE`,
`ANCHOR_MISSING`, `ANCHOR_AMBIGUOUS`, and `MUTATION_FAILED`.
An E5 model process that cannot start or exits nonzero uses `ATTEMPT_FAILED`
and exit `10`; when wrapped by delivery that becomes `COMMAND_FAILED`. The E3
owner reports an expired outer deadline separately as `COMMAND_TIMEOUT`.

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
synchronous command. E4 supplies mutation rules when its tool is used; E5
connects them to delivery and a real attempt. Grading remains in satyrn-evals.

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
