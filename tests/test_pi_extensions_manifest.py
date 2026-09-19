"""M9 (Opus review, 2026-09-19): the `just pi-engine` developer path (`pi`
launched with the package installed, package.json's own ``pi.extensions``)
must load the same guards a real attempt always loads via
``attempt.build_pi_command``, or a developer working in that shell sees
different behavior (no scope restriction, no bash timeout bound) than a
real attempt ever would.
"""

import json
from pathlib import Path

PACKAGE_JSON = Path(__file__).parents[1] / "packages" / "engine" / "package.json"


def test_the_dev_manifest_loads_the_same_guards_build_pi_command_always_loads() -> None:
    manifest = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    extensions = set(manifest["pi"]["extensions"])
    # build_pi_command (attempt.py) always loads exactly these four;
    # runner.ts is added only when a contract declares test_command --
    # conditional on per-run data a static manifest cannot express -- and
    # orchestrator.ts is the `/implement` adapter for this interactive shell
    # itself (its own docstring: it spawns attempt as a subprocess rather
    # than running inside the guarded hermetic child), so it belongs here
    # but never in build_pi_command's own list.
    always_loaded_guards = {"./engine.ts", "./mutator.ts", "./scope.ts", "./bounds.ts"}
    assert always_loaded_guards <= extensions
