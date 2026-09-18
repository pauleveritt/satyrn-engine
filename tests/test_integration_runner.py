"""Integration tier: `run_tests` really spawns the contract's command.

Marked ``integration`` and excluded from the default run and from CI --
this is the tier that earns the one process `run_tests` is for. The
default tier (`tests/test_runner.py`) covers everything reachable without
spawning: the pure tail function, the no-declared-command refusal, the
pure `command_matches` comparison, and the command-mismatch refusal (which
also never spawns).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from satyrn_engine.contract import Contract
from satyrn_engine.derive import RepoFacts, derive_contract
from satyrn_engine.runner import TAIL_BYTES, RunnerCode, run_tests

pytestmark = pytest.mark.integration


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout


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


def test_a_tampered_preserve_path_is_restored_from_base_before_the_extra_run(tmp_path: Path) -> None:
    """The carried set (steering C3/N2): a `preserve` path is restored from
    the accepted base into the worktree before every self-test, so a
    model's edit to it in between never counts. The test command's second
    invocation (with the preserve path appended, per R3) reads that path's
    own content, so what it prints proves which content actually ran."""
    repo = tmp_path / "repo"
    repo.mkdir()
    preserve = repo / "preserve.txt"
    preserve.write_text("original\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git_output(repo, "rev-parse", "HEAD").strip()
    preserve.write_text("tampered\n", encoding="utf-8")  # uncommitted edit in the worktree

    code = (
        "import sys, pathlib\n"
        "args = sys.argv[1:]\n"
        "print(pathlib.Path(args[0]).read_text() if args else 'NO_PRESERVE_ARG')\n"
    )
    contract = Contract(
        id="e7-preserve",
        task="restore",
        test_command=(sys.executable, "-c", code),
        preserve=("preserve.txt",),
    )

    receipt = run_tests(repo, contract, None, base_commit=base)

    assert receipt.ok and receipt.result is not None
    assert "NO_PRESERVE_ARG" in receipt.result.output
    assert "original" in receipt.result.output
    assert "tampered" not in receipt.result.output
    assert preserve.read_text(encoding="utf-8") == "original\n"


def test_a_clean_tree_whose_only_red_tests_are_pytest_excluded_self_tests_green(tmp_path: Path) -> None:
    """The 2026-09-18 route-proof defect, end to end: a repo whose own suite
    is green but whose fixture tree cannot import. `run_tests` runs the
    contract command once more with `preserve` appended; before the fix the
    derived `preserve` named `tests/data/...`, pytest collected it anyway
    (an explicit path defeats `norecursedirs`), and the self-test exited 2."""
    repo = tmp_path / "repo"
    (repo / "tests" / "data" / "fixture").mkdir(parents=True)
    (repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (repo / "tests" / "data" / "fixture" / "test_hidden.py").write_text("import not_a_module\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'x'\nversion = '0'\n"
        "[tool.pytest.ini_options]\n"
        'norecursedirs = ["tests/data"]\n'
        "[tool.satyrn]\n"
        f'self_test = ["{sys.executable}", "-m", "pytest", "-q"]\n',
        encoding="utf-8",
    )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    tracked = tuple(_git_output(repo, "ls-files").split())
    facts = RepoFacts(tracked, (repo / "pyproject.toml").read_text(encoding="utf-8"), "e" * 40)
    contract = derive_contract("Fix tests/test_ok.py", facts)

    assert "tests/data/fixture/test_hidden.py" not in contract.preserve
    receipt = run_tests(repo, contract, None)

    assert receipt.code is RunnerCode.OK
    assert receipt.result is not None
    assert receipt.result.exit_code == 0


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
