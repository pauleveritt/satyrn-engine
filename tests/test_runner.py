"""Default-tier tests for E7's pure boundaries.

`run_tests` itself spawns a process once a command is declared, so its
actual execution belongs to the integration tier
(`tests/test_integration_runner.py`). The one branch tested here --
no declared command -- never reaches `subprocess.run`, so it belongs in
the default tier: binding rule 3 (no subprocess in the default tier) is
enforced mechanically by `tests/conftest.py`'s tripwire, and this test
demonstrates that the refusal path really does not need it.

Every refusal has a sibling success test (binding rule 4).
"""

import subprocess
from pathlib import Path

import pytest

from satyrn_engine import runner
from satyrn_engine.contract import Contract
from satyrn_engine.runner import (
    TAIL_BYTES,
    RunnerCode,
    RunnerReceipt,
    RunnerResult,
    command_matches,
    compact_output,
    run_tests,
    tail_output,
)


def test_tail_output_passes_short_output_through_unchanged() -> None:
    text, truncated = tail_output(b"ok\n")
    assert text == "ok\n"
    assert truncated is False


def test_tail_output_keeps_exactly_the_boundary_untruncated() -> None:
    data = b"a" * TAIL_BYTES
    text, truncated = tail_output(data)
    assert truncated is False
    assert text == "a" * TAIL_BYTES


def test_tail_output_truncates_and_marks_the_last_8192_bytes() -> None:
    data = b"x" * (TAIL_BYTES + 1) + b"tail"
    text, truncated = tail_output(data)
    assert truncated is True
    assert text.startswith("...[output truncated")
    assert text.endswith("x" * (TAIL_BYTES - 4) + "tail")


def test_tail_output_decodes_invalid_utf8_lossily() -> None:
    text, truncated = tail_output(b"\xff\xfevalid\n")
    assert truncated is False
    assert "valid" in text
    assert "�" in text


def test_run_tests_refuses_when_contract_declares_no_command(tmp_path: Path) -> None:
    contract = Contract(id="e7-none", task="test")
    receipt = run_tests(tmp_path, contract, "pytest")
    assert receipt.code is RunnerCode.TEST_COMMAND_UNAVAILABLE
    assert receipt.ok is False
    assert receipt.result is None
    assert "test_command" in receipt.message


def test_run_tests_refuses_a_command_that_does_not_match_the_contract(tmp_path: Path) -> None:
    """Sibling of the matching-command success in the integration tier.

    The mismatch branch returns before `subprocess.run` is ever reached, so
    -- unlike the actual run -- it belongs in the default tier.
    """
    contract = Contract(id="e7-mismatch", task="test", test_command=("pytest", "tests/"))
    receipt = run_tests(tmp_path, contract, "rm -rf /")
    assert receipt.code is RunnerCode.TEST_COMMAND_NOT_ALLOWED
    assert receipt.ok is False
    assert receipt.result is None
    assert '"pytest tests/"' in receipt.message


def test_command_matches_is_a_pure_strict_comparison() -> None:
    declared = ("pytest", "tests/")
    assert command_matches("pytest tests/", declared) is True
    assert command_matches("  pytest tests/  ", declared) is True
    assert command_matches("pytest  tests/", declared) is False
    assert command_matches("pytest", declared) is False
    assert command_matches("", declared) is False


def test_runner_receipt_requires_result_only_when_ok() -> None:
    with pytest.raises(ValueError, match="requires a result"):
        RunnerReceipt(RunnerCode.OK)
    with pytest.raises(ValueError, match="must not carry a result"):
        RunnerReceipt(
            RunnerCode.TEST_COMMAND_UNAVAILABLE,
            result=RunnerResult(exit_code=0, output="", truncated=False, timed_out=False),
        )
    with pytest.raises(TypeError, match="RunnerCode"):
        RunnerReceipt("OK")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="message must be a string"):
        RunnerReceipt(
            RunnerCode.OK,
            message=1,  # type: ignore[arg-type]
            result=RunnerResult(exit_code=0, output="", truncated=False, timed_out=False),
        )


def test_runner_receipt_ok_property_matches_the_code() -> None:
    ok = RunnerReceipt(
        RunnerCode.OK,
        result=RunnerResult(exit_code=1, output="assert 1 == 2", truncated=False, timed_out=False),
    )
    refused = RunnerReceipt(RunnerCode.TEST_COMMAND_UNAVAILABLE, "no test_command")
    assert ok.ok is True
    assert refused.ok is False


PYTEST_Q_FAIL = """..F.E                                                                    [100%]
==================================== ERRORS ====================================
_________________________ ERROR at setup of test_err __________________________
    raise RuntimeError("boom")
E   RuntimeError: boom
=================================== FAILURES ===================================
___________________________________ test_two ___________________________________
>       assert 1 == 2, "one is not two"
E       AssertionError: one is not two
E       assert 1 == 2
tests/test_a.py:5: AssertionError
=========================== short test summary info ============================
FAILED tests/test_a.py::test_two - AssertionError: one is not two
ERROR tests/test_a.py::test_err - RuntimeError: boom
1 failed, 1 passed, 1 error in 0.01s
"""


