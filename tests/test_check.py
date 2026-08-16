"""Library-level tests for check()."""

from pathlib import Path

from satyrn_engine.check import check
from satyrn_engine.exits import ExitCode

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"


def test_check_accepts_valid_contract(tmp_path: Path) -> None:
    result = check(tmp_path, FIXTURES / "valid.yaml")
    assert result.code is ExitCode.OK
    assert result.contract is not None
    assert result.contract.id == "e1-smoke"


def test_check_refuses_unavailable_repo(tmp_path: Path) -> None:
    result = check(tmp_path / "nope", FIXTURES / "valid.yaml")
    assert result.code is ExitCode.REPO_UNAVAILABLE
    assert "nope" in result.message
