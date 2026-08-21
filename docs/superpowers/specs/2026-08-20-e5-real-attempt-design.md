# E5 — One real attempt (design spec)

**Date:** 2026-08-20
**Phase:** E5 (current)
**Status:** accepted — implementation follows in the companion plan

**Correction, 2026-08-20, before implementation:** the accepted proposal
called patch/transcript paths "caller-owned" but did not say they must be
outside the disposable repository. Allowing an in-repository destination
would bypass E4's write boundary and could commit the transcript as product
code. E5 therefore rejects either artifact destination when its existing
parent and unresolved suffix place it inside the canonical repository. The
normal V3 seam already supplies sibling output paths, so this closes a policy
hole without changing that consumer.

**Correction, 2026-08-21:** final review found that “outside the current
repository” was not a sufficient publication boundary. An artifact can point
into the caller's source checkout, another registered worktree, or Git's
administrative/common directory; lexical `resolve()` checks can also be
bypassed by a case alias, and a validated parent can be replaced with a
symlink before publication. E5 therefore discovers every registered worktree
and both Git administrative roots, compares existing ancestors by filesystem
identity, opens and pins each accepted artifact parent with a directory
descriptor during preparation, and publishes the basename relative to that
descriptor without following links. No later path resolution chooses the
destination. Concurrent relocation of the already-open directory itself is a
caller filesystem action outside E5's portable guarantees; replacing its old
pathname cannot redirect publication.

The same review corrected four related boundaries. Tracked symlinks never enter
the E4 revision map. Temporary-contract, spool, artifact, and descriptor cleanup
failures are visible `ATTEMPT_FAILED` results with a retained recovery path;
cleanup does not silently turn a partial attempt into `OK`, and a secondary
cleanup exception never replaces a primary catchable exception. Model and
contract argv use option-safe forms (`--model=VALUE` and a literal `--`).
Contract containment uses filesystem-canonical existing paths and
path-component boundaries, so symlink or case aliases still identify an
in-repository contract while a name such as `..hidden.yaml` is not mistaken
for parent traversal. Asynchronous
stdin, stdout, and stderr failures, plus exceptions from the diagnostic sink,
are contained as named adapter refusals and complete the same terminate-then-
close lifecycle as timeouts.
Finally, the adapter's deadline is a backstop outside E3's attempt timeout: it
requests termination but reports timeout only after the outer delivery process
has closed. E3 remains responsible for stopping its attempt process group and
cleaning the detached worktree. A real delayed-writer regression must prove
that adapter cancellation leaves no descendant running.

**Correction, 2026-08-21, after cancellation review:** outer-child `close`
alone does not prove that the fixed E3 delivery process has finished
cooperative teardown. On POSIX the adapter starts that delivery in a detached
process group. A deadline or transport refusal that arrives before the Node
`spawn` event waits for that event unless a spawn error proves that no child
exists; after spawn, the first refusal remains authoritative while the adapter
sends `SIGTERM`. It reports the refusal only
after both the direct child has closed and a signal-0 probe reports `ESRCH` for
the outer group. An unknown or still-present group keeps the refusal pending.

The adapter deliberately does not force the POSIX outer group with `SIGKILL`.
E3 starts the model attempt in a separate session and owns its process-group
teardown plus worktree cleanup; killing E3 after an arbitrary grace period
could orphan that inner attempt. Windows retains the existing direct-child
TERM/KILL/close fallback and remains outside this phase's platform proof. The
one-shot E2/E4 transport keeps its narrower `Spawner`; these delivery-only
PID, spawn-event, and detached options live in a distinct `DeliverySpawner`.

These corrections narrow unsafe implementation choices; they do not add an
artifact service, process supervisor, or new phase.

## Goal

Run one real Pi model against one contract in the current disposable Git
worktree. `satyrn-engine attempt CONTRACT` gives the child only `read` and
E4's revision-checked `edit`, preserves Pi's JSONL transcript, and exports the
resulting Git patch when the caller supplies the V3 artifact seam.
`/implement CONTRACT` wraps that exact command in E3 delivery so an ordinary
source checkout stays untouched and a successful change becomes a candidate
ref.

