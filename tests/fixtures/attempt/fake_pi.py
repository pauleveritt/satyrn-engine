#!/usr/bin/env python3
"""Fake Pi for E5 integration: drive the shipped E4 mutator once."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    mode = os.environ.get("SATYRN_FAKE_PI_MODE", "replace")
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
    if mode == "implement":
        return implement(os.environ["SATYRN_MUTATION_CONTEXT"], Path(os.environ["SATYRN_ENGINE_REPO"]))

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


def implement(context_text: str, engine_repo: Path) -> int:
    """E11 integration: drive the shipped mutator and runner once each,
    emitting the turn/usage/tool-call events a real Pi run would stream."""
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
    emit({"type": "session_shutdown", "reason": "implement"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
