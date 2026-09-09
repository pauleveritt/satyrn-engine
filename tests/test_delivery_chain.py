"""HP3 slice 2: `deliver_chain`, offline.

The `deliver` parameter is the test seam and therefore the extension seam
(`CLAUDE.md`): the default tier passes a scripted stand-in and no second
injection mechanism exists.

Every base is asserted on the **recorded calls**, not inferred from the
result. A chain that reached the right final ref for the wrong reason would
satisfy a result-only assertion.
"""

from pathlib import Path

import pytest

from satyrn_engine.delivery import (
    DeliveryCode,
    DeliveryReceipt,
    deliver_chain,
)

REPO = Path("/repo")
PHASES = (
    (Path("phase-1.yaml"), ("true",)),
    (Path("phase-2.yaml"), ("true",)),
    (Path("phase-3.yaml"), ("true",)),
)


def _receipt(code: DeliveryCode, ref: str | None, commit: str | None) -> DeliveryReceipt:
    return DeliveryReceipt(
        repository=str(REPO),
        code=code,
        message="",
        contract_id="c",
        base_commit=commit,
        candidate_ref=ref,
        candidate_commit=commit,
        changed_paths=None,
        command_exit=None,
        worktree_path=None,
    )


class _Scripted:
    """A stand-in `deliver` that records every base it was handed."""

    def __init__(self, codes: list[DeliveryCode]) -> None:
        self.codes = codes
        self.bases: list[str | None] = []
        self.calls = 0

    def __call__(self, repo, contract_path, command, timeout=None, *, base=None):
        self.bases.append(base)
        code = self.codes[self.calls]
        self.calls += 1
        ok = code is DeliveryCode.OK
        return _receipt(
            code,
            f"refs/satyrn/candidates/p{self.calls}/head" if ok else None,
            f"commit{self.calls}" if ok else None,
        )


def _run(codes: list[DeliveryCode]) -> tuple:
    scripted = _Scripted(codes)
    chain = deliver_chain(REPO, PHASES, timeout=1.0, deliver=scripted)
    return chain, scripted


def test_three_accepted_phases_produce_one_final_candidate() -> None:
    chain, _ = _run([DeliveryCode.OK] * 3)
    assert chain.code is DeliveryCode.OK
    assert chain.candidate_ref == "refs/satyrn/candidates/p3/head"
    assert len(chain.phases) == 3


def test_each_phase_after_the_first_bases_on_its_predecessors_commit() -> None:
    """The property, asserted where it happens. This is the test that fails
    if the base parameter is threaded but ignored."""
    _, scripted = _run([DeliveryCode.OK] * 3)
    assert scripted.bases == [None, "commit1", "commit2"]


def test_the_accepted_intermediate_refs_are_retained() -> None:
    """This repository grades from retained evidence; discarding the
    intermediates would make a mid-chain regression unreproducible without
    re-running."""
    chain, _ = _run([DeliveryCode.OK] * 3)
    assert chain.accepted_refs == (
        "refs/satyrn/candidates/p1/head",
        "refs/satyrn/candidates/p2/head",
        "refs/satyrn/candidates/p3/head",
    )


def test_a_refusal_at_phase_two_stops_the_chain() -> None:
    chain, scripted = _run([DeliveryCode.OK, DeliveryCode.COMMAND_FAILED, DeliveryCode.OK])
    assert scripted.calls == 2, "phase 3 must not run after phase 2 refused"
    assert len(chain.phases) == 2
    assert chain.candidate_ref is None
    assert chain.accepted_refs == ("refs/satyrn/candidates/p1/head",)


def test_a_refusal_at_phase_one_retains_nothing() -> None:
    chain, scripted = _run([DeliveryCode.NO_CHANGES, DeliveryCode.OK, DeliveryCode.OK])
    assert scripted.calls == 1
    assert chain.candidate_ref is None
    assert chain.accepted_refs == ()


def test_the_reported_code_is_the_stopping_phases_own() -> None:
    """No `CHAIN_FAILED`: a new code would hide which phase failed behind a
    label, and every way a chain can fail is a way a phase can fail."""
    chain, _ = _run([DeliveryCode.OK, DeliveryCode.COMMAND_TIMEOUT, DeliveryCode.OK])
    assert chain.code is DeliveryCode.COMMAND_TIMEOUT


def test_an_empty_chain_is_refused_rather_than_reported_successful() -> None:
    """Zero phases delivering nothing is not a success; it is a caller
    mistake, and reporting OK over an empty sequence is a verdict computed
    over no cells."""
    with pytest.raises(ValueError, match="at least one phase"):
        deliver_chain(REPO, (), timeout=1.0, deliver=_Scripted([]))


def test_a_successful_chain_differs_observably_from_a_stopped_one() -> None:
    """The done-when for this slice: the two outcomes differ in ref count as
    well as in code, so neither can be mistaken for the other."""
    good, _ = _run([DeliveryCode.OK] * 3)
    stopped, _ = _run([DeliveryCode.OK, DeliveryCode.COMMAND_FAILED, DeliveryCode.OK])
    assert (good.code, len(good.accepted_refs), good.candidate_ref is None) != (
        stopped.code, len(stopped.accepted_refs), stopped.candidate_ref is None
    )


# --- review finding 3: a malformed success must not restart from HEAD -------


class _MalformedOK(_Scripted):
    """OK receipts that carry no candidate. Reproduced by review through this
    same seam: two phases with bases `[None, None]`, ending OK with nothing."""

    def __call__(self, repo, contract_path, command, timeout=None, *, base=None):
        self.bases.append(base)
        self.calls += 1
        return _receipt(DeliveryCode.OK, None, None)


def test_an_ok_receipt_without_a_candidate_is_refused() -> None:
    """Tolerating it is worse than failing: `base` falls to None and the next
    phase restarts from HEAD, so the chain reports OK while carrying none of
    its predecessors' work."""
    with pytest.raises(ValueError, match="without a candidate"):
        deliver_chain(REPO, PHASES, timeout=1.0, deliver=_MalformedOK([]))


class _RefWithoutCommit(_Scripted):
    def __call__(self, repo, contract_path, command, timeout=None, *, base=None):
        self.bases.append(base)
        self.calls += 1
        return _receipt(DeliveryCode.OK, "refs/satyrn/candidates/p/head", None)


def test_an_ok_receipt_with_a_ref_but_no_commit_is_refused() -> None:
    """The half that would otherwise slip through: a ref is enough to be
    recorded as accepted, while the missing commit is what resets the base."""
    with pytest.raises(ValueError, match="without a candidate"):
        deliver_chain(REPO, PHASES, timeout=1.0, deliver=_RefWithoutCommit([]))


def test_a_consistent_success_still_advances() -> None:
    """The sibling: the check above refuses malformed successes, not all of
    them."""
    chain, scripted = _run([DeliveryCode.OK] * 3)
    assert chain.code is DeliveryCode.OK
    assert scripted.bases == [None, "commit1", "commit2"]
