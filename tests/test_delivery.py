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
from satyrn_engine.cli import parse_args
from satyrn_engine.delivery import (
    DEFAULT_TIMEOUT,
    DeliveryCode,
    DeliveryOutcome,
    DeliveryReceipt,
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
        "worktree_path",
    ]


def test_receipt_uses_binary_shell_exit_codes() -> None:
    assert _receipt(DeliveryCode.OK).exit_code is ExitCode.OK
    assert _receipt(DeliveryCode.REPO_DIRTY).exit_code is ExitCode.NO_CANDIDATE
    assert _receipt(DeliveryCode.COMMAND_FAILED).exit_code is ExitCode.NO_CANDIDATE


def test_receipt_code_closes_outcome_and_exit_vocabulary() -> None:
    expected = {
        DeliveryCode.OK: (DeliveryOutcome.CANDIDATE_CREATED, ExitCode.OK),
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
    monkeypatch.setattr(cli, "deliver", lambda *args: _receipt(DeliveryCode.OK))
    code = cli.main(["deliver", "--repo", ".", "contract.yaml", "--", "tool"])
    assert code == 0
    assert stdout.getvalue() == _receipt(DeliveryCode.OK).render()


def test_deliver_cli_reserves_exit_one_when_receipt_stdout_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.sys, "stdout", _BrokenStdout())
    monkeypatch.setattr(cli, "deliver", lambda *args: _receipt(DeliveryCode.OK))

    assert cli.main(["deliver", "--repo", ".", "contract.yaml", "--", "tool"]) == 1


def test_deliver_cli_unwinds_on_sigterm_and_restores_the_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = signal.getsignal(signal.SIGTERM)

    def terminate(*args: object) -> DeliveryReceipt:
        del args
        os.kill(os.getpid(), signal.SIGTERM)
        raise AssertionError("SIGTERM handler did not unwind delivery")

    monkeypatch.setattr(cli, "deliver", terminate)

    assert cli.main(["deliver", "--repo", ".", "contract.yaml", "--", "tool"]) == 128 + signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) is previous
