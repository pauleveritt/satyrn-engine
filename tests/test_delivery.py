"""Default-tier tests for E3's pure CLI and receipt boundaries."""

import io
import json
import os
import signal
from dataclasses import replace
from pathlib import Path

import pytest

import satyrn_engine.cli as cli
import satyrn_engine.delivery as delivery
from satyrn_engine.attempt import AttemptCode, AttemptResult
from satyrn_engine.cli import parse_args
from satyrn_engine.delivery import (
    DEFAULT_TIMEOUT,
    DeliveryCode,
    DeliveryOutcome,
    DeliveryReceipt,
    ValidationOutcome,
)
from satyrn_engine.exits import ExitCode

FIXTURES = Path(__file__).parent / "fixtures" / "delivery"


class _BrokenStdout:
    @property
    def buffer(self) -> _BrokenStdout:
        return self

    def write(self, value: bytes) -> int:
        del value
        raise BrokenPipeError

    def flush(self) -> None:
        raise AssertionError("write fails before flush")

    def fileno(self) -> int:
        raise OSError("closed")


def _receipt(code: DeliveryCode) -> DeliveryReceipt:
    match code:
        case DeliveryCode.OK:
            return DeliveryReceipt(
                code=DeliveryCode.OK,
                message="candidate created",
                contract_id="greeting",
                repository="/src/app",
                base_commit="base-sha",
                candidate_ref="refs/satyrn/candidates/greeting/head",
                candidate_commit="candidate-sha",
                changed_paths=("greeting.py",),
                command_exit=0,
                worktree_path=None,
                validation=ValidationOutcome.NOT_REQUESTED,
            )
        case DeliveryCode.REPO_DIRTY:
            return DeliveryReceipt(
                code=DeliveryCode.REPO_DIRTY,
                message="repository has tracked or untracked changes",
                contract_id="greeting",
                repository="/src/app",
                base_commit="base-sha",
                candidate_ref=None,
                candidate_commit=None,
                changed_paths=None,
                command_exit=None,
                worktree_path=None,
            )
        case DeliveryCode.COMMAND_FAILED:
            return DeliveryReceipt(
                code=DeliveryCode.COMMAND_FAILED,
                message="command exited with status 9",
                contract_id="greeting",
                repository="/src/app",
                base_commit="base-sha",
                candidate_ref="refs/satyrn/candidates/greeting/head",
                candidate_commit=None,
                changed_paths=None,
                command_exit=9,
                worktree_path=None,
            )
        case DeliveryCode.CANDIDATE_EXISTS:
            return DeliveryReceipt(
                code=DeliveryCode.CANDIDATE_EXISTS,
                message="candidate ref already exists",
                contract_id="greeting",
                repository="/src/app",
                base_commit="base-sha",
                candidate_ref="refs/satyrn/candidates/greeting/head",
                candidate_commit="candidate-sha",
                changed_paths=("greeting.py",),
                command_exit=0,
                worktree_path=None,
            )
        case DeliveryCode.CLEANUP_FAILED:
            return DeliveryReceipt(
                code=DeliveryCode.CLEANUP_FAILED,
                message="cleanup failed after pending result OK",
                contract_id="greeting",
                repository="/src/app",
                base_commit="base-sha",
                candidate_ref="refs/satyrn/candidates/greeting/head",
                candidate_commit="candidate-sha",
                changed_paths=("greeting.py",),
                command_exit=0,
                worktree_path="/tmp/satyrn-engine-abc/worktree",
                validation=ValidationOutcome.NOT_REQUESTED,
            )
        case _:
            raise AssertionError(f"unknown fixture code: {code}")


@pytest.mark.parametrize(
    ("code", "fixture"),
    [
        (DeliveryCode.OK, "receipt-ok.json"),
        (DeliveryCode.REPO_DIRTY, "receipt-repo-dirty.json"),
        (DeliveryCode.COMMAND_FAILED, "receipt-command-failed.json"),
        (DeliveryCode.CANDIDATE_EXISTS, "receipt-candidate-exists.json"),
        (DeliveryCode.CLEANUP_FAILED, "receipt-cleanup-failed.json"),
    ],
)
def test_receipt_matches_committed_fixture(code: DeliveryCode, fixture: str) -> None:
    rendered = _receipt(code).render()
    assert rendered == (FIXTURES / fixture).read_text(encoding="utf-8")
    assert list(json.loads(rendered)) == [
        "version",
        "outcome",
        "code",
        "message",
        "contract_id",
        "repository",
        "base_commit",
        "candidate_ref",
        "candidate_commit",
        "changed_paths",
        "command_exit",
        "validation",
        "validation_exit",
        "validation_output",
        "worktree_path",
        "budget",
    ]


