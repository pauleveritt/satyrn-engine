# E3 — Delivery (design spec)

**Date:** 2026-08-18
**Phase:** E3 (current)
**Status:** draft — revised after architecture review

## Goal

Deliver one user-visible behavior:

```console
satyrn-engine deliver --repo REPO [--timeout SECONDS] CONTRACT -- COMMAND [ARG ...]
```

The engine runs `COMMAND` once in a detached Git worktree at the repository's
exact `HEAD`. If the command succeeds and changes the tree, the engine creates
a commit and publishes it as a candidate ref. Otherwise it discards the
worktree and publishes no candidate.

This isolates engine-owned Git writes from the caller's working tree, index,
branch, and `HEAD`. It is not a security sandbox: `COMMAND` is trusted and can
write absolute paths, deliberately escape its process group, or mutate shared
Git state. E3 adds lightweight POSIX process-group teardown so an ordinary
timed-out command cannot leave its children running, but it adds no container,
filesystem sandbox, or general process supervisor. E3 proves delivery with a
deterministic executable standing in for a model. E4 adds writable-path and
revision enforcement; E5 adds validation and one real attempt.

## Identity and candidate ref

E3 uses identities Git already provides instead of introducing a UUID,
database, slug, escaping scheme, or allocator:

- `contract.id` is the logical identity of the requested change.
- The candidate commit SHA is the identity of this exact revision.
- The pair `(Git shared-ref namespace, contract.id)` is unique. Different
  spellings or linked worktrees of the same repository share that namespace;
  atomic ref creation supplies the uniqueness constraint.

This is the same separation used by Gerrit, where a Change-Id survives new
patch-set commits, and by GitHub's pull-request refs, where the pull-request
number and terminal `head` role are distinct from the commit they name.
Database terms lead to the same result: this is a repository-scoped composite
key, with Git enforcing the unique constraint.

The candidate ref is:

```text
refs/satyrn/candidates/<contract-id>/head
```

The terminal `head` leaves room for later revision or metadata refs without
adding that machinery now. The engine first rejects any contract id containing
`/`, because the id must be exactly one ref component. It then constructs the
full ref and validates it with `git check-ref-format` without `--normalize`.
It never rewrites, case-folds, normalizes, or hashes the id: two visible ids
cannot silently become the same candidate. An invalid id, including
`team/foo`, produces `INVALID_CANDIDATE_ID` before a worktree is created.

Publication uses:

```console
git update-ref --no-deref REF COMMIT ""
```

The empty expected-old value is an object-id compare-and-swap requiring no
direct ref to exist, so exactly one cooperating delivery process wins a race.
Preflight refuses an existing ordinary or symbolic ref as
`CANDIDATE_EXISTS`; `--no-deref` ensures publication updates the candidate ref
itself rather than a symbolic target. Git's object-id CAS does not atomically
distinguish absence from a concurrently introduced dangling symbolic ref, so
external mutation of this ref during delivery remains part of the trusted
shared-Git-state boundary. Adding a separate lock protocol for that adversarial
case is not justified in E3. Updating, listing, and deleting candidates are
also deferred.