def test_compact_output_keeps_failed_and_error_ids_and_the_summary_line() -> None:
    assert compact_output(PYTEST_Q_FAIL) == (
        "FAILED tests/test_a.py::test_two - AssertionError: one is not two\n"
        "ERROR tests/test_a.py::test_err - RuntimeError: boom\n"
        "1 failed, 1 passed, 1 error in 0.01s\n")


def test_compact_output_finds_a_fenced_summary_too() -> None:
    text = "FAILED t.py::a - x\n========= 1 failed in 0.03s =========\n"
    assert compact_output(text) == "FAILED t.py::a - x\n========= 1 failed in 0.03s =========\n"


def test_compact_output_keeps_a_summary_line_with_the_past_60s_duration_suffix() -> None:
    """R2: `_SUMMARY` is the Interfaces prose regex, which includes pytest's
    optional `(H:MM:SS)` suffix past 60s (m7) -- the shorter code-block
    regex would drop this line."""
    text = "FAILED t.py::a - x\n1 passed in 75.20s (0:01:15)\n"
    assert compact_output(text) == "FAILED t.py::a - x\n1 passed in 75.20s (0:01:15)\n"


def test_compact_output_of_a_passing_run_is_its_tail() -> None:
    text = "\n".join(f"line {i}" for i in range(30)) + "\n"
    assert compact_output(text) == "\n".join(f"line {i}" for i in range(10, 30)) + "\n"


def _contract(**over: object) -> Contract:
    return Contract(id="x", task="t", test_command=("uv", "run", "python", "-m", "pytest", "-q"), **over)  # type: ignore[arg-type]


def _fake_runs(
    monkeypatch: pytest.MonkeyPatch, outputs: dict[tuple[str, ...], tuple[int, bytes]]
) -> list[tuple[list[str], dict]]:
    calls: list[tuple[list[str], dict]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append((list(argv), kwargs))
        code, out = outputs.get(tuple(argv), (0, b""))
        return subprocess.CompletedProcess(argv, code, stdout=out)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    return calls


BASE = "b" * 40
LS_TREE = b"app.py\npyproject.toml\ntests/conftest.py\ntests/test_keep.py\nchecks/check_x.py\n"


def test_run_tests_restores_the_carried_set_from_base_then_runs_suite_preserve_and_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract = _contract(preserve=("tests/test_keep.py",), checks=("checks/check_x.py",))
    suite = ("uv", "run", "python", "-m", "pytest", "-q")
    calls = _fake_runs(monkeypatch, {
        ("git", "ls-tree", "-r", "--name-only", BASE): (0, LS_TREE),
        suite: (1, PYTEST_Q_FAIL.encode()),
        (*suite, "tests/test_keep.py"): (0, b".\n1 passed in 0.01s\n"),
        (*suite, "checks/check_x.py"): (0, b".\n1 passed in 0.01s\n"),
    })
    receipt = run_tests(tmp_path, contract, None, base_commit=BASE)
    argvs = [argv for argv, _ in calls]
    assert argvs[0] == ["git", "ls-tree", "-r", "--name-only", BASE]
    assert argvs[1] == ["git", "checkout", BASE, "--", "tests/test_keep.py", "checks/check_x.py", "tests/conftest.py", "pyproject.toml"]
    assert argvs[2:] == [list(suite), [*suite, "tests/test_keep.py"], [*suite, "checks/check_x.py"]]
    assert all(kwargs["env"]["COLUMNS"] == "500" for _, kwargs in calls[2:])
    assert calls[2][1]["timeout"] <= 120.0 and calls[3][1]["timeout"] <= calls[2][1]["timeout"]   # one shared deadline
    assert receipt.ok and receipt.result is not None
    assert receipt.result.exit_code == 1
    assert receipt.result.output == (
        "FAILED tests/test_a.py::test_two - AssertionError: one is not two\n"
        "ERROR tests/test_a.py::test_err - RuntimeError: boom\n"
        "1 failed, 1 passed, 1 error in 0.01s\n"
        ".\n1 passed in 0.01s\n"
        ".\n1 passed in 0.01s\n")


def test_run_tests_skips_carried_paths_absent_at_base_and_defaults_to_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract = _contract(preserve=("tests/test_gone.py",))
    calls = _fake_runs(monkeypatch, {("git", "ls-tree", "-r", "--name-only", "HEAD"): (0, b"app.py\n")})
    receipt = run_tests(tmp_path, contract, None)
    argvs = [argv for argv, _ in calls]
    assert argvs == [["git", "ls-tree", "-r", "--name-only", "HEAD"], list(contract.test_command)]
    assert receipt.ok


def test_run_tests_still_refuses_a_string_command_that_does_not_match(tmp_path: Path) -> None:
    receipt = run_tests(tmp_path, _contract(), "rm -rf /")
    assert receipt.code is RunnerCode.TEST_COMMAND_NOT_ALLOWED and "only this exact command" in receipt.message
