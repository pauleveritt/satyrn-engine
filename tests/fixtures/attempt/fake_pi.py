#!/usr/bin/env python3
"""Fake Pi for E5 integration: drive the shipped E4 mutator once."""

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    mode = os.environ.get("SATYRN_FAKE_PI_MODE", "replace")
    if mode == "steered":
        # I3: reproduce Node's own behaviour on its inherited stderr -- the
        # way Node puts an inherited pipe fd into O_NONBLOCK. M-E (Opus
        # review, 2026-09-19): before I4 (0c2dea8), `attempt` handed Pi its
        # own `stderr` (attempt's fd 2) directly, so this landed on the same
        # open file description as `forward` (attempt's fd 1, shared via
        # `deliver`'s stderr=STDOUT) and forced a real EAGAIN there -- the
        # exact condition C1 fixes. Since I4, Pi gets its own dedicated
        # stderr pipe (SubprocessPiRunner.run), so setting O_NONBLOCK here
        # now only affects that private pipe and never reaches `forward` --
        # the reviewer instrumented this and confirmed zero EAGAINs occur.
        # Kept anyway: it still exercises Pi's stderr being relayed through
        # `SubprocessPiRunner.run`'s drain thread end to end. See
        # test_integration_steered_session.py's module docstring for the
        # coverage that replaced the lost EAGAIN path.
        flags = fcntl.fcntl(2, fcntl.F_GETFL)
        fcntl.fcntl(2, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    print(json.dumps({"type": "agent_start", "argv": sys.argv[1:]}), flush=True)
    if ready_path := os.environ.get("SATYRN_FAKE_PI_READY"):
        Path(ready_path).write_text("ready", encoding="utf-8")
    if mode == "fail":
        print(json.dumps({"type": "session_shutdown", "reason": "fixture failure"}), flush=True)
        return 17
    if mode == "nochange":
        print(json.dumps({"type": "session_shutdown", "reason": "fixture no change"}), flush=True)
        return 0
    if mode == "delay":
        time.sleep(0.5)
        Path(os.environ["SATYRN_FAKE_PI_MARKER"]).write_text("late", encoding="utf-8")
        time.sleep(30)
        return 0
    if mode in ("implement", "implement_slow"):
        return implement(
            os.environ["SATYRN_MUTATION_CONTEXT"],
            Path(os.environ["SATYRN_ENGINE_REPO"]),
            slow=mode == "implement_slow",
        )
    if mode == "steered":
        return steered(
            os.environ["SATYRN_MUTATION_CONTEXT"],
            Path(os.environ["SATYRN_ENGINE_REPO"]),
        )

    context_text = os.environ["SATYRN_MUTATION_CONTEXT"]
    context = json.loads(context_text)
    [path] = context["revisions"]
    replacement = {
        "path": path,
        "edits": [{"oldText": "return 1", "newText": "return 2"}],
    }
    if mode == "refuse":
        replacement["edits"][0]["oldText"] = "return 3"

    engine_repo = Path(os.environ["SATYRN_ENGINE_REPO"])
    with tempfile.TemporaryDirectory(prefix="satyrn-fake-pi-") as temporary:
        root = Path(temporary)
        context_path = root / "context.json"
        input_path = root / "input.json"
        context_path.write_text(context_text, encoding="utf-8")
        input_path.write_text(json.dumps(replacement), encoding="utf-8")
        completed = subprocess.run(
            [
                "node",
                "--experimental-strip-types",
                str(engine_repo / "tools" / "exercise_mutator.mjs"),
                str(context_path),
                str(input_path),
            ],
            cwd=engine_repo,
            capture_output=True,
            text=True,
            check=False,
        )
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    print(json.dumps({"type": "session_shutdown", "reason": mode}), flush=True)
    return completed.returncode


def implement(context_text: str, engine_repo: Path, *, slow: bool = False) -> int:
    """E11 integration: drive the shipped mutator and runner once each,
    emitting the turn/usage/tool-call events a real Pi run would stream.

    ``slow`` (``SATYRN_FAKE_PI_MODE=implement_slow``, final fix wave item 1):
    emit the same events, past a declared token budget, then block in a long
    sleep before exiting -- proving live budget enforcement needs `attempt`
    to forward each line as Pi writes it. A fake that exits at once (plain
    ``implement``) cannot tell a live trip from a post-exit one; this one
    only passes if `deliver` returns well before the sleep ends.
    """
    context = json.loads(context_text)
    [path] = list(context["revisions"])
    per_turn = int(os.environ.get("SATYRN_FAKE_PI_TOKENS", "0"))

    def emit(event: dict) -> None:
        print(json.dumps(event), flush=True)

    def assistant(output: int) -> None:
        emit({"type": "message_end", "message": {"role": "assistant", "usage": {"input": 1000, "output": per_turn or output}}})

    def node(script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["node", "--experimental-strip-types", str(engine_repo / "tools" / script), *args],
            cwd=engine_repo,
            capture_output=True,
            text=True,
            check=False,
        )

    with tempfile.TemporaryDirectory(prefix="satyrn-fake-pi-") as temporary:
        root = Path(temporary)
        (root / "context.json").write_text(context_text, encoding="utf-8")
        (root / "input.json").write_text(
            json.dumps({"path": path, "edits": [{"oldText": "return 1", "newText": "return 2"}]}), encoding="utf-8"
        )
        emit({"type": "turn_start"})
        assistant(100)
        emit({"type": "tool_execution_start", "toolCallId": "e1", "toolName": "edit"})
        edit = node("exercise_mutator.mjs", str(root / "context.json"), str(root / "input.json"))
        if edit.returncode != 0 or '"ok":true' not in edit.stdout:
            sys.stderr.write(edit.stdout + edit.stderr)
            return 3
        emit({"type": "turn_start"})
        assistant(200)
        emit({"type": "tool_execution_start", "toolCallId": "t1", "toolName": "self_test"})
        test = node("exercise_runner.mjs", str(root / "context.json"))
        if out := os.environ.get("SATYRN_FAKE_PI_SELF_TEST_OUT"):
            Path(out).write_text(test.stdout, encoding="utf-8")
        emit({"type": "entry_appended", "entry": {"type": "custom", "customType": "command_bounded", "data": {"action": "set", "timeout": 120}}})
        emit({"type": "turn_start"})
        assistant(300)
        if slow:
            # Every message_end line above is already on the wire (each
            # `emit` flushes); a budget past 600 tokens has everything it
            # needs to trip right now. `attempt` must forward these lines
            # to its own stdout as they are written, not only after this
            # sleep ends, or deliver's live counter never sees them in time.
            time.sleep(float(os.environ.get("SATYRN_FAKE_PI_SLEEP_SECONDS", "20")))
    emit({"type": "session_shutdown", "reason": "implement"})
    return 0


#: Turn output tokens for the three turns `steered` emits, and their total --
#: I3's test pins the receipt's turns/tokens against these exact numbers.
STEERED_TURN_OUTPUT_TOKENS = (100, 200, 50)
STEERED_TURN_INPUT_TOKENS = 1000


def steered(context_text: str, engine_repo: Path) -> int:
    """I3: a model-free session shaped like a real steered one -- mutate,
    a large self-test tool result, `self_test_detected` and `finish_nudged`
    guard entries, the engine's steer message, then a clean no-tool-call
    stop. Exercises the exact production wiring (`forward` as a real pipe)
    that C1/I2/M5's fixes were written for.

    M-E (Opus review, 2026-09-19): this docstring, and the comment below on
    the large tool-result line, used to claim ``main``'s O_NONBLOCK call
    forced a real EAGAIN on `forward` by sharing its open file description
    with Pi's stderr. Since I4 (0c2dea8) gave Pi its own dedicated stderr
    pipe, that sharing no longer exists -- ``main``'s O_NONBLOCK call now
    only affects Pi's own private pipe, and `forward` sees zero EAGAINs
    here (the reviewer instrumented this run and confirmed it). See
    test_integration_steered_session.py's module docstring and
    ``test_a_steered_session_with_forwards_own_fd_forced_non_blocking`` for
    the coverage that exercises `forward`'s own EAGAIN path directly.
    """
    context = json.loads(context_text)
    [path] = list(context["revisions"])

    def emit(event: dict) -> None:
        print(json.dumps(event), flush=True)

    def assistant(output: int, **extra: object) -> None:
        message = {"role": "assistant", "usage": {"input": STEERED_TURN_INPUT_TOKENS, "output": output}}
        emit({"type": "message_end", "message": message, **extra})

    def node(script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["node", "--experimental-strip-types", str(engine_repo / "tools" / script), *args],
            cwd=engine_repo,
            capture_output=True,
            text=True,
            check=False,
        )

    with tempfile.TemporaryDirectory(prefix="satyrn-fake-pi-") as temporary:
        root = Path(temporary)
        (root / "context.json").write_text(context_text, encoding="utf-8")
        (root / "input.json").write_text(
            json.dumps({"path": path, "edits": [{"oldText": "return 1", "newText": "return 2"}]}), encoding="utf-8"
        )
        # Turn 1: the model mutates the source.
        emit({"type": "turn_start"})
        assistant(STEERED_TURN_OUTPUT_TOKENS[0])
        emit({"type": "tool_execution_start", "toolCallId": "e1", "toolName": "edit"})
        edit = node("exercise_mutator.mjs", str(root / "context.json"), str(root / "input.json"))
        if edit.returncode != 0 or '"ok":true' not in edit.stdout:
            sys.stderr.write(edit.stdout + edit.stderr)
            return 3

        # Turn 2: self_test -- a large tool result (several KB, like a
        # compact self-test summary), then the two guard entries a real
        # green self-test inside a turn produces (runner.ts's `maybeSteer`
        # and its `self_test_detected` note).
        emit({"type": "turn_start"})
        assistant(STEERED_TURN_OUTPUT_TOKENS[1])
        emit({"type": "tool_execution_start", "toolCallId": "t1", "toolName": "self_test"})
        test = node("exercise_runner.mjs", str(root / "context.json"))
        if out := os.environ.get("SATYRN_FAKE_PI_SELF_TEST_OUT"):
            Path(out).write_text(test.stdout, encoding="utf-8")
        # A single large event line (comfortably past any pipe's kernel
        # buffer capacity), like a compact self-test summary, emitted as one
        # `emit` -- i.e. one write+flush in attempt's pump loop. This no
        # longer forces an EAGAIN on `forward` (M-E: since I4, `main`'s own
        # O_NONBLOCK call only touches Pi's private stderr pipe), but it
        # still exercises a large single write+flush through a real pipe.
        large_summary = "PASS tests/test_value.py::test_value\n" * 8000  # ~300 KB
        emit({"type": "tool_result", "toolCallId": "t1", "content": [{"type": "text", "text": large_summary}]})
        emit({"type": "entry_appended", "entry": {"type": "custom", "customType": "self_test_detected", "data": {"generation": 1}}})
        emit({"type": "entry_appended", "entry": {"type": "custom", "customType": "finish_nudged", "data": {"generation": 1}}})
        # The engine's own steer message, delivered back as a custom-role
        # message (runner.ts's `maybeSteer`: `pi.sendMessage({customType:
        # "finish_nudged", ...}, {deliverAs: "steer"})`) -- not the model's
        # own output, so it carries no assistant usage to count.
        emit(
            {
                "type": "message_end",
                "message": {
                    "role": "custom",
                    "customType": "finish_nudged",
                    "content": [{"type": "text", "text": "The self-test passed; if the task is complete, stop now."}],
                },
            }
        )

        # Turn 3: the model makes no further tool call and stops cleanly.
        emit({"type": "turn_start"})
        assistant(STEERED_TURN_OUTPUT_TOKENS[2], stopReason="stop")
    emit({"type": "agent_end"})
    emit({"type": "session_shutdown", "reason": "steered"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
