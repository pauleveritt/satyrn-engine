"""HP3 slice 1: `deliver(..., base=...)`.

Default tier: no model, no network, no subprocess. The argv the resolver is
asked to run is what is asserted here; whether Git accepts it is the
integration tier's question.
"""

import inspect

from satyrn_engine.delivery import _base_argv, base_is_wellformed, deliver


def test_deliver_takes_an_optional_base_defaulting_to_none() -> None:
    parameter = inspect.signature(deliver).parameters["base"]
    assert parameter.default is None
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_no_base_resolves_head_exactly_as_before() -> None:
    """`None` must change nothing. Every recorded receipt and every existing
    caller depends on that, and the untouched delivery suite is the rest of
    the evidence."""
    assert _base_argv(None) == ("rev-parse", "--verify", "HEAD^{commit}")


def test_a_base_resolves_that_commit_ish() -> None:
    assert _base_argv("abc123") == ("rev-parse", "--verify", "abc123^{commit}")


def test_a_base_is_peeled_to_a_commit_like_head_is() -> None:
    """A tag or a ref must reach the same object kind `HEAD^{commit}` does;
    branching from a tag object rather than its commit would fail later, far
    from the cause."""
    assert _base_argv("refs/satyrn/candidates/phase-1/head")[-1].endswith("^{commit}")


def test_a_blank_base_is_not_well_formed() -> None:
    """`""` is not `None`. Treating it as absent would silently base a chained
    phase on `HEAD` -- the one failure this cycle exists to prevent, and the
    one that looks like success.

    This module refuses through receipts rather than exceptions, so the
    predicate is what the default tier can test; the receipt it produces is
    an integration check.
    """
    assert not base_is_wellformed("")
    assert not base_is_wellformed("   ")


def test_a_real_base_is_well_formed() -> None:
    """The sibling. Without it the predicate could return False always and
    every refusal test above would still pass."""
    assert base_is_wellformed("HEAD")
    assert base_is_wellformed("refs/satyrn/candidates/phase-1/head")
