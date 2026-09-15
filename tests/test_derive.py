import pytest

from satyrn_engine.derive import (
    DeriveError,
    RepoFacts,
    contract_id,
    derive_contract,
    render_contract,
    self_test_command,
)

TRACKED = ("pyproject.toml", "src/app/cli.py", "src/app/gate.py", "tests/test_cli.py",
           "tests/unit/test_gate.py", "tests/conftest.py", "checks/check_public.py", "docs/x.md")
PYPROJECT = '[project]\nname = "app"\n[dependency-groups]\ndev = ["pytest>=8"]\n'
HEAD = "a" * 40
FACTS = RepoFacts(TRACKED, PYPROJECT, HEAD)


def test_writable_paths_come_from_named_files_and_new_files_never_from_preserved_tests() -> None:
    contract = derive_contract("Add --check to src/app/cli.py", FACTS)
    assert contract.writable_paths == ("src/app/cli.py",)          # tests/test_cli.py exists and is preserved
    assert contract.task == "Add --check to src/app/cli.py"


def test_a_new_module_under_a_tracked_directory_is_writable_with_its_new_test() -> None:
    contract = derive_contract("Create src/app/run_record.py with a gate", FACTS)
    assert contract.writable_paths == ("src/app/run_record.py", "tests/test_run_record.py")


def test_a_named_directory_becomes_a_pattern_and_mentions_deduplicate() -> None:
    contract = derive_contract("Rework tests/ and tests again, and src/app/gate.py", FACTS)
    assert contract.writable_paths == ("tests/*", "src/app/gate.py")


def test_self_test_command_defaults_to_python_m_pytest_and_honours_a_declaration() -> None:
    assert self_test_command(PYPROJECT) == ("uv", "run", "python", "-m", "pytest", "-q")
    declared = PYPROJECT + '[tool.satyrn]\nself_test = ["uv", "run", "pytest", "-q", "-p", "no:cacheprovider"]\n'
    assert self_test_command(declared) == ("uv", "run", "pytest", "-q", "-p", "no:cacheprovider")
    with pytest.raises(DeriveError, match="self_test"):
        self_test_command(PYPROJECT + "[tool.satyrn]\nself_test = 'pytest'\n")


def test_carried_sets_and_budgets_are_derived_from_the_repo() -> None:
    contract = derive_contract("Fix src/app/gate.py", FACTS)
    assert contract.preserve == ("tests/test_cli.py", "tests/unit/test_gate.py")
    assert contract.checks == ("checks/check_public.py",)
    assert (contract.token_budget, contract.turn_budget) == (32000, 48)


_FALLBACK_TOP_LEVEL = ("docs/*", "pyproject.toml", "src/*", "tests/*")


def test_a_request_naming_nothing_falls_back_to_top_level_entries() -> None:
    contract = derive_contract("make it faster", FACTS)
    assert contract.writable_paths == _FALLBACK_TOP_LEVEL


def test_a_request_naming_only_a_preserved_test_falls_back_to_top_level_entries() -> None:
    contract = derive_contract("Fix tests/test_cli.py", FACTS)
    assert contract.writable_paths == _FALLBACK_TOP_LEVEL


def test_a_request_naming_only_the_tests_directory_falls_back_to_top_level_entries() -> None:
    tracked = ("app.py", "models.py", "templates/index.html", "tests/test_app.py")
    facts = RepoFacts(tracked, PYPROJECT, HEAD)
    contract = derive_contract("uv run python -m pytest tests/", facts)
    assert contract.writable_paths == ("app.py", "models.py", "templates/*", "tests/*")


def test_a_request_naming_a_source_file_is_unaffected_by_the_fallback() -> None:
    contract = derive_contract("Fix src/app/gate.py", FACTS)
    assert contract.writable_paths == ("src/app/gate.py",)          # tests/unit/test_gate.py is preserved


def test_the_fallback_never_makes_checks_writable() -> None:
    contract = derive_contract("make it faster", FACTS)
    assert not any(p == "checks" or p.startswith("checks/") for p in contract.writable_paths)


def test_the_fallback_still_lists_preserve_and_checks_as_carried_not_writable() -> None:
    # The fallback's `tests/*` pattern would fnmatch-admit a preserved file
    # (packages/engine/scope.ts:admits), but scope.ts checks the request's
    # `carried` set -- built from `preserve` plus `checks` (runner.py
    # select_carried) -- before it ever checks `admits(writable_paths, ...)`.
    # A preserved file stays refused because it is carried, regardless of
    # which directory pattern in `writable_paths` would otherwise admit it.
    contract = derive_contract("make it faster", FACTS)
    assert contract.preserve == ("tests/test_cli.py", "tests/unit/test_gate.py")
    assert contract.checks == ("checks/check_public.py",)
    assert "tests/*" in contract.writable_paths
    assert not any(p == "checks" or p.startswith("checks/") for p in contract.writable_paths)


def test_an_empty_repository_still_raises() -> None:
    with pytest.raises(DeriveError, match="name at least one tracked file"):
        derive_contract("make it faster", RepoFacts((), PYPROJECT, HEAD))


def test_a_new_test_file_named_alone_also_falls_back() -> None:
    # Naming only a new test file leaves no source path either, same as
    # naming the tests/ directory or naming nothing.
    contract = derive_contract("Create tests/test_new.py", FACTS)
    assert contract.writable_paths == _FALLBACK_TOP_LEVEL


def test_a_flat_layout_derives_the_sibling_test_and_preserves_the_existing_one() -> None:
    tracked = ("app.py", "tests/test_value.py", "pyproject.toml")
    contract = derive_contract("Make value() return 2 in app.py", RepoFacts(tracked, PYPROJECT, HEAD))
    assert contract.writable_paths == ("app.py", "tests/test_app.py")
    assert contract.preserve == ("tests/test_value.py",)


def test_a_repo_without_a_pyproject_is_refused() -> None:
    with pytest.raises(DeriveError, match="pyproject"):
        derive_contract("Fix src/app/gate.py", RepoFacts(TRACKED, "", HEAD))


def test_the_id_is_stable_for_request_and_head_and_moves_with_either() -> None:
    assert contract_id("x", HEAD) == contract_id("x", HEAD)
    assert contract_id("x", HEAD) != contract_id("y", HEAD) != contract_id("y", "b" * 40)
    assert contract_id("x", HEAD).startswith("implement-") and len(contract_id("x", HEAD)) == 22


def test_rendered_yaml_omits_absent_values_and_loads_back_to_the_same_contract(tmp_path) -> None:
    from satyrn_engine.contract import load_contract
    contract = derive_contract("Fix src/app/gate.py", FACTS)
    text = render_contract(contract)
    assert "deadline_seconds" not in text and "null" not in text
    path = tmp_path / "c.yaml"
    path.write_text(text, encoding="utf-8")
    assert load_contract(path) == contract