This is the first complete vertical slice:

```text
source checkout
  -> E3 detached worktree
     -> E5 one Pi process
        -> read + E4 bounded replacement
     -> E3 candidate or discard
  -> delivery receipt + transcript
```

The model's prose and exit status are never a grading verdict. E3 decides
whether a candidate exists from the resulting Git tree; satyrn-evals V4 will
persist the exported patch and transcript, then grade the patch offline.

## Evidence and scope

The pinned evidence repository is `local-ai-pi@8588ba4`, used as evidence and
not copied as source:

- `docs/superpowers/research/2026-08-16-two-repo-rewrite-and-python-engine.md`,
  especially the E5/V4 roadmap rows and the executable engine seam;
- `docs/superpowers/handoff/satyrn-engine/ROADMAP.md`, E5;
- `harness/pi_invocation.py`, for the recorded Pi print-mode isolation flags
  and the user-resource contamination incidents;
- `docs/superpowers/handoff/HARVEST-INDEX.md`, for the no-enumeration wall,
  child resource contamination, repeated-call runaway, and model-authored
  output that could not be trusted as a verdict.

The installed Pi CLI confirms the E5 child surface: `--print --mode json
--no-session`, explicit `--extension`, discovery-disabling flags, and an exact
`--tools` allowlist.

satyrn-evals V3 already fixes the executable boundary:

- `satyrn-evals attempt TASK -- COMMAND...` owns the disposable workspace;
- it supplies `SATYRN_ATTEMPT_PATCH` and `SATYRN_ATTEMPT_TRANSCRIPT`;
- it persists both artifacts before cleanup and grades only the patch;
- command stdout and exit status are never authoritative.

E5 satisfies that boundary; V4 will provide an engine contract and select the
real command.

## Non-goals

E5 does not add packaging, publishing, Windows proof, multiple tasks, retries,
batching, an orchestrator agent, subagents, shell access, file creation,
whole-file writes, multiple replacements, validation commands, oracle
execution, grading, performance claims, model comparison, a persistent Python
process, or a general transcript schema.

E5 does not make a direct `attempt` safe for a user's ordinary checkout. It is
deliberately the command that runs *inside* an already-disposable workspace.
The human-safe source-checkout surface is `/implement`, which supplies E3.

## Product surfaces

### Attempt CLI

```text
satyrn-engine attempt [--model MODEL] CONTRACT
```

- The repository is the process's current working directory. There is no
  `--repo`: requiring the caller to enter the disposable worktree makes the
  ownership boundary visible.
- `MODEL` is selected by `--model`, then `SATYRN_MODEL`. If neither is
  present, the command is a usage error. E5 never silently chooses Pi's
  configured default because the model identity must be reproducible.
- `CONTRACT` is resolved from the current working directory. It may be inside
  or outside the worktree.
- `attempt` runs once and never creates another worktree, candidate, receipt,
  retry, or validation phase.

The attempt writes project files only through E4 inside the current worktree.
Two explicitly caller-owned artifact paths are the exception:

| environment variable | bytes written |
|---|---|
| `SATYRN_ATTEMPT_TRANSCRIPT` | exact Pi JSONL stdout, including an empty stream |
| `SATYRN_ATTEMPT_PATCH` | exact non-empty Git diff after the model exits |

An absent variable means that artifact is not written. Both destinations must
be outside every registered worktree and outside Git's administrative and
common directories. Transcript bytes are also forwarded to
attempt stdout. Pi diagnostics are forwarded to attempt stderr. An empty diff
leaves `SATYRN_ATTEMPT_PATCH` absent, matching V3's `NO_PATCH` distinction.

