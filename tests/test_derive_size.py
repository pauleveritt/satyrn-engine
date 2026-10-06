"""The medium-class boundary (design section 6, plan Ruling 6).

The six self-hosted tasks and depth-3 were, until this fix, read live from
`satyrn-evals`'s task tree on every test run. Two problems with that: first,
`satyrn-evals` is a separate, actively edited repository -- one more
declared symbol on `selfhost-preflight-quiet`'s `Produces:` line (it sat
exactly at the 10-symbol cap then) would turn this repository's `just gates` red
with no commit made here, which release-two's frozen tree cannot tolerate.
Second, on a machine without the evals checkout the nine parametrized rows
that called `request_for` silently skipped, so `just gates` reported green
while testing strictly less than it claimed to.

The fix pins the six self-hosted tasks (plus `agentclinic-repair-depth-3`
and the two large-tier refused tasks) as vendored fixture data under
`tests/fixtures/derive_size/<task>.json`, per the plan's own note that this
module "is also the place the six self-hosted tasks are pinned". Each
fixture carries a reconstructed request containing only the real task's
`Files:` block and its `Interfaces:` `Produces:` lines (the only parts
`size_refusal` and `produces_names` read), plus the expected non-test path
count, produced-symbol count and verdict computed from the *live* request
at vendoring time (`tools/../scratchpad/vendor.py`, run by hand against
`satyrn-evals` on 2026-09-17; see the module docstring table this file's
fix report records). `test_vendored_matches_live_manifests` below is an
optional, best-effort cross-check against the live evals tree: it SKIPS
cleanly when that tree is absent, and when present and diverging it reports
the divergence with `warnings.warn` rather than failing -- this file must
never be able to turn *this* repository's gate red because of an edit made
in `satyrn-evals`.
"""

import json
import os
import warnings
from collections.abc import Mapping
from pathlib import Path

import pytest

from satyrn_engine.attempt import build_prompt
from satyrn_engine.derive import (
    MEDIUM_MODULE_CAP,
    MEDIUM_PRODUCES_CAP,
    RepoFacts,
    _files_paths,
    _is_test_file,
    derive_contract,
    produces_names,
    size_refusal,
)

FIXTURES = Path(__file__).parent / "fixtures" / "derive_size"
LIVE_TASKS_ENV = "SATYRN_EVALS_TASKS"


def live_tasks_root(environment: Mapping[str, str] | None = None) -> Path:
    """The evals task tree the cross-check reads: the directory
    ``SATYRN_EVALS_TASKS`` names, else the sibling ``satyrn-evals`` checkout
    beside this repository. Never a machine-specific path."""
    environment = os.environ if environment is None else environment
    if named := environment.get(LIVE_TASKS_ENV):
        return Path(named)
    return Path(__file__).resolve().parents[2] / "satyrn-evals" / "src" / "satyrn_evals" / "tasks"

ADMITTED = (
    "selfhost-run-record-gate", "selfhost-docs-linter", "selfhost-guard-prefixes",
    "selfhost-review-script", "agentclinic-repair-depth-3", "selfhost-preflight-quiet",
)
REFUSED = ("selfhost-cell-loop", "selfhost-speed-probe")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def request_for(name: str) -> str:
    """The vendored request text for a self-hosted task (Finding 3): a
    reconstructed `Files:` + `Interfaces:`/`Produces:` block, not a live
    read of the `satyrn-evals` checkout."""
    return _fixture(name)["request"]


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


@pytest.mark.parametrize("name", ADMITTED + REFUSED)
def test_the_vendored_fixture_matches_its_own_recorded_counts(name):
    """The fixture is data, not just a fast path: pin that the vendored
    request text itself reproduces the counts recorded alongside it."""
    fixture = _fixture(name)
    request = fixture["request"]
    produces_count = len(produces_names(request))
    non_test_paths = tuple(
        p for p in _files_paths(request) if not _is_test_file(p) and not p.rstrip("/").endswith("tests")
    )
    assert produces_count == fixture["produces_count"]
    assert len(non_test_paths) == fixture["non_test_path_count"]
    assert (size_refusal(request) is not None) == (fixture["verdict"] == "refused")


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


def test_selfhost_preflight_quiet_sits_one_under_the_produces_cap():
    # The real task, re-vendored 2026-10-06 at 9 produced symbols. It sat at
    # the cap (10) until the live task dropped `Certificate.as_dict()`. The
    # boundary itself is pinned synthetically by the two tests below.
    request = request_for("selfhost-preflight-quiet")
    assert len(produces_names(request)) == MEDIUM_PRODUCES_CAP - 1
    assert size_refusal(request) is None


def test_ten_produced_symbols_are_admitted():
    request = ("Files:\n- Create: `a.py`, `tests/`\n\nInterfaces:\n- Produces: "
               + ", ".join(f"`sym{i}`" for i in range(1, 11)) + ".\n")
    assert len(produces_names(request)) == 10
    assert size_refusal(request) is None


def test_eleven_produced_symbols_are_refused():
    request = ("Files:\n- Create: `a.py`, `tests/`\n\nInterfaces:\n- Produces: "
               + ", ".join(f"`sym{i}`" for i in range(1, 12)) + ".\n")
    assert len(produces_names(request)) == 11
    refusal = size_refusal(request)
    assert refusal is not None
    assert "11 symbols" in refusal


