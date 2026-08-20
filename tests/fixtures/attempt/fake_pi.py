#!/usr/bin/env python3
"""Fake Pi for E5 integration: drive the shipped E4 mutator once."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    mode = os.environ.get("SATYRN_FAKE_PI_MODE", "replace")
    print(json.dumps({"type": "agent_start", "argv": sys.argv[1:]}), flush=True)
    if mode == "fail":
        print(json.dumps({"type": "session_shutdown", "reason": "fixture failure"}), flush=True)
        return 17
    if mode == "nochange":
        print(json.dumps({"type": "session_shutdown", "reason": "fixture no change"}), flush=True)
        return 0

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


if __name__ == "__main__":
    raise SystemExit(main())