Artifact publication is atomic, exclusive, and no-follow: the validated parent
is opened and pinned by descriptor during preparation, a sibling temporary
file is flushed, then linked to
the previously absent basename relative to that same descriptor only after the
complete bytes exist. A pre-existing artifact path, symlink, non-regular
parent, identity change, or write failure is an accepted operation failure,
not permission to overwrite unrelated data. The descriptor remains owned by
the attempt until every result or exception path has tried to close it exactly
once.

### `/implement CONTRACT`

The installed TypeScript package keeps the slash-command spelling. E5 changes
its meaning from E2's check-only architecture probe to the real vertical
slice:

1. resolve the source repository and contract from Pi's current directory;
2. require `SATYRN_ENGINE_REPO` and `SATYRN_MODEL`;
3. invoke E3 `deliver` with that contract and this repository's `attempt`
   command;
4. parse the one E3 delivery receipt and report its code, candidate ref, and
   candidate commit;
5. convert start, timeout, malformed receipt, stream, diagnostic-sink, and
   transport failures into named adapter refusals without throwing from the Pi
   handler.

When a contract is inside the source repository, the inner attempt receives
its repository-relative path so it reads the contract from the exact detached
base it will modify. An external contract remains an absolute read-only input.

The adapter does not implement delivery, mutation, Git policy, prompt policy,
or grading. It only constructs and observes the command boundary. E3 remains
the process-group, timeout, worktree, candidate, and cleanup owner.

## Attempt preparation

### Repository and frozen contract

`attempt` first runs E1 check against the current directory, then requires a
Git worktree with a readable `HEAD`. It loads the accepted contract once and
copies the exact bytes to an engine-owned temporary file outside the
worktree. E4 requests point at this frozen copy.

Freezing is a policy boundary: even if a contract mistakenly declares its own
path writable, one model edit cannot expand permissions for a later tool call.
The temporary contract is removed after Pi exits.

### Revision map

The engine enumerates tracked paths with a NUL-delimited, engine-owned Git
query. For every existing regular file reached without following any symlink
component, whose safe POSIX relative path matches one of
`contract.writable_paths` under E4's `fnmatch` rule, it records the SHA-256 of
the exact bytes.

The resulting E4 mutation context is:

```json
{
  "version": 1,
  "repo": "/absolute/disposable/worktree",
  "contract": "/private/tmp/frozen-contract.yaml",
  "revisions": {
    "src/app.py": "<64 lowercase hex>"
  }
}
```

No matching existing file is `ATTEMPT_FAILED`; E4 does not create files, so a
model run could not succeed. The sorted list of writable files is included in
the prompt, closing the recorded Envelope arm's no-enumeration wall without
granting `ls`, `find`, or shell access.

The Git boundary disables hooks, fsmonitor, external diff, textconv, replace
refs, graft overlays, prompts, and optional locks where relevant. It preserves
ordinary filters for the checked-out worktree, matching E3's trust boundary.

## One Pi child

The exact child shape is conceptually:

```text
pi --print --mode json --no-session --model=MODEL
   --no-extensions
   --extension packages/engine/engine.ts
   --extension packages/engine/mutator.ts
   --no-skills --no-prompt-templates --no-themes --no-context-files
   --no-approve
   --tools read,edit
   PROMPT
```

`engine.ts` supplies the E3.5 loop breaker. `mutator.ts` replaces the built-in
edit because `SATYRN_MUTATION_CONTEXT` and `SATYRN_ENGINE_REPO` are explicit.
The child does not load `orchestrator.ts`, so it cannot recursively invoke
`/implement`.

The prompt contains only:

- the contract's task text;
- the exact sorted writable-file list;
- the facts that it may read files, must use the bounded edit tool for writes,
  must not create files, and should stop when the task is complete.

There is no pre-chewed implementation plan, hidden test result, oracle, or
reference patch. That distinction preserves the headroom Paul described:
high-level outcomes are delegated, deterministic mechanics stay in tools.