def test_receipt_uses_binary_shell_exit_codes() -> None:
    assert _receipt(DeliveryCode.OK).exit_code is ExitCode.OK
    assert _receipt(DeliveryCode.REPO_DIRTY).exit_code is ExitCode.NO_CANDIDATE
    assert _receipt(DeliveryCode.COMMAND_FAILED).exit_code is ExitCode.NO_CANDIDATE


def test_receipt_code_closes_outcome_and_exit_vocabulary() -> None:
    expected = {
        DeliveryCode.OK: (DeliveryOutcome.CANDIDATE_CREATED, ExitCode.OK),
        DeliveryCode.TESTS_FAILED: (DeliveryOutcome.CANDIDATE_CREATED, ExitCode.TESTS_FAILED),
        DeliveryCode.BUDGET_EXHAUSTED: (DeliveryOutcome.CANDIDATE_CREATED, ExitCode.BUDGET_EXHAUSTED),
        DeliveryCode.CONTRACT_UNREADABLE: (DeliveryOutcome.REFUSED, ExitCode.CONTRACT_UNREADABLE),
        DeliveryCode.CONTRACT_INVALID_YAML: (DeliveryOutcome.REFUSED, ExitCode.CONTRACT_INVALID_YAML),
        DeliveryCode.CONTRACT_MISSING_FIELD: (DeliveryOutcome.REFUSED, ExitCode.CONTRACT_MISSING_FIELD),
        DeliveryCode.REPO_UNAVAILABLE: (DeliveryOutcome.REFUSED, ExitCode.REPO_UNAVAILABLE),
        DeliveryCode.REPO_NOT_GIT: (DeliveryOutcome.REFUSED, ExitCode.NO_CANDIDATE),
        DeliveryCode.REPO_DIRTY: (DeliveryOutcome.REFUSED, ExitCode.NO_CANDIDATE),
        DeliveryCode.INVALID_CANDIDATE_ID: (DeliveryOutcome.REFUSED, ExitCode.NO_CANDIDATE),
        DeliveryCode.CANDIDATE_EXISTS: (DeliveryOutcome.REFUSED, ExitCode.NO_CANDIDATE),
        DeliveryCode.COMMAND_UNAVAILABLE: (DeliveryOutcome.REFUSED, ExitCode.NO_CANDIDATE),
        DeliveryCode.COMMAND_TIMEOUT: (DeliveryOutcome.DISCARDED, ExitCode.NO_CANDIDATE),
        DeliveryCode.COMMAND_FAILED: (DeliveryOutcome.DISCARDED, ExitCode.NO_CANDIDATE),
        DeliveryCode.COMMAND_CHANGED_HEAD: (DeliveryOutcome.DISCARDED, ExitCode.NO_CANDIDATE),
        DeliveryCode.NO_CHANGES: (DeliveryOutcome.DISCARDED, ExitCode.NO_CANDIDATE),
        DeliveryCode.GIT_FAILED: (DeliveryOutcome.REFUSED, ExitCode.NO_CANDIDATE),
        DeliveryCode.CLEANUP_FAILED: (DeliveryOutcome.REFUSED, ExitCode.NO_CANDIDATE),
    }

    assert {code: outcome for code, (outcome, _) in expected.items()} == delivery._CODE_TO_OUTCOME
    assert {code: exit_code for code, (_, exit_code) in expected.items()} == delivery._CODE_TO_EXIT
    assert {
        exit_code: DeliveryCode[exit_code.name]
        for exit_code in (
            ExitCode.CONTRACT_UNREADABLE,
            ExitCode.CONTRACT_INVALID_YAML,
            ExitCode.CONTRACT_MISSING_FIELD,
            ExitCode.REPO_UNAVAILABLE,
        )
    } == delivery._CHECK_REFUSAL_TO_DELIVERY_CODE
    assert _receipt(DeliveryCode.OK).outcome is DeliveryOutcome.CANDIDATE_CREATED
    assert _receipt(DeliveryCode.COMMAND_FAILED).outcome is DeliveryOutcome.DISCARDED
    assert _receipt(DeliveryCode.REPO_DIRTY).outcome is DeliveryOutcome.REFUSED
    with pytest.raises(TypeError, match="DeliveryCode"):
        replace(_receipt(DeliveryCode.OK), code="TYPO")  # type: ignore[arg-type]


