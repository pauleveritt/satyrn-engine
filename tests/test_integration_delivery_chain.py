"""HP3 slice 3: the chain against real Git.

The first test here is the one the whole cycle exists for, and the only one
that fails if `base` is threaded but ignored: every other check passes
against a chain that quietly bases every phase on `HEAD`. It was run once
against a build with `base` forced to `None` and observed to fail before
being trusted.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from satyrn_engine.delivery import DeliveryCode, deliver_chain

pytestmark = pytest.mark.integration


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(("git", *args), cwd=repo, capture_output=True)


def make_repo(path: Path) -> Path:
    path.mkdir()
    assert git(path, "init", "--quiet", "--initial-branch=master").returncode == 0
    assert git(path, "config", "user.name", "HP3 Test").returncode == 0
    assert git(path, "config", "user.email", "hp3@example.invalid").returncode == 0
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    assert git(path, "add", "-A").returncode == 0
    assert git(path, "commit", "--quiet", "-m", "base").returncode == 0
    return path


def _phases(tmp_path: Path, count: int = 3) -> tuple:
    phases = []
    for index in range(1, count + 1):
        contract = tmp_path / f"phase-{index}.yaml"
        contract.write_text(
            f"id: 'phase-{index}'\ntask: 'add phase {index}'\n", encoding="utf-8"
        )
        command = (
            sys.executable,
            "-c",
            f"open('phase{index}.txt','w').write('{index}')",
        )
        phases.append((contract, command))
    return tuple(phases)


def _tree(repo: Path, commit: str) -> set[str]:
    out = subprocess.run(
        ("git", "ls-tree", "-r", "--name-only", commit),
        cwd=repo, capture_output=True, check=True,
    )
    return set(out.stdout.decode().split())


def test_the_final_phase_contains_every_earlier_phases_files(
    tmp_path: Path,
) -> None:
    """The property. Code folds forward through the checkout.

    Against a chain that bases every phase on HEAD, phase 3's tree holds
    `phase3.txt` and neither of the others, and this assertion fails.
    """
    repo = make_repo(tmp_path / "repo")
    chain = deliver_chain(repo, _phases(tmp_path), timeout=60.0)

    assert chain.code is DeliveryCode.OK, chain.phases[-1].message
    assert chain.candidate_ref is not None
    tree = _tree(repo, chain.phases[-1].candidate_commit or "")
    assert {"phase1.txt", "phase2.txt", "phase3.txt"} <= tree
    assert "base.txt" in tree


def test_each_phase_records_its_predecessors_commit_as_its_base(
    tmp_path: Path,
) -> None:
    """The same property read from the receipts, which is what a later
    investigation would have to rely on."""
    repo = make_repo(tmp_path / "repo")
    chain = deliver_chain(repo, _phases(tmp_path), timeout=60.0)
    bases = [receipt.base_commit for receipt in chain.phases]
    commits = [receipt.candidate_commit for receipt in chain.phases]
    assert bases[1] == commits[0]
    assert bases[2] == commits[1]


def test_every_accepted_intermediate_ref_still_resolves(tmp_path: Path) -> None:
    """Retained evidence: a mid-chain regression must be reproducible without
    re-running the chain."""
    repo = make_repo(tmp_path / "repo")
    chain = deliver_chain(repo, _phases(tmp_path), timeout=60.0)
    assert len(chain.accepted_refs) == 3
    for ref in chain.accepted_refs:
        assert git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").returncode == 0


def test_re_running_the_same_chain_refuses_and_keeps_the_first_run(
    tmp_path: Path,
) -> None:
    """`CANDIDATE_EXISTS` is correct rather than inconvenient: overwriting a
    candidate would destroy the evidence the first run produced."""
    repo = make_repo(tmp_path / "repo")
    phases = _phases(tmp_path)
    first = deliver_chain(repo, phases, timeout=60.0)
    assert first.code is DeliveryCode.OK
    before = _tree(repo, first.phases[-1].candidate_commit or "")

    second = deliver_chain(repo, phases, timeout=60.0)
    assert second.code is DeliveryCode.CANDIDATE_EXISTS
    assert second.candidate_ref is None
    assert _tree(repo, first.phases[-1].candidate_commit or "") == before
    # Reading the tree is not enough: an unreachable object survives a deleted
    # ref, so a tree check passes over evidence nothing can find any more.
    # Resolve every ref the first run published.
    for ref in first.accepted_refs:
        assert git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").returncode == 0


def test_an_unresolvable_base_refuses_rather_than_using_head(
    tmp_path: Path,
) -> None:
    """A fall back to HEAD would make a broken chain look like a working one."""
    from satyrn_engine.delivery import deliver

    repo = make_repo(tmp_path / "repo")
    contract, command = _phases(tmp_path, 1)[0]
    receipt = deliver(repo, contract, command, 60.0, base="no-such-commit")
    assert receipt.code is DeliveryCode.REPO_NOT_GIT
    assert "no-such-commit" in receipt.message


def test_a_real_base_is_accepted(tmp_path: Path) -> None:
    """The sibling: the refusal above is not refusing every base."""
    from satyrn_engine.delivery import deliver

    repo = make_repo(tmp_path / "repo")
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=repo, capture_output=True, check=True
    ).stdout.decode().strip()
    contract, command = _phases(tmp_path, 1)[0]
    receipt = deliver(repo, contract, command, 60.0, base=head)
    assert receipt.code is DeliveryCode.OK
    assert receipt.base_commit == head


def test_a_mid_chain_refusal_stops_the_chain_and_keeps_what_was_accepted(
    tmp_path: Path,
) -> None:
    """The real rejection witness the plan called for and the suite lacked.

    Phase 2's command exits non-zero, so delivery refuses it. Phase 3 must not
    run, no final ref may exist, phase 1's ref must still resolve -- that is
    what makes a mid-chain regression reproducible without re-running -- and
    the caller's own checkout must be untouched throughout.
    """
    repo = make_repo(tmp_path / "repo")
    phases = list(_phases(tmp_path))
    failing = tmp_path / "phase-2.yaml"
    phases[1] = (failing, (sys.executable, "-c", "raise SystemExit(3)"))

    chain = deliver_chain(repo, tuple(phases), timeout=60.0)

    assert chain.code is not DeliveryCode.OK
    assert len(chain.phases) == 2, "phase 3 must not run after phase 2 refused"
    assert chain.candidate_ref is None
    assert len(chain.accepted_refs) == 1
    assert git(repo, "rev-parse", "--verify",
               f"{chain.accepted_refs[0]}^{{commit}}").returncode == 0
    status = subprocess.run(
        ("git", "status", "--porcelain"), cwd=repo, capture_output=True, check=True
    )
    assert status.stdout == b"", "the caller checkout must be untouched"


def test_a_chain_of_one_phase_still_publishes_its_candidate(tmp_path: Path) -> None:
    """The sibling for the refusal above: the stopping logic refuses failures,
    not chains in general."""
    repo = make_repo(tmp_path / "repo")
    chain = deliver_chain(repo, _phases(tmp_path, 1), timeout=60.0)
    assert chain.code is DeliveryCode.OK
    assert chain.candidate_ref is not None
    assert len(chain.accepted_refs) == 1
