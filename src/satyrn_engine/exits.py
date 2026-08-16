"""Exit codes for the engine's CLI and library.

The numeric values are a stable contract: callers (and the Pi adapter in
later phases) rely on them to distinguish refusals. Do not renumber.
"""

from enum import IntEnum


class ExitCode(IntEnum):
    """Stable process exit codes.

    ``OK`` and every refusal value are deliberate, named verdicts. Exit
    code 1 is deliberately absent: Python reports an uncaught exception as
    exit 1, so reserving it keeps a crash distinguishable from a refusal.
    """

    OK = 0
    USAGE = 2  # argparse's own exit for a malformed command line
    CONTRACT_UNREADABLE = 3
    CONTRACT_INVALID_YAML = 4
    CONTRACT_MISSING_FIELD = 5
    REPO_UNAVAILABLE = 6
