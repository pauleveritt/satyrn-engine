import pytest

from satyrn_engine.derive import (
    DeriveError,
    RepoFacts,
    _writable_paths,
    contract_id,
    derive_contract,
    files_block,
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


def test_preserve_omits_the_repos_pytest_excluded_directories() -> None:
    tracked = (
        "pyproject.toml",
        "src/app/gate.py",
        "tests/test_gate.py",
        "tests/data/overlay-task/grader/overlay/test_hidden.py",
        "tests/integration/data/mini-session/base/test_solution.py",
    )
    pyproject = (
        '[project]\nname = "app"\n'
        "[tool.pytest.ini_options]\n"
        'norecursedirs = [".claude", "tests/data", "tests/integration/data"]\n'
    )
    contract = derive_contract("Fix src/app/gate.py", RepoFacts(tracked, pyproject, HEAD))
    assert contract.preserve == ("tests/test_gate.py",)


def test_preserve_honours_a_bare_basename_norecursedirs_pattern() -> None:
    """pytest matches a bare pattern against a directory's basename, so
    ``norecursedirs = ["data"]`` excludes ``tests/data`` even though the full
    path differs."""
    tracked = ("pyproject.toml", "src/app/gate.py", "tests/data/test_bad.py", "tests/test_gate.py")
    pyproject = (
        '[project]\nname = "app"\n'
        "[tool.pytest.ini_options]\n"
        'norecursedirs = ["data"]\n'
    )
    contract = derive_contract("Fix src/app/gate.py", RepoFacts(tracked, pyproject, HEAD))
    assert contract.preserve == ("tests/test_gate.py",)


@pytest.mark.parametrize(
    "pyproject",
    [
        '[project]\nname = "app"\n[tool]\npytest = 5\n',
        'tool = "x"\n',
        "[tool.pytest]\nini_options = 5\n",
    ],
)
def test_an_odd_pytest_table_is_ignored_not_a_crash(pyproject: str) -> None:
    contract = derive_contract("Fix src/app/gate.py", RepoFacts(TRACKED, pyproject, HEAD))
    assert contract.preserve == ("tests/test_cli.py", "tests/unit/test_gate.py")


_FALLBACK_TOP_LEVEL = ("docs/*", "src/*", "tests/*")          # pyproject.toml is carried, not writable


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


def test_a_tests_directory_of_only_support_files_still_falls_back() -> None:
    # tests/conftest.py and tests/__init__.py are test-support, not source --
    # the directory has no non-test file, so naming only tests/ still counts
    # as naming nothing but tests.
    tracked = ("app.py", "tests/conftest.py", "tests/__init__.py")
    contract = derive_contract("uv run python -m pytest tests/", RepoFacts(tracked, PYPROJECT, HEAD))
    assert contract.writable_paths == ("app.py", "tests/*")


def test_a_directory_with_a_real_module_alongside_tests_is_not_test_only() -> None:
    # tests/helpers.py is a real source module living under tests/, so the
    # directory is not test-only and naming it does not trigger the fallback.
    tracked = ("app.py", "tests/helpers.py", "tests/test_app.py")
    contract = derive_contract("Rework tests/", RepoFacts(tracked, PYPROJECT, HEAD))
    assert contract.writable_paths == ("tests/*",)


def test_the_fallback_omits_carried_top_level_files_but_keeps_uv_lock() -> None:
    # pyproject.toml and a root conftest.py are carried (runner.select_carried:
    # preserve, checks, conftest.py at any depth, INFRASTRUCTURE) and so are
    # refused by scope even when writable_paths would otherwise admit them --
    # listing them as writable would be a lie. uv.lock is not carried, so it
    # stays in the fallback.
    tracked = ("pyproject.toml", "conftest.py", "uv.lock", "src/app/gate.py", "tests/test_gate.py")
    contract = derive_contract("make it faster", RepoFacts(tracked, PYPROJECT, HEAD))
    assert contract.writable_paths == ("src/*", "tests/*", "uv.lock")
    assert "pyproject.toml" not in contract.writable_paths
    assert "conftest.py" not in contract.writable_paths


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


FILES_REQUEST = """\
Task 8: The run-record gate

Files:
- Create: `src/satyrn_evals/run_record.py`, `tests/`
- Modify: `src/satyrn_evals/cli.py` (add `launch --check RECORD`)

Interfaces:
- Produces: `RunRecord`, `gate(record)`.

Step 3: Implement. First check `src/satyrn_evals/errors.py` and do not change it.
"""


def test_files_block_is_the_block_and_stops_at_the_next_header():
    block = files_block(FILES_REQUEST)
    assert "run_record.py" in block
    assert "cli.py" in block
    assert "errors.py" not in block
    assert "RunRecord" not in block


def test_writable_paths_come_from_the_files_block_only():
    tracked = ("src/satyrn_evals/cli.py", "src/satyrn_evals/errors.py", "tests/test_cli.py")
    paths = _writable_paths(FILES_REQUEST, tracked, preserve=("tests/test_cli.py",), checks=())
    assert "src/satyrn_evals/errors.py" not in paths
    assert "src/satyrn_evals/cli.py" in paths
    assert "src/satyrn_evals/run_record.py" in paths


def test_a_request_without_a_files_block_still_reads_the_whole_request():
    tracked = ("app.py", "models.py", "tests/test_app.py")
    paths = _writable_paths("Repair the seeded bug in app.py and models.py.", tracked,
                            preserve=("tests/test_app.py",), checks=())
    assert "app.py" in paths
    assert "models.py" in paths


def test_files_block_is_none_for_a_request_with_no_files_header():
    assert files_block("Repair the seeded bug in app.py and models.py.") is None


def test_an_empty_files_block_admits_nothing_rather_than_widening_to_the_whole_request():
    # `files_block` returns "" (falsy but not None) when the header is the
    # last line of the request. `_writable_paths` must read that as "the
    # block named nothing" -- and fall back the same way an explicit empty
    # selection always has -- never as "no block, so read the whole
    # request", which would re-admit `new_thing.py` named only in the prose
    # before the (empty) `Files:` block.
    assert files_block("Files:\n") == ""
    tracked = ("src/app/cli.py", "tests/test_cli.py")
    request = "Create src/app/new_thing.py with a helper.\n\nFiles:\n"
    paths = _writable_paths(request, tracked, preserve=("tests/test_cli.py",), checks=())
    assert "src/app/new_thing.py" not in paths
    assert paths == ("src/*", "tests/*")