def test_the_refusal_text_never_enters_the_rendered_prompt():
    """Plan Ruling 7: the refusal is the developer's message, not the

    model's. `derive_contract` binds `Contract.task` to the request
    unchanged, and `size_refusal` is computed and surfaced separately (cli.py
    stderr, the receipt) -- there is no path that folds it into the
    contract. A hard-refusal or a prompt-embedded refusal here would
    confound design section 7's runaway-resume reading, which is measured on
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


def test_the_produces_clause_refusal_never_enters_the_rendered_prompt():
    """Controller ruling: the module-clause test above pins only that
    clause's message. Design section 7's runaway-resume measurement is
    taken on `selfhost-cell-loop`, which fires the PRODUCES clause with a
    different message -- that is the load-bearing path Ruling 7 protects
    (a prompt-embedded refusal there would confound the runaway-resume
    reading), so it must be pinned directly against the vendored task
    request rather than only the synthetic module-clause one above.
    """
    request = request_for("selfhost-cell-loop")
    refusal = size_refusal(request)
    assert refusal is not None

    tracked = ("pyproject.toml", "README.md")
    facts = RepoFacts(tracked, '[project]\nname = "app"\n', "a" * 40)
    contract = derive_contract(request, facts)
    prompt = build_prompt(contract, existing=(), tracked=())

    assert refusal not in prompt
    for phrase in ("split the request", "18 of 18", "no passing state", "Interfaces: block declares"):
        assert phrase not in prompt


@pytest.mark.parametrize("name", ADMITTED + REFUSED)
def test_vendored_matches_live_manifests(name):
    """Best-effort cross-check against the live `satyrn-evals` checkout.

    Skips cleanly when that tree is absent -- it is a separate,
    independently maintained repository, not a dependency of this one.
    When the tree is present and the live request's counts or verdict
    diverge from the vendored fixture (someone edited the task on the
    other side), this test *reports* the divergence with `warnings.warn`
    and still passes: it must never be able to turn this repository's own
    gate red because of an edit made in `satyrn-evals`. Re-vendor the
    fixture (see this module's docstring) to pick up an intentional
    change.
    """
    manifest = live_tasks_root() / name / "manifest.json"
    if not manifest.is_file():
        pytest.skip(f"the evals task tree is not present: {manifest}")

    live_request = json.loads(manifest.read_text(encoding="utf-8"))["contract"]
    live_produces = len(produces_names(live_request))
    live_non_test_paths = len(tuple(
        p for p in _files_paths(live_request) if not _is_test_file(p) and not p.rstrip("/").endswith("tests")
    ))
    live_verdict = "refused" if size_refusal(live_request) is not None else "admitted"

    fixture = _fixture(name)
    divergences = []
    if live_produces != fixture["produces_count"]:
        divergences.append(f"produces_count live={live_produces} vendored={fixture['produces_count']}")
    if live_non_test_paths != fixture["non_test_path_count"]:
        divergences.append(
            f"non_test_path_count live={live_non_test_paths} vendored={fixture['non_test_path_count']}"
        )
    if live_verdict != fixture["verdict"]:
        divergences.append(f"verdict live={live_verdict} vendored={fixture['verdict']}")

    if divergences:
        warnings.warn(
            f"{name}: vendored fixture diverges from the live satyrn-evals manifest: "
            + "; ".join(divergences),
            stacklevel=1,
        )


def test_the_live_tasks_root_is_the_directory_the_environment_names(tmp_path):
    assert live_tasks_root({LIVE_TASKS_ENV: str(tmp_path)}) == tmp_path


def test_the_live_tasks_root_defaults_to_the_sibling_evals_checkout():
    root = live_tasks_root({})
    assert root.parts[-4:] == ("satyrn-evals", "src", "satyrn_evals", "tasks")
    assert root.parents[3] == Path(__file__).resolve().parents[2]


def _live_task(root: Path, name: str, contract: str) -> None:
    (root / name).mkdir()
    (root / name / "manifest.json").write_text(json.dumps({"contract": contract}), encoding="utf-8")


def test_a_live_manifest_that_matches_the_vendored_fixture_raises_no_warning(tmp_path, monkeypatch):
    name = "selfhost-preflight-quiet"
    _live_task(tmp_path, name, _fixture(name)["request"])
    monkeypatch.setenv(LIVE_TASKS_ENV, str(tmp_path))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        test_vendored_matches_live_manifests(name)


def test_a_live_manifest_that_diverges_from_the_vendored_fixture_warns(tmp_path, monkeypatch):
    name = "selfhost-preflight-quiet"
    diverged = (
        "Files:\n- Create: `scripts/preflight_quiet.py`\n- Test: `tests/`\n\n"
        "Interfaces:\n- Produces: `only_one() -> int`.\n"
    )
    _live_task(tmp_path, name, diverged)
    monkeypatch.setenv(LIVE_TASKS_ENV, str(tmp_path))
    with pytest.warns(UserWarning, match="produces_count live=1 vendored=9"):
        test_vendored_matches_live_manifests(name)


def test_the_vendored_preflight_quiet_fixture_declares_nine_symbols():
    fixture = _fixture("selfhost-preflight-quiet")
    assert fixture["produces_count"] == 9
    assert len(produces_names(fixture["request"])) == 9
    assert "Certificate.as_dict" not in produces_names(fixture["request"])
