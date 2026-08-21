# Glossary

Terms earn a place here when a phase lands the concept; the roadmap's concept
budget tracks which names the design actually needs.

```{glossary}
adapter
  The thin TypeScript layer that makes the engine reachable inside Pi as the
  ``/implement`` command and, with an explicit mutation context, a bounded
  ``edit`` tool. It starts the engine as a subprocess
  (``uv run --project $SATYRN_ENGINE_REPO satyrn-engine protocol``), sends
  one versioned JSON request, reads one JSON response, and converts every
  transport failure into a named refusal. It owns transport only; contract
  semantics stay in Python.

check
  The engine's first operation: parse and validate a contract, lint the
  repository path it names, and either accept it (exit ``0``) or refuse
  with a named cause. Exposed as the ``check`` subcommand and the
  ``check()`` library seam.

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
  ignored. See {doc}`usage` for the accepted shape.

engine
  The Python core of satyrn-engine: a library and command-line tool that
  parses and validates a contract, applies one bounded replacement, and delivers a candidate
  change without modifying the caller's working tree. Invoked from the
  shell as ``satyrn-engine``.

exit code
  The process exit status returned by ``satyrn-engine``. The values are a
  stable contract: ``0`` succeeds; ``2`` through ``7`` retain the check and
  protocol meanings; delivery uses ``8`` for every handled result without a
  candidate; mutation uses ``9`` for an accepted replacement refusal; and ``1``
  is reserved for an uncaught internal error — a crash, never a refusal. A
  delivery receipt or mutation JSON response gives the precise cause.

guard
  A small TypeScript check that observes an ordinary Pi tool call before it
  runs. E3.5 ships one guard: the loop breaker, which remembers the last twenty
  admitted call keys and refuses a sixth exact repeat while five matches remain
  in that window. Its state belongs to one extension registration. It is not a
  mutation policy or a Python engine operation.

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
