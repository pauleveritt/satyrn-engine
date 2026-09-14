"""Integration tier: `run_tests` really spawns the contract's command.

Marked ``integration`` and excluded from the default run and from CI --
this is the tier that earns the one process `run_tests` is for. The
default tier (`tests/test_runner.py`) covers everything reachable without
spawning: the pure tail function, the no-declared-command refusal, the
pure `command_matches` comparison, and the command-mismatch refusal (which
also never spawns).
"""

import sys
from pathlib import Path

import pytest

from satyrn_engine.contract import Contract
from satyrn_engine.runner import TAIL_BYTES, RunnerCode, run_tests

pytestmark = pytest.mark.integration


def _contract(*command: str) -> Contract:
    return Contract(id="e7-integration", task="run the command", test_command=tuple(command))


def _command(*parts: str) -> str:
    return " ".join(parts)


def test_a_passing_suite_returns_ok_with_exit_code_zero(tmp_path: Path) -> None:
    contract = _contract(sys.executable, "-c", "print('all good')")
    receipt = run_tests(tmp_path, contract, _command(*contract.test_command))

    assert receipt.code is RunnerCode.OK
    assert receipt.ok is True
    assert receipt.result is not None
    assert receipt.result.exit_code == 0
    assert receipt.result.timed_out is False
    assert "all good" in receipt.result.output


def test_a_failing_suite_is_a_result_not_an_error(tmp_path: Path) -> None:
    """The `CLAUDE.md`-recorded principle: a failing suite is a result."""
    contract = _contract(sys.executable, "-c", "assert 1 == 2, 'boom'")
    receipt = run_tests(tmp_path, contract, _command(*contract.test_command))

    assert receipt.code is RunnerCode.OK
    assert receipt.ok is True
    assert receipt.result is not None
    assert receipt.result.exit_code != 0
    assert receipt.result.timed_out is False
    assert "boom" in receipt.result.output


def test_a_whitespace_padded_command_still_matches_and_runs(tmp_path: Path) -> None:
    """Sibling of the exact-match run: leading/trailing whitespace is
    stripped before comparison, and a match still runs the contract's own
    argv, never the padded string."""
    contract = _contract(sys.executable, "-c", "print('all good')")
    receipt = run_tests(tmp_path, contract, f"  {_command(*contract.test_command)}  ")

    assert receipt.code is RunnerCode.OK
    assert receipt.ok is True
    assert receipt.result is not None
    assert receipt.result.exit_code == 0
    assert "all good" in receipt.result.output


def test_output_over_8192_bytes_is_truncated_to_the_tail(tmp_path: Path) -> None:
    contract = _contract(sys.executable, "-c", "print('a' * 20000)")
    receipt = run_tests(tmp_path, contract, _command(*contract.test_command))

    assert receipt.result is not None
    assert receipt.result.truncated is True
    assert len(receipt.result.output.encode("utf-8", errors="replace")) <= TAIL_BYTES + 200
    assert receipt.result.output.startswith("...[output truncated")
    assert receipt.result.output.rstrip("\n").endswith("a" * 100)


def test_a_command_that_never_returns_times_out_instead_of_raising(tmp_path: Path) -> None:
    contract = _contract(sys.executable, "-c", "import time; time.sleep(60)")
    receipt = run_tests(
        tmp_path,
        contract,
        _command(*contract.test_command),
        timeout=0.2,
    )

    assert receipt.code is RunnerCode.OK
    assert receipt.ok is True
    assert receipt.result is not None
    assert receipt.result.timed_out is True


def test_a_missing_command_is_refused_as_unavailable_not_an_exception(tmp_path: Path) -> None:
    contract = _contract("this-binary-does-not-exist-e7")
    receipt = run_tests(tmp_path, contract, _command(*contract.test_command))

    assert receipt.code is RunnerCode.TEST_COMMAND_UNAVAILABLE
    assert receipt.ok is False
    assert receipt.result is None


def test_the_command_runs_with_cwd_set_to_the_repo(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    marker.write_text("present\n", encoding="utf-8")
    contract = _contract(
        sys.executable, "-c", "import pathlib; print(pathlib.Path('marker.txt').read_text())"
    )
    receipt = run_tests(tmp_path, contract, _command(*contract.test_command))

    assert receipt.result is not None
    assert receipt.result.exit_code == 0
    assert "present" in receipt.result.output
