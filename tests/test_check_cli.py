"""End-to-end tests for `satyrn-engine check` through main().

The CLI surface is `satyrn-engine check --repo REPO CONTRACT`, so `main`'s
argv always leads with the ``check`` subcommand token (exactly what the
console script passes from ``sys.argv[1:]``).
"""

from pathlib import Path

from satyrn_engine.cli import main
from satyrn_engine.exits import ExitCode

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"
VALID = FIXTURES / "valid.yaml"
INVALID = FIXTURES / "invalid.yaml"
MISSING_FIELD = FIXTURES / "missing-field.yaml"


def test_valid_contract_accepted(tmp_path: Path, capsys) -> None:
    assert main(["check", "--repo", str(tmp_path), str(VALID)]) == ExitCode.OK
    assert capsys.readouterr().err == ""


def test_invalid_yaml_refused(tmp_path: Path, capsys) -> None:
    assert main(["check", "--repo", str(tmp_path), str(INVALID)]) == ExitCode.CONTRACT_INVALID_YAML
    assert "CONTRACT_INVALID_YAML" in capsys.readouterr().err


def test_impossible_repo_refused(tmp_path: Path, capsys) -> None:
    assert main(["check", "--repo", str(tmp_path / "nope"), str(VALID)]) == ExitCode.REPO_UNAVAILABLE
    assert "REPO_UNAVAILABLE" in capsys.readouterr().err


def test_impossible_contract_refused(tmp_path: Path, capsys) -> None:
    assert main(["check", "--repo", str(tmp_path), str(tmp_path / "no-such.yaml")]) == ExitCode.CONTRACT_UNREADABLE
    assert "CONTRACT_UNREADABLE" in capsys.readouterr().err


def test_missing_field_refused(tmp_path: Path, capsys) -> None:
    assert main(["check", "--repo", str(tmp_path), str(MISSING_FIELD)]) == ExitCode.CONTRACT_MISSING_FIELD
    assert "CONTRACT_MISSING_FIELD" in capsys.readouterr().err


def test_check_paths_make_no_process_or_model_calls(tmp_path: Path) -> None:
    # The autouse tripwire in conftest.py fails this test if any path
    # spawns a process or opens a network socket; driving every path here
    # asserts the invariant end to end.
    cases = [
        (["check", "--repo", str(tmp_path), str(VALID)], ExitCode.OK),
        (["check", "--repo", str(tmp_path), str(INVALID)], ExitCode.CONTRACT_INVALID_YAML),
        (["check", "--repo", str(tmp_path / "nope"), str(VALID)], ExitCode.REPO_UNAVAILABLE),
        (["check", "--repo", str(tmp_path), str(tmp_path / "no-such.yaml")], ExitCode.CONTRACT_UNREADABLE),
        (["check", "--repo", str(tmp_path), str(MISSING_FIELD)], ExitCode.CONTRACT_MISSING_FIELD),
    ]
    for argv, expected in cases:
        assert main(argv) == expected
