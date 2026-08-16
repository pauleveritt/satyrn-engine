"""The exit-code table is a stable contract; pin the numbers."""

from satyrn_engine.exits import ExitCode


def test_exit_codes_are_distinct_and_stable() -> None:
    values = sorted(int(code) for code in ExitCode)
    assert values == [0, 2, 3, 4, 5, 6]
