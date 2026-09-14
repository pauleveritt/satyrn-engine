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
    INVALID_REQUEST = 7  # the protocol surface received a malformed request
    NO_CANDIDATE = 8  # an accepted delivery operation created no candidate
    MUTATION_REFUSED = 9  # an accepted replacement was safely refused
    ATTEMPT_FAILED = 10  # an accepted model attempt failed after preparation
    TEST_COMMAND_UNAVAILABLE = 11  # a declared test command could not be run at all
    TEST_COMMAND_NOT_ALLOWED = 12  # a model-supplied bash command did not match the contract's test_command
    TESTS_FAILED = 13  # the engine's own run of the contract's tests failed
    BUDGET_EXHAUSTED = 14  # a whole-attempt turn/deadline budget was spent
