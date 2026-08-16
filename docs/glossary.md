# Glossary

Terms earn a place here when a phase lands the concept; the roadmap's
concept budget tracks which names the design actually needs. A term owned
by a later phase — for example *candidate* and *receipt* (E3) — is
deliberately absent until that phase arrives.

```{glossary}
adapter
  The thin TypeScript extension that makes the engine reachable inside Pi
  as the ``/implement`` command. It starts the engine as a subprocess
  (``uv run --project $SATYRN_ENGINE_REPO satyrn-engine protocol``), sends
  one versioned JSON request, reads one JSON response, and converts every
  transport failure into a named refusal. It owns transport only; contract
  semantics stay in Python.

check
  The engine's first operation: parse and validate a contract, lint the
  repository path it names, and either accept it (exit ``0``) or refuse
  with a named cause. Exposed as the ``check`` subcommand and the
  ``check()`` library seam.

contract
  A bounded, declarative description of a change to make, written as a
  YAML file. Its top level is a mapping with two required fields, ``id``
  and ``task`` (both non-empty strings); unknown fields are ignored. See
  {doc}`usage` for the accepted shape.

engine
  The Python core of satyrn-engine: a library and command-line tool that
  parse and validate a contract and, in later phases, deliver a candidate
  change without modifying the caller's working tree. Invoked from the
  shell as ``satyrn-engine``.

exit code
  The process exit status returned by ``satyrn-engine check``. The values
  are a stable contract: ``0`` accepts, ``2`` through ``7`` are named
  refusals, and ``1`` is reserved for an uncaught internal error — a
  crash, never a refusal.

protocol
  The one-shot JSON surface between the adapter and the engine: one
  versioned request on stdin, one versioned response on stdout, then exit.
  The response is authoritative; the process exit code mirrors it so a
  caller that cannot parse the response still has a named signal. Version
  mismatches are refused, not guessed at.

refusal
  A deliberate, named rejection of a contract or repository, reported as a
  one-line ``satyrn-engine: <CAUSE>: <detail>`` message on stderr with a
  stable exit code. A refusal is a verdict; a crash is not a refusal.

tripwire
  The autouse test fixture that forbids the default tier from spawning a
  process or opening a network socket, failing any test that does. Proven
  once by a planted process-spawning test, then kept to constrain every
  later phase's tests.

working tree
  The checked-out directory tree the engine operates against (the
  ``--repo`` argument). ``check`` lints that the path exists and is a
  directory; it never writes to the tree.
```
