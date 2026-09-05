# E5 timeout preservation correction

**Date:** 2026-09-05
**Status:** accepted repair to E5; implementation and deterministic evidence
follow in the same correction branch.

## Correction

The E5 description correctly says that Pi stdout is written to a regular spool
and exported after Pi exits, but it did not account for an outer timeout
delivering `SIGTERM` to the Engine command group. Default signal disposition
ended the Engine attempt process before its normal spool-to-artifact
finalization could run. The partial transcript then disappeared when the
caller removed its temporary workspace.

For `attempt`, temporary `SIGTERM` and `SIGHUP` handlers forward the signal to
Pi's separate process group, then keep the Engine process alive through its
child wait and artifact publication. Pi terminates; the Engine reaps it and
atomically exports the bytes already present in the spool before returning its
normal failure. `SIGHUP` is POSIX-only; `SIGTERM` is the portable path.

## Loop-breaker correction

The E3.5 loop breaker previously blocked each sixth exact repeated call but
left print-mode Pi free to retry the same blocked call indefinitely. Its
registration-local state now ends the turn on the third consecutive blocked
call, resetting after any admitted call. Every blocked call still emits the
existing `loop_broken` entry; only the third response adds Pi's documented
`terminate: true` field.

## Evidence required

Default Node replay proves the two non-terminal blocks, third terminal block,
and reset after an admitted call. Marked E5 integration proves an Engine
attempt sent a group `SIGTERM` preserves its partial JSONL transcript, reaps
the direct Pi child, and leaves no late-write descendant.