def test_receipt_escapes_surrogate_paths_for_utf8_output() -> None:
    rendered = replace(_receipt(DeliveryCode.OK), repository="bad\udcff").render()
    assert "bad\\udcff" in rendered
    rendered.encode("utf-8")


def test_validation_outcome_has_exactly_six_members() -> None:
    assert {member.value for member in ValidationOutcome} == {
        "passed",
        "failed",
        "timed_out",
        "unavailable",
        "not_requested",
        "not_applicable",
    }


def test_delivery_payload_always_carries_a_validation_outcome() -> None:
    """`validation` is never an ambiguous None. A candidate-created receipt
    with no declared test records NOT_REQUESTED; a refused receipt (no
    candidate) records NOT_APPLICABLE."""
    created = _receipt(DeliveryCode.OK).payload()
    assert created["validation"] is ValidationOutcome.NOT_REQUESTED
    assert created["validation_exit"] is None
    assert created["validation_output"] is None
    refused = _receipt(DeliveryCode.REPO_DIRTY).payload()
    assert refused["validation"] is ValidationOutcome.NOT_APPLICABLE


def test_non_candidate_receipt_records_not_applicable() -> None:
    """A refused/discarded receipt has no candidate to validate, so its
    validation is NOT_APPLICABLE -- not NOT_REQUESTED, which is reserved for
    "no test_command declared"."""
    assert _receipt(DeliveryCode.REPO_DIRTY).validation is ValidationOutcome.NOT_APPLICABLE
    assert _receipt(DeliveryCode.COMMAND_FAILED).validation is ValidationOutcome.NOT_APPLICABLE
    assert _receipt(DeliveryCode.CANDIDATE_EXISTS).validation is ValidationOutcome.NOT_APPLICABLE


def test_receipt_rejects_a_non_validation_outcome() -> None:
    with pytest.raises(TypeError, match="ValidationOutcome"):
        replace(_receipt(DeliveryCode.OK), validation="passed")  # type: ignore[arg-type]


def _validation_context(
    tmp_path: Path, test_command: tuple[str, ...]
) -> delivery._DeliveryContext:
    return delivery._DeliveryContext(
        repository=str(tmp_path),
        root=tmp_path,
        environment={},
        contract_id="validation",
        base_commit="a" * 40,
        candidate_ref="refs/satyrn/candidates/validation/head",
        test_command=test_command,
    )


def _validation_pending(context: delivery._DeliveryContext) -> DeliveryReceipt:
    return delivery._context_receipt(
        context,
        DeliveryCode.OK,
        "candidate created",
        candidate_commit="c" * 40,
        changed_paths=("app.py",),
        command_exit=0,
    )


def _validation_state(tmp_path: Path) -> delivery._AttemptState:
    return delivery._AttemptState(tmp_path, tmp_path / "worktree", parent_exists=False)


def _stub_validation_run(
    monkeypatch: pytest.MonkeyPatch, result: delivery._TestRunResult
) -> None:
    monkeypatch.setattr(delivery, "_checkout_candidate", lambda *args: None)
    monkeypatch.setattr(delivery, "_run_test_command", lambda *args, **kwargs: result)


