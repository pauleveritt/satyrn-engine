"""I3 (Opus review, 2026-09-19): a model-free end-to-end test for a steered
session -- the shape existing coverage never exercised.

``test_integration_implement.py::test_a_fake_model_completes_implement_end_to_end``
covers derive -> a fake ``pi`` -> deliver -> receipt, but its fake Pi
(``fake_pi.py``'s ``implement`` mode) only ever emits ``command_bounded``.
This test drives the ``steered`` mode instead: a large tool result, a
``self_test_detected`` guard entry, the engine's ``finish_nudged`` steer
message, and a clean no-tool-call stop -- through the exact production
wiring (``deliver`` spawning ``attempt`` with ``stdout=PIPE``/
``stderr=STDOUT``) instead of any mock.

M-E (Opus review, 2026-09-19): this docstring, and ``fake_pi.py``'s own
``steered`` comments, used to claim the fake Pi's O_NONBLOCK call on its own
inherited fd 2 landed on the *same* open file description as ``forward`` and
forced a real EAGAIN there, exercising C1/I2/M5's retry paths end to end.
That was true before I4 (0c2dea8): Pi now gets its own dedicated stderr
pipe, so the O_NONBLOCK it sets on fd 2 is contained entirely inside that
private pipe and never reaches ``forward``'s own descriptor -- the reviewer
instrumented this run and confirmed zero EAGAINs occur here. This test still
covers real value (the steered session's shape through deliver's real spawn
and attempt's real pump/guard/receipt wiring), just not that one path
anymore. See ``test_a_steered_session_with_forwards_own_fd_forced_non_blocking``
below for the coverage that replaces it: it forces O_NONBLOCK directly on
attempt's own stdout fd (``forward``, not Pi's), the one descriptor
``deliver`` actually reads live.
"""

from pathlib import Path

import pytest
from test_integration_attempt import ROOT
from test_integration_implement import _attempt_command, _implement_fixture

from satyrn_engine.delivery import deliver

pytestmark = pytest.mark.integration

# Mirrors fake_pi.py's STEERED_TURN_OUTPUT_TOKENS = (100, 200, 50) and
# STEERED_TURN_INPUT_TOKENS = 1000 (tests/ has no __init__.py, so the fixture
# script cannot be imported as a package -- see N4's note in
# test_integration_implement.py -- the same way test_a_fake_model_completes_
# implement_end_to_end hardcodes its own fake's token totals rather than
# importing them).
_STEERED_TURN_OUTPUT_TOKENS = (100, 200, 50)
_STEERED_TURN_INPUT_TOKENS = 1000


def test_a_steered_session_completes_with_the_guard_entries_and_exact_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, contract_path, _environment = _implement_fixture(tmp_path, capsys, monkeypatch)
    monkeypatch.setenv("SATYRN_FAKE_PI_MODE", "steered")

    receipt = deliver(repo, contract_path, _attempt_command(contract_path), timeout=120.0)
    payload = receipt.payload()

    assert (payload["code"], payload["outcome"]) == ("OK", "candidate-created"), payload
    assert payload["candidate_commit"] is not None
    assert payload["changed_paths"] == ["app.py"]

    expected_turns = len(_STEERED_TURN_OUTPUT_TOKENS)
    expected_tokens_in = _STEERED_TURN_INPUT_TOKENS * expected_turns
    expected_tokens_out = sum(_STEERED_TURN_OUTPUT_TOKENS)
    assert (payload["turns"], payload["tool_calls"]) == (expected_turns, 2)
    assert (payload["tokens_in"], payload["tokens_out"]) == (expected_tokens_in, expected_tokens_out)

    assert payload["guard_firings"]["self_test_detected"] == 1
    assert payload["guard_firings"]["finish_nudged"] == 1

    budget = payload["budget"]
    assert budget["live_counter"] == "live"
    assert budget["tokens_used"] == expected_tokens_out


def _attempt_command_with_forwards_fd_forced_nonblocking(contract_path: Path) -> tuple[str, ...]:
    """The least invasive seam found for forcing a real EAGAIN on
    `forward` (attempt's own stdout, the fd `deliver` actually reads live):
    a tiny wrapper, run in place of the `satyrn-engine` console script, that
    flips O_NONBLOCK on fd 1 before handing off to the real CLI entry point.
    No production code changes and no flag a real run could ever trip --
    this only exists in the command a test builds for its own subprocess.
    """
    wrapper = (
        "import fcntl, os, sys\n"
        "flags = fcntl.fcntl(1, fcntl.F_GETFL)\n"
        "fcntl.fcntl(1, fcntl.F_SETFL, flags | os.O_NONBLOCK)\n"
        "from satyrn_engine.cli import main\n"
        "sys.exit(main(sys.argv[1:]))\n"
    )
    return (
        "uv", "run", "--project", str(ROOT), "python", "-c", wrapper,
        "attempt", "--model=fixture/model", "--", str(contract_path),
    )


# M-E (Opus review, 2026-09-19): the coverage I3's test lost when I4 gave Pi
# its own private stderr pipe -- a real EAGAIN on `forward` itself, through
# deliver's real spawn shape, not a mock. Forcing O_NONBLOCK directly on
# attempt's own fd 1 before its pump ever starts reproduces exactly the
# condition `_write_retrying_backpressure`/`_flush_retrying_backpressure`
# exist for (C1's docstring): a write to `forward` that raises
# BlockingIOError or returns short even though `deliver`, the reader, is
# alive and draining normally. If `_flush_retrying_backpressure` is ever
# reverted to a bare `flush()` (dropping the EAGAIN retry), this goes red:
# the flush raises inside the pump thread, `forward` is marked a dead sink,
# and the receipt's live budget counter can no longer be trusted.
def test_a_steered_session_with_forwards_own_fd_forced_non_blocking(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, contract_path, _environment = _implement_fixture(tmp_path, capsys, monkeypatch)
    monkeypatch.setenv("SATYRN_FAKE_PI_MODE", "steered")

    receipt = deliver(
        repo, contract_path, _attempt_command_with_forwards_fd_forced_nonblocking(contract_path), timeout=120.0
    )
    payload = receipt.payload()

    assert (payload["code"], payload["outcome"]) == ("OK", "candidate-created"), payload

    expected_turns = len(_STEERED_TURN_OUTPUT_TOKENS)
    expected_tokens_in = _STEERED_TURN_INPUT_TOKENS * expected_turns
    expected_tokens_out = sum(_STEERED_TURN_OUTPUT_TOKENS)
    assert (payload["turns"], payload["tool_calls"]) == (expected_turns, 2)
    assert (payload["tokens_in"], payload["tokens_out"]) == (expected_tokens_in, expected_tokens_out)

    budget = payload["budget"]
    assert budget["live_counter"] == "live"
    assert budget["tokens_used"] == expected_tokens_out
