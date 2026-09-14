"""Pin the prompt's stated shell-command bound to bounds.ts's own constant."""

import re
from pathlib import Path

from satyrn_engine.attempt import BASH_BOUND_SECONDS

BOUNDS_TS = Path(__file__).parents[1] / "packages" / "engine" / "bounds.ts"


def test_the_prompt_names_the_same_bound_bounds_ts_applies() -> None:
    match = re.search(r"^export const DEFAULT_TIMEOUT_SECONDS = (\d+);$", BOUNDS_TS.read_text(), re.MULTILINE)
    assert match is not None and int(match.group(1)) == BASH_BOUND_SECONDS == 120