def test_validation_records_passed_and_leaves_code_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The success sibling of the FAILED test below: passing tests keep the
    coarse status OK and record PASSED on the payload."""
    context = _validation_context(tmp_path, ("pytest",))
    _stub_validation_run(
        monkeypatch, delivery._TestRunResult(returncode=0, output=b"2 passed\n")
    )

    receipt = delivery._validate_candidate(
        context, _validation_state(tmp_path), _validation_pending(context), 30.0
    )

    assert receipt.code is DeliveryCode.OK
    assert receipt.validation is ValidationOutcome.PASSED
    assert receipt.validation_exit == 0
    assert receipt.validation_output == "2 passed\n"
    assert receipt.candidate_commit == "c" * 40
    assert receipt.command_exit == 0


def test_validation_records_failed_and_retains_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _validation_context(tmp_path, ("pytest",))
    _stub_validation_run(
        monkeypatch, delivery._TestRunResult(returncode=1, output=b"1 failed\n")
    )

    receipt = delivery._validate_candidate(
        context, _validation_state(tmp_path), _validation_pending(context), 30.0
    )

    assert receipt.code is DeliveryCode.TESTS_FAILED
    assert receipt.outcome is DeliveryOutcome.CANDIDATE_CREATED
    assert receipt.exit_code is ExitCode.TESTS_FAILED
    assert receipt.validation is ValidationOutcome.FAILED
    assert receipt.validation_exit == 1
    assert receipt.validation_output == "1 failed\n"
    assert receipt.candidate_commit == "c" * 40
    assert receipt.changed_paths == ("app.py",)
    assert receipt.command_exit == 0


def test_validation_records_timed_out_and_keeps_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _validation_context(tmp_path, ("pytest",))
    _stub_validation_run(
        monkeypatch, delivery._TestRunResult(output=b"partial", timed_out=True)
    )

    receipt = delivery._validate_candidate(
        context, _validation_state(tmp_path), _validation_pending(context), 30.0
    )

    assert receipt.code is DeliveryCode.OK
    assert receipt.validation is ValidationOutcome.TIMED_OUT
    assert receipt.validation_exit is None
    assert receipt.validation_output == "partial"
    assert receipt.candidate_commit == "c" * 40


def test_validation_records_unavailable_and_keeps_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _validation_context(tmp_path, ("pytest",))
    _stub_validation_run(
        monkeypatch,
        delivery._TestRunResult(
            unavailable="cannot run test command ['pytest']: No such file"
        ),
    )

    receipt = delivery._validate_candidate(
        context, _validation_state(tmp_path), _validation_pending(context), 30.0
    )

    assert receipt.code is DeliveryCode.OK
    assert receipt.validation is ValidationOutcome.UNAVAILABLE
    assert receipt.validation_exit is None
    assert receipt.validation_output == "cannot run test command ['pytest']: No such file"
    assert receipt.candidate_commit == "c" * 40


def test_validation_records_not_requested_without_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sibling of every declared-command test: an empty test_command never
    reaches the runner, so a build that always runs the suite fails here."""
    context = _validation_context(tmp_path, ())
    ran = False

    def unexpected_run(*args: object, **kwargs: object) -> delivery._TestRunResult:
        nonlocal ran
        ran = True
        raise AssertionError("no test_command must not run a test")

    monkeypatch.setattr(delivery, "_run_test_command", unexpected_run)

    receipt = delivery._validate_candidate(
        context, _validation_state(tmp_path), _validation_pending(context), 30.0
    )

    assert receipt.code is DeliveryCode.OK
    assert receipt.validation is ValidationOutcome.NOT_REQUESTED
    assert receipt.validation_exit is None
    assert receipt.validation_output is None
    assert not ran
    assert receipt.candidate_commit == "c" * 40


