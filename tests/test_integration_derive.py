"""End-to-end proof of `satyrn-engine derive` against a real Git repository.

Marked ``integration``: it spawns real ``git`` subprocesses, which the
default tier's tripwire (``tests/conftest.py``) forbids.
"""

import subprocess
from pathlib import Path

import pytest

from satyrn_engine.cli import main
from satyrn_engine.exits import ExitCode

pytestmark = pytest.mark.integration

TRACKED = ("pyproject.toml", "src/app/cli.py", "src/app/gate.py", "tests/test_cli.py",
           "tests/unit/test_gate.py", "tests/conftest.py", "checks/check_public.py", "docs/x.md")
PYPROJECT = '[project]\nname = "app"\n[dependency-groups]\ndev = ["pytest>=8"]\n'


def _init_repo(root: Path) -> None:
    for rel in TRACKED:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PYPROJECT if rel == "pyproject.toml" else f"# {rel}\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True)


def test_derive_writes_a_contract_under_the_git_dir_and_is_stable_across_reruns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_repo(tmp_path)

    exit_code = main(["derive", "--repo", str(tmp_path), "--", "Fix", "src/app/gate.py"])
    captured = capsys.readouterr()

    assert exit_code == ExitCode.OK
    assert "id:" in captured.out
    assert "src/app/gate.py" in captured.out
    assert "satyrn-engine: contract " in captured.err
    contract_path = Path(captured.err.split("satyrn-engine: contract ", 1)[1].strip())
    assert contract_path.is_relative_to(tmp_path / ".git" / "satyrn" / "contracts")
    assert contract_path.is_file()
    assert contract_path.read_text(encoding="utf-8") == captured.out

    exit_code_again = main(["derive", "--repo", str(tmp_path), "--", "Fix", "src/app/gate.py"])
    captured_again = capsys.readouterr()
    contract_path_again = Path(captured_again.err.split("satyrn-engine: contract ", 1)[1].strip())
    assert exit_code_again == ExitCode.OK
    assert contract_path_again == contract_path


def test_a_request_naming_nothing_falls_back_to_top_level_entries_at_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_repo(tmp_path)

    exit_code = main(["derive", "--repo", str(tmp_path), "--", "make", "it", "faster"])
    captured = capsys.readouterr()

    assert exit_code == ExitCode.OK
    writable_block = captured.out.split("writable_paths:", 1)[1].split("test_command:", 1)[0]
    for entry in ("docs/*", "pyproject.toml", "src/*", "tests/*"):
        assert entry in writable_block
    assert "checks" not in writable_block


def test_an_empty_repository_is_refused_at_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)

    exit_code = main(["derive", "--repo", str(tmp_path), "--", "make", "it", "faster"])
    captured = capsys.readouterr()

    assert exit_code == ExitCode.CONTRACT_MISSING_FIELD
    assert "DERIVE:" in captured.err
