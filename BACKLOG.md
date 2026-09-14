# Backlog

> Tangents live here, not in the current phase. Every entry carries **the
> condition that reopens it**; an entry with no reopen condition is a wish,
> not a backlog item. Capped and checked by `just lint-docs`.

Moved out of `ROADMAP.md` on 2026-09-09, verbatim, when HP3 was scheduled and
the planning surface needed the caps regime `satyrn-evals` adopted on
2026-09-02. Derived from that repository's **current** shape, not from the
stale `research/facts-field-backlog` branch, which predates its 2026-09-07
reset.

Deferred, each with the condition that reopens it — see `BRIEF.md`:
a persistent sidecar (reopens on measured startup cost); a subinterpreter
pool (reopens on a concurrent caller); guards in Python (reopens only if
the everyday path acquires a Python prerequisite for some other reason —
the latency argument against it is wrong and should not be re-derived);
contract authoring (stays a main-agent skill); a multi-method protocol
(add a second method only when a vertical slice needs it); the **Windows
`/implement` run** (reopens on access to a Windows machine — E2's
done-when named POSIX and Windows, and only POSIX has been recorded; the
integration tier does not run in CI, so this is a manual recorded run,
not a CI job).

Added 2026-09-06, from the V13 evidence above — both are what "a workflow
like AgentClinic" needs, and neither is started: **file creation through
the mutator** under the contract's `writable_paths` (reopens whenever a
task requires a new file; `attempt`'s prompt currently forbids creation
and every eval task that needs it is excluded), and a **bounded,
model-invocable test runner** (reopens with file creation — given only
`read,edit`, the model invented a tool named `uv_run` and called
`python -m pytest tests/` through it twice before Pi refused it). Ship the
refusal-keyed breaker before or with the runner: a runner is a new way to
stall.