def test_validation_checkout_failure_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declared command whose candidate cannot be checked out records
    UNAVAILABLE (declared but not runnable), not a false PASSED or FAILED."""
    context = _validation_context(tmp_path, ("pytest",))
    monkeypatch.setattr(
        delivery, "_checkout_candidate", lambda *args: "cannot check out candidate commit for validation: boom"
    )
    ran = False

    def unexpected_run(*args: object, **kwargs: object) -> delivery._TestRunResult:
        nonlocal ran
        ran = True
        raise AssertionError("an unavailable candidate must not reach the runner")

    monkeypatch.setattr(delivery, "_run_test_command", unexpected_run)

    receipt = delivery._validate_candidate(
        context, _validation_state(tmp_path), _validation_pending(context), 30.0
    )

    assert receipt.validation is ValidationOutcome.UNAVAILABLE
    assert receipt.validation_exit is None
    assert receipt.validation_output == "cannot check out candidate commit for validation: boom"
    assert not ran
    assert receipt.candidate_commit == "c" * 40


def test_deliver_cli_preserves_command_argv_after_literal_separator() -> None:
    args = parse_args(
        [
            "deliver",
            "--repo",
            ".",
            "--timeout",
            "2.5",
            "contract.yaml",
            "--",
            "tool",
            "--flag",
            "a b",
            "--",
            "nested",
        ]
    )
    assert args.repo == "."
    assert args.contract == "contract.yaml"
    assert args.timeout == 2.5
    assert args.attempt_command == ("tool", "--flag", "a b", "--", "nested")


def test_deliver_cli_uses_default_timeout() -> None:
    args = parse_args(["deliver", "--repo", ".", "contract.yaml", "--", "tool"])
    assert args.timeout == DEFAULT_TIMEOUT


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf", "-inf", "not-a-number"])
def test_deliver_cli_refuses_non_positive_or_non_finite_timeout(timeout: str) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["deliver", "--repo", ".", "--timeout", timeout, "contract.yaml", "--", "tool"])
    assert excinfo.value.code == int(ExitCode.USAGE)


def test_deliver_cli_defaults_base_to_none() -> None:
    """`None` must reach `deliver` unchanged -- it is what keeps today's
    HEAD-derived behaviour exactly as it was for every caller that never
    passes --base (HP3 design doc, section 3)."""
    args = parse_args(["deliver", "--repo", ".", "contract.yaml", "--", "tool"])
    assert args.base is None


def test_deliver_cli_accepts_a_base_commit_ish() -> None:
    args = parse_args(
        ["deliver", "--repo", ".", "--base", "abc1234", "contract.yaml", "--", "tool"]
    )
    assert args.base == "abc1234"


def test_deliver_cli_refuses_a_blank_base() -> None:
    """Blank is not absent (`delivery.base_is_wellformed`'s own docstring):
    mapping it to HEAD would silently base a chained phase on the caller's
    head instead of its predecessor's commit, the one failure HP3 exists to
    prevent and the one that looks like success."""
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["deliver", "--repo", ".", "--base", "  ", "contract.yaml", "--", "tool"])
    assert excinfo.value.code == int(ExitCode.USAGE)


def test_deliver_cli_passes_base_through_to_deliver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_deliver(
        repo: Path,
        contract: Path,
        command: tuple[str, ...],
        timeout: float,
        *,
        base: str | None = None,
        turn_limit: int | None = None,
        deadline_seconds: float | None = None,
    ) -> DeliveryReceipt:
        captured["base"] = base
        return _receipt(DeliveryCode.OK)

    monkeypatch.setattr(cli, "deliver", fake_deliver)
    code = cli.main(
        ["deliver", "--repo", ".", "--base", "abc1234", "contract.yaml", "--", "tool"]
    )
    assert code == 0
    assert captured["base"] == "abc1234"


def test_deliver_cli_passes_none_through_when_base_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_deliver(
        repo: Path,
        contract: Path,
        command: tuple[str, ...],
        timeout: float,
        *,
        base: str | None = None,
        turn_limit: int | None = None,
        deadline_seconds: float | None = None,
    ) -> DeliveryReceipt:
        captured["base"] = base
        return _receipt(DeliveryCode.OK)

    monkeypatch.setattr(cli, "deliver", fake_deliver)
    code = cli.main(["deliver", "--repo", ".", "contract.yaml", "--", "tool"])
    assert code == 0
    assert captured["base"] is None


def test_deliver_cli_parses_budget_flags() -> None:
    args = parse_args(
        [
            "deliver",
            "--repo",
            ".",
            "--turn-limit",
            "3",
            "--deadline-seconds",
            "2.5",
            "contract.yaml",
            "--",
            "tool",
        ]
    )
    assert args.turn_limit == 3
    assert args.deadline_seconds == 2.5


def test_deliver_cli_budget_flags_default_to_none() -> None:
    """Omitted flags reach `deliver` as None, which means "not overridden" so
    the contract's own budget (or no budget) applies -- never a default 0."""
    args = parse_args(["deliver", "--repo", ".", "contract.yaml", "--", "tool"])
    assert args.turn_limit is None
    assert args.deadline_seconds is None


@pytest.mark.parametrize("turn_limit", ["0", "-1", "1.5", "nan", "not-a-number"])
def test_deliver_cli_refuses_invalid_turn_limit(turn_limit: str) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(
            ["deliver", "--repo", ".", "--turn-limit", turn_limit, "contract.yaml", "--", "tool"]
        )
    assert excinfo.value.code == int(ExitCode.USAGE)


@pytest.mark.parametrize("deadline", ["0", "-1", "nan", "inf", "-inf", "not-a-number"])
def test_deliver_cli_refuses_invalid_deadline_seconds(deadline: str) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(
            ["deliver", "--repo", ".", "--deadline-seconds", deadline, "contract.yaml", "--", "tool"]
        )
    assert excinfo.value.code == int(ExitCode.USAGE)


def test_deliver_cli_passes_budget_flags_through_to_deliver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_deliver(
        repo: Path,
        contract: Path,
        command: tuple[str, ...],
        timeout: float,
        *,
        base: str | None = None,
        turn_limit: int | None = None,
        deadline_seconds: float | None = None,
    ) -> DeliveryReceipt:
        captured["turn_limit"] = turn_limit
        captured["deadline_seconds"] = deadline_seconds
        return _receipt(DeliveryCode.OK)

    monkeypatch.setattr(cli, "deliver", fake_deliver)
    code = cli.main(
        [
            "deliver",
            "--repo",
            ".",
            "--turn-limit",
            "4",
            "--deadline-seconds",
            "1.5",
            "contract.yaml",
            "--",
            "tool",
        ]
    )
    assert code == 0
    assert captured["turn_limit"] == 4
    assert captured["deadline_seconds"] == 1.5


def test_deliver_cli_requires_literal_separator() -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["deliver", "--repo", ".", "contract.yaml", "tool"])
    assert excinfo.value.code == int(ExitCode.USAGE)


