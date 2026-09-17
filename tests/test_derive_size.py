"""The medium-class boundary (design §6, plan Ruling 6).

The six self-hosted tasks and depth-3 are read from the evals task tree, so
the predicate is measured against the real requests rather than a paraphrase.
If that tree is absent the rows skip with the reason; the pure rows below
still run.
"""

import json
from pathlib import Path

import pytest

from satyrn_engine.attempt import build_prompt
from satyrn_engine.derive import (
    MEDIUM_MODULE_CAP,
    MEDIUM_PRODUCES_CAP,
    RepoFacts,
    derive_contract,
    produces_names,
    size_refusal,
)

TASKS = Path("/Users/pauleveritt/projects/pauleveritt/satyrn-evals/src/satyrn_evals/tasks")
ADMITTED = (
    "selfhost-run-record-gate", "selfhost-docs-linter", "selfhost-guard-prefixes",
    "selfhost-review-script", "agentclinic-repair-depth-3",
)
REFUSED = ("selfhost-cell-loop", "selfhost-speed-probe")


def request_for(name: str) -> str:
    manifest = TASKS / name / "manifest.json"
    if not manifest.is_file():
        pytest.skip(f"the evals task tree is not present: {manifest}")
    return json.loads(manifest.read_text(encoding="utf-8"))["contract"]


@pytest.mark.parametrize("name", ADMITTED)
def test_every_medium_and_floor_task_is_admitted(name):
    assert size_refusal(request_for(name)) is None


@pytest.mark.parametrize("name", REFUSED)
def test_every_large_tier_task_is_refused(name):
    refusal = size_refusal(request_for(name))
    assert refusal is not None
    assert "split" in refusal


@pytest.mark.parametrize("name", ADMITTED + REFUSED)
def test_the_produces_count_is_on_the_expected_side_of_the_cap(name):
    count = len(produces_names(request_for(name)))
    assert (count > MEDIUM_PRODUCES_CAP) == (name in REFUSED), f"{name}: {count}"


def test_three_modules_in_files_are_refused_whatever_the_interfaces_say():
    request = "Files:\n- Create: `a.py`, `b.py`, `c.py`, `tests/`\n\nInterfaces:\n- Produces: `f`.\n"
    assert "3 non-test paths" in (size_refusal(request) or "")


def test_two_modules_in_files_are_admitted():
    request = "Files:\n- Create: `a.py`, `tests/`\n- Modify: `b.py`\n\nInterfaces:\n- Produces: `f`.\n"
    assert size_refusal(request) is None


def test_a_request_with_no_files_block_is_admitted():
    assert size_refusal("Repair the seeded bug in app.py.") is None


def test_a_request_with_an_empty_files_block_is_admitted():
    # files_block returns "" (truthy-check hazard) for a present-but-empty
    # Files: block, distinct from None for no block at all (Task 3's review).
    assert size_refusal("Files:\n\nInterfaces:\n- Produces: `f`.\n") is None


def test_produces_names_skips_paths_and_prose():
    request = ("Interfaces:\n- Produces: `RunRecord`, `load_run_record(path: Path) -> RunRecord`, "
               "`src/x/run_record.py`, `errors.py`, `satyrn-evals launch --check R.json`.\n")
    assert produces_names(request) == ("RunRecord", "load_run_record")


def test_produces_names_strips_a_leading_class_or_type_keyword():
    request = "Interfaces:\n- Produces: `class Status(StrEnum)`, `type Spawn = Callable`.\n"
    assert produces_names(request) == ("Status", "Spawn")


def test_the_caps_are_the_plans_numbers():
    assert (MEDIUM_MODULE_CAP, MEDIUM_PRODUCES_CAP) == (2, 10)


def test_the_refusal_text_never_enters_the_rendered_prompt():
    """Plan Ruling 7: the refusal is the developer's message, not the

    model's. `derive_contract` binds `Contract.task` to the request
    unchanged, and `size_refusal` is computed and surfaced separately (cli.py
    stderr, the receipt) -- there is no path that folds it into the
    contract. A hard-refusal or a prompt-embedded refusal here would
    confound design §7's runaway-resume reading, which is measured on
    `selfhost-cell-loop`, a request this predicate refuses.
    """
    request = "Files:\n- Create: `a.py`, `b.py`, `c.py`, `tests/`\n\nInterfaces:\n- Produces: `f`.\n"
    refusal = size_refusal(request)
    assert refusal is not None

    tracked = ("pyproject.toml", "a.py", "b.py", "c.py", "tests/test_a.py")
    facts = RepoFacts(tracked, '[project]\nname = "app"\n', "a" * 40)
    contract = derive_contract(request, facts)
    prompt = build_prompt(contract, existing=tracked, tracked=tracked)

    assert refusal not in prompt
    assert "split the request" not in prompt
    assert "medium class" not in prompt
