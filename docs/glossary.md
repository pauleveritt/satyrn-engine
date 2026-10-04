# Glossary

Terms earn a place here when a phase lands the concept; the roadmap's concept
budget tracks which names the design actually needs.

```{glossary}
adapter
  The thin TypeScript layer that makes the engine reachable inside Pi as the
  ``/implement`` command and, with an explicit mutation context, a bounded
  ``edit`` tool. ``/implement`` starts E3 delivery with an E5 attempt inside;
  each bounded edit sends one versioned request to the Python mutation
  protocol. It converts transport failures into contained results and owns no
  contract, mutation, or Git policy.

attempt
  One E5 run of an explicitly selected Pi model. Standalone invocation expects
  the caller to supply a clean disposable Git worktree; it does not create
  isolation itself. ``/implement`` supplies that worktree through E3 delivery.
  The model receives Pi's native ``read``, ``bash`` and ``write``, E4's bounded
  ``edit`` in place of Pi's, and ``self_test`` only when the contract declares
  a ``test_command``. The transcript
  and patch are evidence artifacts published through pinned directories
  outside every registered worktree and Git administrative directory, not a
  grading verdict; E3 decides whether the resulting tree becomes a candidate.

check
  The engine's first operation: parse and validate a contract, lint the
  repository path it names, and either accept it (exit ``0``) or refuse
  with a named cause. Exposed as the ``check`` subcommand and the
  ``check()`` library seam.

carried
  ``preserve`` and ``checks`` paths (plus tracked test infrastructure)
  restored from the accepted base into the worktree before every
  {term}`self_test` run and before validation, so the model's edits to them
  never count.

argument repair
  One of Pi 0.85.1's three repairs to an ``edit`` call before validation,
  ported as ``prepareEditArguments`` in ``packages/engine/mutator.ts``:
  ``edits`` sent as a JSON string that parses to an array or one edit, a bare
  edit object in ``edits``, and legacy top-level ``oldText``/``newText``. It
  returns a new object rather than mutating the model's call, and never reads,
  moves or invents a path, so per-item, nested and multi-file shapes stay
  refused.

candidate
  A commit produced by one successful delivery attempt and published at
  ``refs/satyrn/candidates/<contract-id>/head``. It has the captured base
  commit as its parent. It is reviewable Git state, not a branch, merge, or
  automatic change to the caller's checkout.

candidate ref
  The create-once Git ref naming a {term}`candidate`. The contract id is the
  logical identity and the commit SHA is the exact revision. Git's shared ref
  namespace makes the identity the same across symlink spellings and linked
  worktrees of one repository.

contract
  A bounded, declarative description of a change to make, written as a
  YAML file. Its top level is a mapping with two required fields, ``id``
  and ``task`` (both non-empty strings). E4 adds optional ``writable_paths``
  patterns; omitting them permits no bounded replacement. Unknown fields are
  ignored. See {doc}`usage` for the accepted shape. The spec's ``objective``
  and ``self_test_command`` are this file's ``task`` and ``test_command``.

confinement root
  The ``SATYRN_CONFINEMENT_ROOT`` variable a caller's confinement extension
  reads. When the caller set it, the attempt points it at its own worktree
  for the inner Pi; it never adds the variable, and
  ``SATYRN_CONFINEMENT_ROOTS`` passes through untouched
  (``src/satyrn_engine/attempt.py``).

edit parity
  The Engine's ``edit`` presents Pi 0.85.1's tool description, parameter
  descriptions and guidelines, verbatim except three sentences adapted
  because this tool applies entries in order against the evolving buffer,
  and runs Pi's {term}`argument repair` before validation
  (``packages/engine/mutator.ts``).

engine
  The Python core of satyrn-engine: a library and command-line tool that
  parses and validates a contract, applies one bounded replacement, runs one
  model attempt, and delivers a candidate change without modifying the
  caller's working tree. Invoked from the shell as ``satyrn-engine``.

exit code
  The process exit status returned by ``satyrn-engine``. The values are a
  stable contract: ``0`` succeeds; ``2`` through ``7`` retain the check and
  protocol meanings; delivery uses ``8`` for every handled result without a
  candidate; mutation uses ``9`` for an accepted replacement refusal; attempt
  failures use ``10``; and ``1`` is reserved for an uncaught internal error —
  a crash, never a refusal. A delivery receipt or mutation JSON response gives
  the precise cause.

finish nudge
  The steer ``packages/engine/runner.ts`` sends when a ``self_test`` run
  inside a turn passes after a change to a non-test path, at most once per
  such change: it tells the model to stop and report if the change is
  complete, and stops nothing itself. Each one records a ``finish_nudged``
  {term}`guard firing`.

guard
  A small TypeScript check that observes an ordinary Pi tool call before it
  runs. Four guards ship: the loop breaker, which remembers the last twenty
  admitted call keys and refuses a sixth exact repeat while five matches remain
  in that window, and runs in every Pi session (``engine.ts``); writable-path
  scope (the scope guard, over ``write`` and ``edit``), symbol preservation,
  and command bounds, which register only inside the ``/implement`` child
  (``scope.ts``, ``mutator.ts``, ``bounds.ts``, loaded there by explicit
  ``--extension`` flags, not through the package's own extension list).
  Command bounds also records a timed-out ``bash`` result after it runs. Each
  guard's state belongs to one extension registration. A guard is not a
  mutation policy or a Python engine operation.

guard firing
  A ``pi.appendEntry`` custom entry a guard records when it acts. The receipt
  counts these from the child's json stream as ``entry_appended`` events; no
  file the model's shell can reach is evidence. ``GUARD_KINDS``
  (``src/satyrn_engine/budget.py``) names the ten counted kinds: the guards'
  ``loop_broken``, ``scope_refused``, ``symbol_preserved``,
  ``command_bounded`` and ``command_timed_out``, and ``runner.ts``'s
  ``self_test_detected``, ``self_test_enforced``, ``self_test_red_stop``,
  ``finish_nudged`` and ``runaway_resumed``.

integration tier
  The marked test tier (``@pytest.mark.integration``) that starts real
  subprocesses and, for delivery, real local Git repositories and commands.
  It is excluded from the hermetic default run and from CI (``addopts = -m
  "not integration"``); run it explicitly with ``uv run pytest -m
  integration``.

protocol
  The one-shot JSON surface between the adapter and the engine: one
  versioned request on stdin, one versioned response on stdout, then exit.
  The response is authoritative; the process exit code mirrors it so a
  caller that cannot parse the response still has a named signal. Version
  mismatches are refused, not guessed at.

revision
  The lowercase SHA-256 hash of a file's exact bytes at the point the engine
  read it. E4 accepts a replacement only when the caller's prior revision still
  equals the current file. A successful replacement returns the next revision;
  a determinate engine refusal never advances it. A transport failure poisons
  the context because the publication result is unknown.

self_test
  The engine's registered tool that restores {term}`carried` tests, runs the
  contract's ``test_command`` and the checks, and returns failed ids with
  their first assertion line. Two gates around it live in
  ``packages/engine/runner.ts``: the completion gate runs it once, on a
  tool-call-free turn, when nothing has run it since the last landed
  mutation; the red-stop gate, when that turn is otherwise silent and the
  last completed run at the current mutation generation did not pass, runs
  it again and sends one follow-up only if that fresh run is still red.
  Each fires at most once per mutation generation, and an enforced-gate
  failure follow-up on a generation also satisfies the red-stop gate for
  that same generation. When a ``bash`` result carries a pytest summary and no
  self-test has run since the last landed mutation, the runner also runs
  ``self_test`` and records ``self_test_detected``. Each gate run records a
  {term}`guard firing`, ``self_test_enforced`` or ``self_test_red_stop``.

runaway resume
  The follow-up ``packages/engine/runner.ts`` sends when an assistant turn hits
  the per-turn output cap with no tool call, telling the model to make the next
  change with a tool call. It is sent at most twice per session, never on a
  turn where a ``self_test`` gate already sent a follow-up, and records a
  ``runaway_resumed`` {term}`guard firing`.

receipt
  The one versioned UTF-8 JSON result written by an accepted ``deliver``
  operation. Its closed ``code`` vocabulary names the exact cause; ``outcome``
  is the candidate-lifecycle category derived from that code, and the shell
  exit is a separate derived transport signal. The remaining fields record the
  base, candidate, changed paths, command status, and any retained cleanup path.

refusal
  A deliberate, named rejection of a contract or repository. Over the CLI
  it is reported as a one-line ``satyrn-engine: <CAUSE>: <detail>``
  message on stderr with a stable exit code; over the {term}`protocol` it
  travels as a JSON response on stdout whose ``code`` names the cause. A
  refusal is a verdict; a crash is not a refusal.

tripwire
  The autouse test fixture that forbids the default tier from spawning a
  process or opening a network socket, failing any test that does. Proven
  once by a planted process-spawning test, then kept to constrain every
  later phase's tests.

working tree
  The checked-out directory tree the engine operates against (the
  ``--repo`` argument). ``check`` lints that the path exists and is a
  directory. ``deliver`` requires a clean Git root and never writes to this
  caller-owned tree.

worktree isolation
  Running one delivery command in a temporary linked Git worktree detached at
  a captured base commit. Ordinary writes land outside the caller's checkout;
  the temporary worktree is removed before a candidate ref is published. Git
  registration uncertainty and process-teardown uncertainty both retain the
  path rather than deleting it unsafely. It is isolation from the caller's
  files and index, not a security sandbox.
```