def test_deliver_cli_requires_separator_before_the_first_command_token() -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["deliver", "--repo", ".", "contract.yaml", "tool", "--", "argument"])
    assert excinfo.value.code == int(ExitCode.USAGE)


@pytest.mark.parametrize("help_token", ["-h", "--help"])
def test_deliver_cli_does_not_treat_command_help_as_engine_help(
    help_token: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["deliver", "--repo", ".", "contract.yaml", "tool", help_token])
    assert excinfo.value.code == int(ExitCode.USAGE)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("help_token", ["-h", "--help"])
def test_deliver_cli_preserves_command_help_after_separator(help_token: str) -> None:
    args = parse_args(["deliver", "--repo", ".", "contract.yaml", "--", "tool", help_token])
    assert args.attempt_command == ("tool", help_token)


def test_deliver_cli_rejects_a_leading_separator_before_subcommand() -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["--", "deliver", "--repo", ".", "contract.yaml"])
    assert excinfo.value.code == int(ExitCode.USAGE)


def test_deliver_cli_requires_command_after_separator() -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["deliver", "--repo", ".", "contract.yaml", "--"])
    assert excinfo.value.code == int(ExitCode.USAGE)


def test_deliver_help_documents_the_command_boundary(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["deliver", "--repo", ".", "--help", "--"])
    assert excinfo.value.code == 0
    assert "CONTRACT -- COMMAND [ARG ...]" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["deliver", "--help"],
        ["deliver", "--repo", ".", "--help"],
        ["deliver", "--repo=.", "--help"],
    ],
)
def test_deliver_help_without_separator_remains_available(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(argv)
    assert excinfo.value.code == 0
    assert "CONTRACT -- COMMAND [ARG ...]" in capsys.readouterr().out


def test_deliver_cli_supports_an_embedded_text_only_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli, "deliver", lambda *args, **kwargs: _receipt(DeliveryCode.OK))
    code = cli.main(["deliver", "--repo", ".", "contract.yaml", "--", "tool"])
    assert code == 0
    assert stdout.getvalue() == _receipt(DeliveryCode.OK).render()


def test_deliver_cli_reserves_exit_one_when_receipt_stdout_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.sys, "stdout", _BrokenStdout())
    monkeypatch.setattr(cli, "deliver", lambda *args, **kwargs: _receipt(DeliveryCode.OK))

    assert cli.main(["deliver", "--repo", ".", "contract.yaml", "--", "tool"]) == 1


def test_deliver_cli_unwinds_on_sigterm_and_restores_the_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = signal.getsignal(signal.SIGTERM)

    def terminate(*args: object, **kwargs: object) -> DeliveryReceipt:
        del args, kwargs
        os.kill(os.getpid(), signal.SIGTERM)
        raise AssertionError("SIGTERM handler did not unwind delivery")

    monkeypatch.setattr(cli, "deliver", terminate)

    assert cli.main(["deliver", "--repo", ".", "contract.yaml", "--", "tool"]) == 128 + signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) is previous


@pytest.mark.parametrize(
    "interruption",
    (signal.SIGTERM,) + ((signal.SIGHUP,) if os.name == "posix" else ()),
)
def test_attempt_cli_finishes_artifact_work_after_termination_and_restores_handler(
    monkeypatch: pytest.MonkeyPatch,
    interruption: signal.Signals,
) -> None:
    previous = signal.getsignal(interruption)

    def interrupted_then_finished(*args: object, **kwargs: object) -> AttemptResult:
        del args
        del kwargs
        os.kill(os.getpid(), interruption)
        return AttemptResult(AttemptCode.OK, model="model")

    monkeypatch.setattr(cli, "attempt", interrupted_then_finished)

    assert cli.main(["attempt", "--model", "model", "contract.yaml"]) == 0
    assert signal.getsignal(interruption) is previous
