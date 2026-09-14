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


def test_load_contract_without_test_command_has_empty_default() -> None:
    contract = load_contract(FIXTURES / "valid.yaml")
    assert contract.test_command == ()


def test_load_contract_with_test_command(tmp_path: Path) -> None:
    path = tmp_path / "with-command.yaml"
    path.write_text(
        "id: e7-smoke\ntask: test\ntest_command:\n  - python\n  - -m\n  - pytest\n",
        encoding="utf-8",
    )
    contract = load_contract(path)
    assert contract.test_command == ("python", "-m", "pytest")


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
    rendered = "null" if value is None else repr(value)
    path.write_text(
        "id: e4-invalid\ntask: test\nwritable_paths: " + rendered + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ContractError) as excinfo:
        load_contract(path)
    assert excinfo.value.code is ExitCode.CONTRACT_MISSING_FIELD
    assert "writable_paths" in excinfo.value.message


@pytest.mark.parametrize(
    "value",
    ["pytest", None, [], ["pytest", ""], ["pytest", 1]],
)
def test_load_invalid_test_command_is_refused(tmp_path: Path, value: object) -> None:
    path = tmp_path / "invalid-test-command.yaml"
    rendered = "null" if value is None else repr(value)
    path.write_text(
        "id: e7-invalid\ntask: test\ntest_command: " + rendered + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ContractError) as excinfo:
        load_contract(path)
    assert excinfo.value.code is ExitCode.CONTRACT_MISSING_FIELD
    assert "test_command" in excinfo.value.message


def test_load_contract_with_budget_fields(tmp_path: Path) -> None:
    path = tmp_path / "budget.yaml"
    path.write_text(
        "id: v5-budget\ntask: test\nturn_budget: 3\ndeadline_seconds: 4.5\n",
        encoding="utf-8",
    )
    contract = load_contract(path)
    assert contract.turn_budget == 3
    assert contract.deadline_seconds == 4.5


def test_load_contract_budget_defaults_to_absent_not_zero(tmp_path: Path) -> None:
    """The sibling: absent is absent (None), never a hidden zero default that
    would turn an undeclared budget into an immediate exhaustion."""
    path = tmp_path / "no-budget.yaml"
    path.write_text("id: v5-no-budget\ntask: test\n", encoding="utf-8")
    contract = load_contract(path)
    assert contract.turn_budget is None
    assert contract.deadline_seconds is None


def test_load_contract_deadline_seconds_as_integer_is_a_float(tmp_path: Path) -> None:
    path = tmp_path / "int-deadline.yaml"
    path.write_text("id: v5-int\ntask: test\ndeadline_seconds: 4\n", encoding="utf-8")
    contract = load_contract(path)
    assert contract.deadline_seconds == 4.0
    assert isinstance(contract.deadline_seconds, float)


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "3", None])
def test_load_invalid_turn_budget_is_refused(tmp_path: Path, value: object) -> None:
    path = tmp_path / "invalid-turn-budget.yaml"
    rendered = "null" if value is None else repr(value)
    path.write_text(
        "id: v5-invalid\ntask: test\nturn_budget: " + rendered + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ContractError) as excinfo:
        load_contract(path)
    assert excinfo.value.code is ExitCode.CONTRACT_MISSING_FIELD
    assert "turn_budget" in excinfo.value.message


@pytest.mark.parametrize("value", [0, -1, "nan", "inf", True, None])
def test_load_invalid_deadline_seconds_is_refused(tmp_path: Path, value: object) -> None:
    path = tmp_path / "invalid-deadline.yaml"
    if value is None:
        rendered = "null"
    elif isinstance(value, bool):
        rendered = "true"
    else:
        rendered = repr(value)
    path.write_text(
        "id: v5-invalid\ntask: test\ndeadline_seconds: " + rendered + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ContractError) as excinfo:
        load_contract(path)
    assert excinfo.value.code is ExitCode.CONTRACT_MISSING_FIELD
    assert "deadline_seconds" in excinfo.value.message