The parent environment is copied, then repository-routing Git variables,
`VIRTUAL_ENV`, and `SSH_AUTH_SOCK` are removed. The harness virtualenv and an
agent socket must not become tools available to a child. Model credentials and
the user's Pi model catalog remain available; discovery flags prevent ambient
extensions, skills, templates, themes, and context files from changing the
run.

## Patch and transcript

Pi stdout is spooled to a regular temporary file while the direct child runs.
This avoids a descendant-held pipe and unbounded memory, following E3's
recorded process lesson. After the child is reaped, those exact bytes are
atomically exported to `SATYRN_ATTEMPT_TRANSCRIPT` when requested and copied
to attempt stdout.

The patch is produced after Pi exits with a binary-capable, no-ext-diff,
no-textconv, no-color Git diff against the captured `HEAD`. It is never parsed
or graded by the engine. A non-empty patch is atomically exported to
`SATYRN_ATTEMPT_PATCH`; an empty patch is not written.

Artifacts are attempted even when Pi exits nonzero so the failure remains
diagnosable. Artifact failure supersedes the Pi result and returns
`ATTEMPT_FAILED`.

## Results and exit codes

`attempt` has no JSON receipt; E3 owns the product receipt and evals owns the
attempt record. The direct command uses a small typed library result and one
diagnostic line on refusal:

| exit | code | meaning |
|---:|---|---|
| 0 | `OK` | Pi exited zero; requested artifacts were published |
| 2 | `USAGE` | missing model or malformed CLI |
| 3–6 | existing E1 codes | contract or repository check refused before Pi |
| 10 | `ATTEMPT_FAILED` | accepted attempt could not prepare Git/context, start or complete Pi, or publish artifacts |

Exit 1 remains reserved for an unexpected Python exception. A nonzero Pi exit
is normalized to 10 after transcript and patch preservation. `/implement`
receives that as E3 `COMMAND_FAILED`; evals records it but never treats it as
the verdict.

## Types and seams

Python adds:

- `AttemptCode(StrEnum)` for detailed result identity;
- `AttemptResult`, `AttemptContext`, and `AttemptArtifacts` as frozen,
  slotted dataclasses;
- a `PiRunner` protocol used by production subprocess execution and default
  tests;
- typed command/environment builders and explicit code-to-exit mapping.

TypeScript adds discriminated receipt and adapter-result types. Production
spawning and the test double share one `DeliverySpawner` interface; there is
no second plugin framework. The existing one-shot `Spawner` remains unchanged.

## Test evidence

### Default tier

No model, network, Git, Node, or subprocess:

- CLI model precedence and stable exits;
- prompt and exact hermetic Pi argv;
- revision-context and artifact decisions through injected Git/Pi seams;
- success plus siblings for no files, Pi unavailable/nonzero, Git failure,
  transcript failure, patch failure, and malformed adapter receipt;
- TypeScript `/implement` command construction and every named transport
  refusal with an injected spawner;
- exhaustive mappings and 100% statement/branch coverage.

### Integration tier

Marked `integration`, excluded from CI:

- a fake `pi` executable in a real Git repo proves argv, environment,
  transcript, patch, nonzero preservation, and no-change behavior;
- a real Node mutator exchange drives TypeScript → Python E4 in the current
  worktree;
- a real E3 delivery around the fake attempt produces a candidate while the
  caller checkout remains unchanged;
- package-install replay proves only the intended child extensions load;
- all refusal cases have a successful sibling.

### Live evidence

A separately gated command, never default or CI, records one real model run.
Completion requires naming:

- Pi version and exact model string;
- contract and base commit;
- non-empty transcript artifact;
- E3 receipt and candidate ref;
- proof that the candidate changed only declared files and the caller checkout
  stayed clean.

## Done when

E5 is complete when direct `attempt` and `/implement` share one execution path;
the fake integration slice proves E3 + E4 end to end; one gated real-model run
leaves a transcript, receipt, and candidate ref; the caller tree remains clean;
default/integration/TypeScript gates pass; and all Python plus phase-owned
TypeScript has 100% statement and branch coverage.
