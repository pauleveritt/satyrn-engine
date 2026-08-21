"""The exit-code table is a stable contract; pin the numbers."""

from satyrn_engine.exits import ExitCode


def test_exit_codes_are_distinct_and_stable() -> None:
    assert [(code.name, int(code)) for code in ExitCode] == [
        ("OK", 0),
        ("USAGE", 2),
        ("CONTRACT_UNREADABLE", 3),
        ("CONTRACT_INVALID_YAML", 4),
        ("CONTRACT_MISSING_FIELD", 5),
        ("REPO_UNAVAILABLE", 6),
        ("INVALID_REQUEST", 7),
        ("NO_CANDIDATE", 8),
        ("MUTATION_REFUSED", 9),
    ]