Sources: [Git ref-name rules](https://git-scm.com/docs/git-check-ref-format),
[Git ref transactions](https://git-scm.com/docs/git-update-ref),
[GitHub pull-request refs](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/checking-out-pull-requests-locally),
[Gerrit Change-Id](https://gerrit-review.googlesource.com/Documentation/user-changeid.html),
[Gerrit change refs](https://gerrit-review.googlesource.com/Documentation/access-control.html#references),
and [PostgreSQL unique constraints](https://www.postgresql.org/docs/current/ddl-constraints.html#DDL-CONSTRAINTS-UNIQUE-CONSTRAINTS).

## CLI boundary

`CONTRACT` and `COMMAND` are separated by a literal `--`. At least one command
token is required. CLI syntax errors, including a missing command, use
argparse's stderr and exit `USAGE = 2`; no receipt exists because no operation
was accepted.

The command is invoked as its exact argument vector with `shell=False`, the
isolated worktree as `cwd`, and stdin connected to `DEVNULL`. It inherits the
caller's environment, including `PATH` and normal Git configuration, except
that repository-local routing variables and `GIT_NAMESPACE` are removed so a
Git command discovers the isolated worktree. E3 does not send the contract or
task through argv, environment, or stdin. Command stdout and stderr are
forwarded to the engine's stderr so stdout remains machine-readable.

The default timeout is 30 seconds. On POSIX the engine starts `COMMAND` in a
new session, making the direct child the leader of a new process group. On
timeout it sends `SIGTERM` to that group, waits up to five seconds for the
process group to exit, then sends `SIGKILL` to any group that remains and reaps
the direct child. A missing process group during either signal is treated as
already exited. Process-group teardown completes before worktree cleanup
begins.

This is bounded cleanup, not adversarial descendant tracking. `COMMAND` is
expected to run synchronously and not call `setsid()` or otherwise move a
descendant out of the engine-created process group. Cancellation, retry, and
repair remain outside E3. A timeout value must be finite and greater than zero;
zero, negative, NaN, and infinite values are CLI `USAGE = 2` errors before
delivery starts.

## Receipt and exit behavior

Every accepted operation writes exactly one UTF-8 JSON object followed by a
newline to stdout. An unexpected engine bug remains an uncaught exit `1` and
does not fabricate a receipt.

The receipt always has the same fields:

| field | type | populated when |
|-------|------|----------------|
| `version` | integer | always `1` |
| `outcome` | string | always: `candidate-created`, `discarded`, or `refused` |
| `code` | string | always; authoritative specific result |
| `message` | string | always; human-readable detail |
| `contract_id` | string or null | contract parsing succeeds |
| `repository` | string | always; absolute normalized input path, without resolving symlinks |
| `base_commit` | string or null | Git preflight resolves `HEAD^{commit}` |
| `candidate_ref` | string or null | the candidate id validates |
| `candidate_commit` | string or null | `commit-tree` succeeds, whether publication wins or not |
| `changed_paths` | array of UTF-8 strings or null | final diff succeeds; sorted by UTF-8 bytes; `[]` means known empty, null means not evaluated or unavailable |
| `command_exit` | integer or null | the direct child exits; unavailable and timeout remain null |
| `worktree_path` | string or null | cleanup fails and operator action is required |

A successful receipt is therefore shaped as follows:

```json
{"version":1,"outcome":"candidate-created","code":"OK","message":"candidate created","contract_id":"greeting","repository":"/src/app","base_commit":"<sha>","candidate_ref":"refs/satyrn/candidates/greeting/head","candidate_commit":"<sha>","changed_paths":["greeting.py"],"command_exit":0,"worktree_path":null}
```

On an early refusal the still-unknown values are null. `NO_CHANGES` reports
`changed_paths: []`; a command failure leaves paths null and records its
nonzero `command_exit`. On publication loss,
`candidate_commit` and `changed_paths` describe the unpublished commit and the
code is `CANDIDATE_EXISTS`. `CLEANUP_FAILED` replaces any pending result, names
that displaced result in `message`, and retains the path in `worktree_path`.

Success exits `0`. Existing E1 failures keep codes `3`–`6`, and protocol input
keeps `INVALID_REQUEST = 7`. All new handled outcomes that create no candidate
exit `NO_CANDIDATE = 8`; automation reads the receipt `code` for the precise
reason. This deliberately keeps the shell surface binary instead of assigning
a numeric exit code to every Git or process detail.

| receipt `code` | outcome | condition |
|----------------|---------|-----------|
| E1's `CONTRACT_*`, `REPO_UNAVAILABLE` | `refused` | inherited preflight refusal |
| `REPO_NOT_GIT`, `REPO_DIRTY` | `refused` | unsupported source state |
| `INVALID_CANDIDATE_ID`, `CANDIDATE_EXISTS` | `refused` | candidate identity cannot be created |
| `COMMAND_UNAVAILABLE` | `refused` | the direct child cannot start |
| `COMMAND_TIMEOUT`, `COMMAND_FAILED`, `COMMAND_CHANGED_HEAD` | `discarded` | command did not yield an acceptable tree |
| `NO_CHANGES` | `discarded` | command succeeds but the tree is unchanged |
| `GIT_FAILED`, `CLEANUP_FAILED` | `refused` | named engine-owned Git or cleanup operation fails |
| `OK` | `candidate-created` | the candidate ref is created atomically |

`outcome` describes the candidate lifecycle; `code` describes the specific
cause. E3 deliberately does not expose `infrastructure-failure` as a fourth
outcome. A generic `COMMAND` boundary cannot reliably distinguish a model
failure from a broken model server, missing executable, or other setup failure.
Later model and eval phases may classify their own evidence without changing
this delivery receipt.

## Workflow

The supported E3 evidence target is a normal, non-bare POSIX working
repository with at least one commit. The supplied `--repo` must name its root
(a symlink to that root is accepted), and its tracked, deleted, and untracked
state must be clean. The source may move concurrently after preflight; the
candidate remains based on the captured commit.

```text
START
  |
  v
E1 check -> resolve Git root and exact HEAD -> require clean source
  | refused / not Git / dirty
  +----------------------------------------------------------> REFUSED
  v
construct and validate refs/satyrn/candidates/<id>/head
  | invalid / already exists
  +----------------------------------------------------------> REFUSED
  v
add detached temporary worktree at BASE
  | Git failure
  +--> cleanup if registered (failure => CLEANUP_FAILED) ---> GIT_FAILED
  v
run trusted COMMAND once
  | unavailable / timeout / nonzero
  +--> cleanup (failure => CLEANUP_FAILED) ------------> REFUSED/DISCARDED
  v
require detached HEAD == BASE -> git add -A -> compare tree with BASE
  | moved HEAD / no changes
  +--> cleanup (failure => CLEANUP_FAILED) --------------------> DISCARDED
  v
write tree -> commit-tree with parent BASE -> derive changed paths
  | Git failure
  +--> cleanup (failure => CLEANUP_FAILED) --------------------> GIT_FAILED
  |
  v
remove worktree
  | failure
  +--------------------------------------------------> CLEANUP_FAILED
  v
update-ref --no-deref REF COMMIT ""
  | ref now exists                 | other Git failure
  +--> CANDIDATE_EXISTS            +---------------------> GIT_FAILED
  v
CANDIDATE_CREATED
```

The ref transition is intentionally just create-if-absent:

```text
ABSENT -- update-ref(REF, COMMIT, expected=absent) --> CREATED
   |                         |
   | another creator wins    | other failure
   v                         v
EXISTS                  ABSENT OR UNKNOWN
```

An initial lookup gives an early, useful refusal; `update-ref` is still the
race-safe decision. After an update failure the engine re-reads both symbolic
and ordinary ref state. A now-existing ref is `CANDIDATE_EXISTS`; any verified
absence or unreadable state is `GIT_FAILED`. E3 intentionally exposes both of
the latter as one machine result: callers must not infer retry safety from a
diagnostic lookup. The receipt therefore does not add a second `ref_state`
axis; `code` and the candidate fields describe its stable result.

Every handled path after worktree registration passes through the same
cleanup operation, `git worktree remove --force PATH`. Force is needed because
candidate state is dirty or staged. A locked worktree intentionally remains a
visible `CLEANUP_FAILED`; manual recovery is:

```console
git worktree unlock PATH
git worktree remove --force PATH
git worktree prune
```

A locked worktree can alternatively be removed by giving `--force` twice. The
temporary parent is retained until Git confirms the linked worktree is absent.
Abrupt termination can still leave an engine-owned worktree; automatic crash
recovery is deferred.

The registered-worktree lifetime is enclosed by `try`/`finally`. A guard is set
only after Git confirms registration and cleared after confirmed removal, so
normal cleanup is not repeated. After an unexpected Python exception, the
engine attempts cleanup while the guard is set and then re-raises the original
error as exit `1`, without inventing a receipt. If cleanup also fails, stderr
reports the retained path without hiding the original exception. Signals that
prevent `finally` from running, such as `SIGKILL`, still require the manual
recovery above.

The new commit is created before cleanup and published afterward. A crash or
lost race can therefore leave a dangling Git object, but never a partial
candidate ref or an engine write in the caller's tree. A temporary pin ref is
not justified in E3.

## Git and implementation decisions

| decision | reason |
|----------|--------|
| Resolve `--repo` with `git rev-parse --show-toplevel` and pin `HEAD^{commit}` | Operate from the root and base every later action on an immutable commit. [git-rev-parse](https://git-scm.com/docs/git-rev-parse) |
| Check `git --no-optional-locks status --porcelain=v1 -z --untracked-files=all --ignore-submodules=none` | Stable, NUL-safe scripting output; no optional caller-index refresh. [git-status](https://git-scm.com/docs/git-status), [git](https://git-scm.com/docs/git#Documentation/git.txt---no-optional-locks) |
| Remove repository-local routing variables named by `git rev-parse --local-env-vars`, plus `GIT_NAMESPACE`, from engine and command environments | Prevent inherited `GIT_DIR`, `GIT_WORK_TREE`, and ref namespace from redirecting operations. Git documents the foreign-repository pattern; `GIT_NAMESPACE` is an additional ref-routing variable not in that reported list. [git-rev-parse](https://git-scm.com/docs/git-rev-parse), [gitnamespaces](https://git-scm.com/docs/gitnamespaces), [githooks](https://git-scm.com/docs/githooks) |
| Preserve normal system, global, and repository config; override `core.hooksPath` with an engine-owned empty directory | Standard Git features such as LFS and clean/smudge filters continue to work, while engine-owned checkout does not run repository hooks. Filters remain part of the trusted-repository boundary. [git-config](https://git-scm.com/docs/git-config), [githooks](https://git-scm.com/docs/githooks) |
| Use `git worktree add --detach PATH BASE` in a unique temporary parent | Isolate index and tree while sharing Git objects. [git-worktree](https://git-scm.com/docs/git-worktree), [tempfile](https://docs.python.org/3/library/tempfile.html) |
| Start `COMMAND` with `start_new_session=True`; on timeout terminate its POSIX process group with `SIGTERM`, then `SIGKILL` after five seconds | Bound the attempt and its ordinary descendants without introducing a container or persistent supervisor. This re-earns a behavior retained by the earlier prototype. [subprocess](https://docs.python.org/3/library/subprocess.html), [os.killpg](https://docs.python.org/3/library/os.html#os.killpg) |
| Stage with `git add -A`, then `write-tree` and `commit-tree TREE -p BASE` | Include additions, modifications, and deletions and make the parent explicit without editor or signing behavior. Use identity `satyrn-engine <satyrn-engine@localhost>`, disable signing, and pass message `candidate: ID` plus `base: SHA` on stdin. [git-add](https://git-scm.com/docs/git-add), [git-write-tree](https://git-scm.com/docs/git-write-tree), [git-commit-tree](https://git-scm.com/docs/git-commit-tree) |
| Derive paths from `git diff-tree --no-commit-id --name-only -r -z --no-renames --no-ext-diff BASE COMMIT` | The final commit is authoritative. Split NUL-delimited bytes, require UTF-8, and sort by those bytes so Git config and locale cannot change the receipt. Non-UTF-8 paths produce `GIT_FAILED` and no publication rather than a new encoding scheme. [git-diff-tree](https://git-scm.com/docs/git-diff-tree) |

Preflight, worktree add/removal, registration checks, and ref lookup/publication
run from the canonical source root. `add`, tree comparison, `write-tree`,
`commit-tree`, and `diff-tree` run from the isolated worktree. This cwd split is
part of the isolation guarantee, not an implementation convenience.

The Python surface stays small: `delivery.py` owns `DeliveryReceipt` and the
`deliver()` lifecycle; `cli.py` owns parsing and rendering; `exits.py` adds
`NO_CANDIDATE`. E3 adds no Python dependency. Git is an explicit external
runtime requirement. There is no injected Git-runner or test-only failure
hook: the production library seam is the test and extension seam.

The earlier prototype is evidence, not source. Its candidate lifecycle and
process teardown were inspected at the repository's pinned handoff revision
[`8588ba4`](https://github.com/pauleveritt/local-ai-pi/tree/8588ba4), especially
[`harness/candidate.py`](https://github.com/pauleveritt/local-ai-pi/blob/8588ba4/harness/candidate.py),
[`harness/processes.py`](https://github.com/pauleveritt/local-ai-pi/blob/8588ba4/harness/processes.py),
the [two-repository rewrite](https://github.com/pauleveritt/local-ai-pi/blob/8588ba4/docs/superpowers/research/2026-08-16-two-repo-rewrite-and-python-engine.md),
and the [harvest index](https://github.com/pauleveritt/local-ai-pi/blob/8588ba4/docs/superpowers/handoff/HARVEST-INDEX.md).
No implementation is copied from it.

## Testing and phase work

Default-tier tests cover parsing, timeout validation, receipt construction,
the pure `/` id rejection, and result precedence. Git-specific ref-name rules
are integration-only because `git check-ref-format` is a real subprocess. The
default tier preserves E1's process and network tripwire. Marked integration
tests use temporary local Git repositories and production CLI seams—never a
network or test-only injected backend—to prove:

1. Success creates one commit with parent `BASE`, publishes the expected ref,
   reports added/modified/deleted paths, removes the worktree, and leaves the
   caller tree, index, branch, and `HEAD` unchanged.
2. No-op, nonzero, unavailable, timeout, and moved-`HEAD` commands emit their
   exact receipts, publish no ref, and clean up. The timeout fixture starts a
   same-process-group descendant that would write a delayed sentinel; teardown
   prevents the write before the worktree is removed.
3. Dirty repositories, invalid ids, and existing ordinary, symbolic, and
   dangling-symbolic refs refuse without engine-owned mutation.
4. A locked worktree proves `CLEANUP_FAILED`, outcome precedence, retained-path
   reporting, and explicit test teardown.
5. Two real CLI processes race on the same id; exactly one creates the ref and
   the other reports `CANDIDATE_EXISTS`.
6. An ancestor ref that collides with the candidate namespace produces
   `GIT_FAILED`; the sibling success uses the same id without that collision.
7. UTF-8 paths have canonical byte order; a POSIX non-UTF-8 path produces
   `GIT_FAILED` and no candidate, paired with a UTF-8 success fixture.
8. A hook sentinel does not fire, while a normal repository filter remains
   usable with the preserved Git configuration.

E3 also updates `README.md`, `ROADMAP.md`, architecture, usage, glossary,
the documentation index, contributing guidance, and the pytest
integration-marker description, plus the test that pins the complete
`ExitCode` table. It adds exact committed receipt fixtures for `OK`,
`REPO_DIRTY`, `COMMAND_FAILED`, `CANDIDATE_EXISTS`, and `CLEANUP_FAILED`; the
SDD record names commands and evidence for both success and failure. Those
changes belong in the implementation plan and phase commit, not in this
spec-only change.

Planned completion commands:

```console
uv run pytest
uv run pytest -m integration tests/test_delivery.py -v
uv run ruff check .
uv run pyrefly check
uv run --group docs sphinx-build -W -b html docs docs/_build/html
```

## Explicitly outside E3

- Pi, model selection, task-to-command handoff, and contract authoring.
- Writable-path and revision enforcement (E4), then validation and a real
  attempt (E5).
- A security sandbox, containment of descendants that deliberately escape the
  engine-created process group, retry, repair, or cancellation.
- Applying, updating, listing, or deleting candidates; UUIDs and patch sets.
- Special behavior or evidence for bare or unborn repositories, sparse
  checkouts, submodule mutation, and Windows. Cross-platform packaging is E6.
- A persistent service, JSON-RPC, plugins, or CI execution of integration tests.
