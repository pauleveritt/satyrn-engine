"""Unit tests for contract loading and validation.

Each refusal has a sibling success case (binding rule 4).
"""

from pathlib import Path

import pytest

from satyrn_engine.contract import Contract, ContractError, load_contract
from satyrn_engine.exits import ExitCode

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"


def test_load_valid_contract() -> None:
    contract = load_contract(FIXTURES / "valid.yaml")
    assert contract == Contract(id="e1-smoke", task="Replace the greeting text", writable_paths=())


def test_load_contract_with_writable_paths() -> None:
    contract = load_contract(FIXTURES / "writable.yaml")
    assert contract == Contract(
        id="e4-smoke",
        task="Replace the greeting text",
        writable_paths=("src/*.py", "tests/fixtures/app.py"),
    )


def test_load_missing_field_is_refused() -> None:
    with pytest.raises(ContractError) as excinfo:
        load_contract(FIXTURES / "missing-field.yaml")
    assert excinfo.value.code is ExitCode.CONTRACT_MISSING_FIELD
    assert "task" in excinfo.value.message


def test_load_invalid_yaml_is_refused() -> None:
    with pytest.raises(ContractError) as excinfo:
        load_contract(FIXTURES / "invalid.yaml")
    assert excinfo.value.code is ExitCode.CONTRACT_INVALID_YAML


def test_load_unreadable_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ContractError) as excinfo:
        load_contract(tmp_path / "missing.yaml")
    assert excinfo.value.code is ExitCode.CONTRACT_UNREADABLE


def test_load_non_mapping_top_level_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ContractError) as excinfo:
        load_contract(path)
    assert excinfo.value.code is ExitCode.CONTRACT_MISSING_FIELD


def test_load_contract_ignores_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "extra.yaml"
    path.write_text(
        "id: e1-extra\ntask: Replace the greeting text\nfuture: whatever\n",
        encoding="utf-8",
    )
    assert load_contract(path) == Contract(id="e1-extra", task="Replace the greeting text")


def test_load_blank_required_field_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "blank.yaml"
    path.write_text("id: ''\ntask: test\n", encoding="utf-8")
    with pytest.raises(ContractError) as excinfo:
        load_contract(path)
    assert excinfo.value.code is ExitCode.CONTRACT_MISSING_FIELD


@pytest.mark.parametrize(
    "value",
    ["src/*.py", None, ["src/*.py", ""], ["src/*.py", 1]],
)
def test_load_invalid_writable_paths_is_refused(tmp_path: Path, value: object) -> None:
    path = tmp_path / "invalid-writable.yaml"
    path.write_text(
        "id: e4-invalid\ntask: test\nwritable_paths: " + repr(value) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ContractError) as excinfo:
        load_contract(path)
    assert excinfo.value.code is ExitCode.CONTRACT_MISSING_FIELD
    assert "writable_paths" in excinfo.value.message
