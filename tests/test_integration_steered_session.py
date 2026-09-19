"""I3 (Opus review, 2026-09-19): a model-free end-to-end test for a steered
session -- the shape existing coverage never exercised.

``test_integration_implement.py::test_a_fake_model_completes_implement_end_to_end``
covers derive -> a fake ``pi`` -> deliver -> receipt, but its fake Pi
(``fake_pi.py``'s ``implement`` mode) only ever emits ``command_bounded``.
This test drives the ``steered`` mode instead: a large tool result, a
``self_test_detected`` guard entry, the engine's ``finish_nudged`` steer
message, and a clean no-tool-call stop -- through the exact production wiring
(``deliver`` spawning ``attempt`` with ``stdout=PIPE``/``stderr=STDOUT``, and
the fake Pi setting O_NONBLOCK on its own inherited stderr the way Node
does, reproducing the shared open file description C1/I2/M5 were written
for) instead of any mock.
"""

from pathlib import Path

import pytest
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
