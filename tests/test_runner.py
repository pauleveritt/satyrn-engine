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

from pathlib import Path

import pytest

from satyrn_engine.contract import Contract
from satyrn_engine.runner import (
    TAIL_BYTES,
    RunnerCode,
    RunnerReceipt,
    RunnerResult,
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
    receipt = run_tests(tmp_path, contract)
    assert receipt.code is RunnerCode.TEST_COMMAND_UNAVAILABLE
    assert receipt.ok is False
    assert receipt.result is None
    assert "test_command" in receipt.message


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
