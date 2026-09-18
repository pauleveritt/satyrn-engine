"""End-to-end tests for `satyrn-engine check` through main().

The CLI surface is `satyrn-engine check --repo REPO CONTRACT`, so `main`'s
argv always leads with the ``check`` subcommand token (exactly what the
console script passes from ``sys.argv[1:]``).
"""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from satyrn_engine.cli import main, parse_args
from satyrn_engine.exits import ExitCode
from satyrn_engine.protocol import PROTOCOL_VERSION

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"
VALID = FIXTURES / "valid.yaml"
INVALID = FIXTURES / "invalid.yaml"
MISSING_FIELD = FIXTURES / "missing-field.yaml"


def test_valid_contract_accepted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--repo", str(tmp_path), str(VALID)]) == ExitCode.OK
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_invalid_yaml_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--repo", str(tmp_path), str(INVALID)]) == ExitCode.CONTRACT_INVALID_YAML
    assert "CONTRACT_INVALID_YAML" in capsys.readouterr().err


def test_impossible_repo_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--repo", str(tmp_path / "nope"), str(VALID)]) == ExitCode.REPO_UNAVAILABLE
    assert "REPO_UNAVAILABLE" in capsys.readouterr().err


def test_impossible_contract_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--repo", str(tmp_path), str(tmp_path / "no-such.yaml")]) == ExitCode.CONTRACT_UNREADABLE
    assert "CONTRACT_UNREADABLE" in capsys.readouterr().err


def test_missing_field_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--repo", str(tmp_path), str(MISSING_FIELD)]) == ExitCode.CONTRACT_MISSING_FIELD
    assert "CONTRACT_MISSING_FIELD" in capsys.readouterr().err


def test_check_paths_make_no_process_or_model_calls(tmp_path: Path) -> None:
    # The autouse tripwire in conftest.py fails this test if any path
    # spawns a process or opens a network socket; driving every path here
    # asserts the invariant end to end.
    cases = [
        (["check", "--repo", str(tmp_path), str(VALID)], ExitCode.OK),
        (["check", "--repo", str(tmp_path), str(INVALID)], ExitCode.CONTRACT_INVALID_YAML),
        (["check", "--repo", str(tmp_path / "nope"), str(VALID)], ExitCode.REPO_UNAVAILABLE),
        (["check", "--repo", str(tmp_path), str(tmp_path / "no-such.yaml")], ExitCode.CONTRACT_UNREADABLE),
        (["check", "--repo", str(tmp_path), str(MISSING_FIELD)], ExitCode.CONTRACT_MISSING_FIELD),
    ]
    for argv, expected in cases:
        assert main(argv) == expected


def test_derive_request_keeps_a_leading_dash_out_of_argparse() -> None:
    args = parse_args(["derive", "--repo", ".", "--", "-add", "a", "flag"])
    assert args.request == ["-add", "a", "flag"]


def test_derive_budget_arguments_default_to_none_and_parse_a_positive_int() -> None:
    default = parse_args(["derive", "--repo", ".", "--", "req"])
    assert (default.token_budget, default.turn_budget) == (None, None)
    explicit = parse_args(
        ["derive", "--repo", ".", "--token-budget", "48000", "--turn-budget", "72", "--", "req"]
    )
    assert (explicit.token_budget, explicit.turn_budget) == (48000, 72)


@pytest.mark.parametrize("flag", ["--token-budget", "--turn-budget"])
def test_a_non_positive_derive_budget_is_refused(flag: str) -> None:
    with pytest.raises(SystemExit):
        parse_args(["derive", "--repo", ".", flag, "0", "--", "req"])


@pytest.mark.parametrize(
    ("budget_args", "expected"),
    [
        (["--token-budget", "48000", "--turn-budget", "72"], (48000, 72)),
        ([], (32000, 48)),
    ],
)
def test_derive_writes_the_budget_into_the_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    budget_args: list[str],
    expected: tuple[int, int],
) -> None:
    """The eval adapter passes the record's limits to `derive`; the written
    contract must carry them. Without them the product default stays 32000/48
    (maintainer ruling 2026-09-18: only the eval contract changes)."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    git_dir = tmp_path / "gitdir"
    git_dir.mkdir()

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "ls-files" in argv:
            return subprocess.CompletedProcess(argv, 0, "pyproject.toml\nsrc/app.py\n", "")
        if "--git-dir" in argv:
            return subprocess.CompletedProcess(argv, 0, f"{git_dir}\n", "")
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("satyrn_engine.cli.subprocess.run", fake_run)
    assert main([
        "derive", "--repo", str(repo), *budget_args, "--", "Fix src/app.py",
    ]) == 0
    written = list((git_dir / "satyrn" / "contracts").glob("*.yaml"))
    assert len(written) == 1
    body = yaml.safe_load(written[0].read_text(encoding="utf-8"))
    assert (body["token_budget"], body["turn_budget"]) == expected


class _FakeStream:
    """A stand-in for sys.stdin/sys.stdout exposing a binary ``buffer``."""

    def __init__(self, data: bytes = b"") -> None:
        self.buffer = io.BytesIO(data)


def test_protocol_subcommand_via_main(monkeypatch: pytest.MonkeyPatch) -> None:
    request = json.dumps(
        {
            "version": PROTOCOL_VERSION,
            "operation": "check",
            "repo": str(Path(__file__).parents[1]),
            "contract": str(Path(__file__).parents[1] / "tests" / "fixtures" / "contracts" / "valid.yaml"),
        }
    )
    out = _FakeStream()
    monkeypatch.setattr(sys, "stdin", _FakeStream(request.encode("utf-8")))
    monkeypatch.setattr(sys, "stdout", out)
    assert main(["protocol"]) == ExitCode.OK
    body = json.loads(out.buffer.getvalue().decode("utf-8"))
    assert body["ok"] is True
    assert body["code"] == "OK"
