"""Integration evidence for E3 delivery through the real console script."""

import errno
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
import yaml

import satyrn_engine.delivery as delivery

ROOT = Path(__file__).parents[1]

pytestmark = pytest.mark.integration


def delivery_argv(
    repo: Path,
    contract: Path,
    command: Sequence[str],
    timeout: float,
    *,
    base: str | None = None,
) -> tuple[str, ...]:
    return (
        os.fspath(Path(sys.executable).with_name("satyrn-engine")),
        "deliver",
        "--repo",
        str(repo),
        "--timeout",
        str(timeout),
        *(("--base", base) if base is not None else ()),
        str(contract),
        "--",
        *command,
    )


def git(repo: Path, *args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("git", *args),
        cwd=repo,
        input=input_bytes,
        capture_output=True,
        check=False,
    )


def make_repo(path: Path) -> Path:
    path.mkdir()
    assert git(path, "init", "--quiet", "--initial-branch=master").returncode == 0
    assert git(path, "config", "user.name", "E3 Test").returncode == 0
    assert git(path, "config", "user.email", "e3@example.invalid").returncode == 0
    (path / "kept.txt").write_text("before\n", encoding="utf-8")
    (path / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    assert git(path, "add", "-A").returncode == 0
    assert git(path, "commit", "--quiet", "-m", "base").returncode == 0
    return path


def write_contract(path: Path, candidate_id: str = "greeting") -> Path:
    path.write_text(f"id: {candidate_id!r}\ntask: 'make a bounded change'\n", encoding="utf-8")
    return path


def write_test_contract(
    path: Path, candidate_id: str, test_command: Sequence[str]
) -> Path:
    """Write a contract whose declared test command the engine must run."""
    path.write_text(
        yaml.safe_dump(
            {
                "id": candidate_id,
                "task": "make a bounded change",
                "test_command": list(test_command),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def write_budget_contract(
    path: Path,
    candidate_id: str,
    *,
    turn_budget: int | None = None,
    deadline_seconds: float | None = None,
    test_command: Sequence[str] | None = None,
) -> Path:
    """Write a contract declaring a whole-attempt budget and optional tests."""
    data: dict[str, object] = {
        "id": candidate_id,
        "task": "make a bounded change",
    }
    if turn_budget is not None:
        data["turn_budget"] = turn_budget
    if deadline_seconds is not None:
        data["deadline_seconds"] = deadline_seconds
    if test_command is not None:
        data["test_command"] = list(test_command)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def run_delivery(
    repo: Path,
    contract: Path,
    command: Sequence[str] = (sys.executable, "-c", "pass"),
    *,
    timeout: float = 30.0,
    environment: dict[str, str] | None = None,
    base: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    proc = subprocess.run(
        delivery_argv(repo, contract, command, timeout, base=base),
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = proc.stdout.splitlines()
    assert len(lines) == 1, proc
    return proc, json.loads(lines[0])


def source_snapshot(repo: Path) -> tuple[bytes, bytes, bytes]:
    head = git(repo, "rev-parse", "HEAD").stdout
    status = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    worktrees = git(repo, "worktree", "list", "--porcelain", "-z").stdout
    return head, status, worktrees


def assert_source_unchanged(repo: Path, before: tuple[bytes, bytes, bytes]) -> None:
    assert source_snapshot(repo) == before


def test_clean_root_reaches_no_changes_without_touching_source(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml")
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract)

    assert proc.returncode == 8
    assert proc.stderr == ""
    assert receipt == {
        "version": 1,
        "outcome": "discarded",
        "code": "NO_CHANGES",
        "message": "command produced no changes",
        "contract_id": "greeting",
        "repository": os.path.abspath(repo),
        "base_commit": before[0].strip().decode(),
        "candidate_ref": "refs/satyrn/candidates/greeting/head",
        "candidate_commit": None,
        "changed_paths": [],
        "command_exit": 0,
        "validation": "not_applicable",
        "validation_exit": None,
        "validation_output": None,
        "worktree_path": None,
        "budget": {
            "state": "not_declared",
            "turns_used": 0,
            "seconds_used": 0.0,
            "turn_limit": None,
            "deadline_seconds": None,
            "token_limit": None,
            "tokens_used": 0,
        },
        "turns": 0,
        "tool_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "guard_firings": {
            "loop_broken": 0,
            "scope_refused": 0,
            "symbol_preserved": 0,
            "command_bounded": 0,
            "command_timed_out": 0,
            "self_test_redirected": 0,
        },
        "carried": {
            "preserve": [],
            "checks": [],
            "infrastructure": [],
            "absent": [],
            "tampered": [],
        },
    }
    assert_source_unchanged(repo, before)


def test_base_composes_two_real_cli_deliveries_into_one_fold_forward(
    tmp_path: Path,
) -> None:
    """The fold-forward proof for `--base`: two real `satyrn-engine deliver`
    subprocess calls, `--base` threaded by hand the way an external caller
    (never importing this package) has to. Against a build that parses
    `--base` but ignores it, phase 2's tree holds only `phase2.txt` and this
    assertion fails."""
    repo = make_repo(tmp_path / "repo")
    contract_one = tmp_path / "phase-1.yaml"
    contract_one.write_text("id: 'phase-1'\ntask: 'add phase 1'\n", encoding="utf-8")
    contract_two = tmp_path / "phase-2.yaml"
    contract_two.write_text("id: 'phase-2'\ntask: 'add phase 2'\n", encoding="utf-8")

    _, receipt_one = run_delivery(
        repo,
        contract_one,
        (sys.executable, "-c", "open('phase1.txt', 'w').write('1')"),
    )
    assert receipt_one["code"] == "OK", receipt_one
    base = receipt_one["candidate_commit"]
    assert isinstance(base, str) and base

    _, receipt_two = run_delivery(
        repo,
        contract_two,
        (sys.executable, "-c", "open('phase2.txt', 'w').write('2')"),
        base=base,
    )
    assert receipt_two["code"] == "OK", receipt_two

    tree = subprocess.run(
        ("git", "ls-tree", "-r", "--name-only", receipt_two["candidate_commit"]),
        cwd=repo, capture_output=True, check=True, text=True,
    ).stdout.split()
    assert {"phase1.txt", "phase2.txt"} <= set(tree)


@pytest.mark.parametrize(
    ("contract_text", "repository_exists", "expected_code", "expected_exit"),
    [
        (None, True, "CONTRACT_UNREADABLE", 3),
        ("id: [\n", True, "CONTRACT_INVALID_YAML", 4),
        ("id: missing-task\n", True, "CONTRACT_MISSING_FIELD", 5),
        ("id: known\ntask: test\n", False, "REPO_UNAVAILABLE", 6),
    ],
)
def test_deliver_preserves_e1_receipt_and_exit_codes_without_running_command(
    tmp_path: Path,
    contract_text: str | None,
    repository_exists: bool,
    expected_code: str,
    expected_exit: int,
) -> None:
    repository = make_repo(tmp_path / "repo") if repository_exists else tmp_path / "missing-repo"
    contract = tmp_path / "contract.yaml"
    if contract_text is not None:
        contract.write_text(contract_text, encoding="utf-8")
    sentinel = tmp_path / "command-ran"
    command = (sys.executable, "-c", f"from pathlib import Path; Path({str(sentinel)!r}).touch()")

    proc, receipt = run_delivery(repository, contract, command)

    assert proc.returncode == expected_exit
    assert proc.stdout.count("\n") == 1
    assert receipt["code"] == expected_code
    assert receipt["candidate_ref"] is None
    assert not sentinel.exists()


@pytest.mark.parametrize(
    "dirty",
    [
        lambda repo: (repo / "kept.txt").write_text("changed\n", encoding="utf-8"),
        lambda repo: (repo / "deleted.txt").unlink(),
        lambda repo: (repo / "untracked.txt").write_text("new\n", encoding="utf-8"),
    ],
    ids=["tracked", "deleted", "untracked"],
)
def test_dirty_source_is_refused_before_worktree(
    tmp_path: Path,
    dirty: Callable[[Path], object],
) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml")
    dirty(repo)
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract)

    assert proc.returncode == 8
    assert receipt["code"] == "REPO_DIRTY"
    assert receipt["candidate_ref"] is None
    assert_source_unchanged(repo, before)


def test_subdirectory_is_refused_but_symlink_to_root_is_accepted(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    subdirectory = repo / "package"
    subdirectory.mkdir()
    (subdirectory / ".keep").write_text("", encoding="utf-8")
    assert git(repo, "add", "-A").returncode == 0
    assert git(repo, "commit", "--quiet", "-m", "package").returncode == 0
    contract = write_contract(tmp_path / "contract.yaml")
    before = source_snapshot(repo)

    _, refused = run_delivery(subdirectory, contract)
    assert refused["code"] == "REPO_NOT_GIT"
    assert_source_unchanged(repo, before)

    symlink = tmp_path / "repo-link"
    symlink.symlink_to(repo, target_is_directory=True)
    _, accepted = run_delivery(symlink, contract)
    assert accepted["code"] == "NO_CHANGES", accepted
    assert accepted["repository"] == os.path.abspath(symlink)
    assert_source_unchanged(repo, before)


def test_repository_root_ending_in_newline_is_accepted(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo\n")
    contract = write_contract(tmp_path / "contract.yaml", "newline-root")
    before = source_snapshot(repo)

    _, receipt = run_delivery(repo, contract)

    assert receipt["code"] == "NO_CHANGES", receipt
    assert receipt["repository"] == os.path.abspath(repo)
    assert_source_unchanged(repo, before)


def test_linked_worktree_root_is_accepted(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    linked = tmp_path / "linked"
    assert git(repo, "worktree", "add", "--quiet", "--detach", str(linked), "HEAD").returncode == 0
    contract = write_contract(tmp_path / "contract.yaml", "linked")
    before = source_snapshot(linked)

    _, receipt = run_delivery(linked, contract)

    assert receipt["code"] == "NO_CHANGES", receipt
    assert_source_unchanged(linked, before)


@pytest.mark.parametrize("candidate_id", ["team/foo", "bad..id"])
def test_invalid_candidate_id_is_refused_before_worktree(tmp_path: Path, candidate_id: str) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", candidate_id)
    before = source_snapshot(repo)

    _, receipt = run_delivery(repo, contract)

    assert receipt["code"] == "INVALID_CANDIDATE_ID"
    assert receipt["candidate_ref"] is None
    assert_source_unchanged(repo, before)


@pytest.mark.parametrize("escaped_id", [r"\0", r"\uDCFF"], ids=["nul", "surrogate"])
def test_non_processable_candidate_id_is_a_named_refusal(tmp_path: Path, escaped_id: str) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = tmp_path / "contract.yaml"
    contract.write_text(f'id: "{escaped_id}"\ntask: test\n', encoding="utf-8")
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract)

    assert proc.returncode == 8
    assert receipt["code"] == "INVALID_CANDIDATE_ID", receipt
    assert receipt["candidate_ref"] is None
    assert_source_unchanged(repo, before)


@pytest.mark.parametrize("ref_kind", ["ordinary", "symbolic", "dangling-symbolic"])
def test_existing_candidate_ref_is_refused_before_worktree(tmp_path: Path, ref_kind: str) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml")
    candidate_ref = "refs/satyrn/candidates/greeting/head"
    if ref_kind == "ordinary":
        assert git(repo, "update-ref", candidate_ref, "HEAD").returncode == 0
    else:
        target = "refs/heads/master" if ref_kind == "symbolic" else "refs/heads/missing"
        assert git(repo, "symbolic-ref", candidate_ref, target).returncode == 0
    before = source_snapshot(repo)

    _, receipt = run_delivery(repo, contract)

    assert receipt["code"] == "CANDIDATE_EXISTS", receipt
    assert receipt["candidate_ref"] == candidate_ref
    assert_source_unchanged(repo, before)


def test_success_creates_candidate_with_exact_parent_and_paths(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    signer_sentinel = tmp_path / "signer-ran"
    signer = tmp_path / "signer"
    signer.write_text(
        f"#!/bin/sh\nprintf '' > {shlex.quote(str(signer_sentinel))}\nexit 1\n",
        encoding="utf-8",
    )
    signer.chmod(0o755)
    assert git(repo, "config", "commit.gpgSign", "true").returncode == 0
    assert git(repo, "config", "gpg.program", str(signer)).returncode == 0
    contract = write_contract(tmp_path / "contract.yaml", "three-paths")
    script = (
        "from pathlib import Path; "
        "Path('kept.txt').write_text('after\\n'); "
        "Path('added.txt').write_text('new\\n'); "
        "Path('deleted.txt').unlink(); "
        "print('attempt stdout', flush=True); "
        "import sys; print('attempt stderr', file=sys.stderr)"
    )
    before = source_snapshot(repo)
    branch = git(repo, "symbolic-ref", "HEAD").stdout

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", script))

    assert proc.returncode == 0
    assert proc.stdout.count("\n") == 1
    assert proc.stderr == "attempt stdout\nattempt stderr\n"
    candidate_ref = str(receipt["candidate_ref"])
    candidate_commit = str(receipt["candidate_commit"])
    assert receipt == {
        "version": 1,
        "outcome": "candidate-created",
        "code": "OK",
        "message": "candidate created",
        "contract_id": "three-paths",
        "repository": os.path.abspath(repo),
        "base_commit": before[0].strip().decode(),
        "candidate_ref": "refs/satyrn/candidates/three-paths/head",
        "candidate_commit": candidate_commit,
        "changed_paths": ["added.txt", "deleted.txt", "kept.txt"],
        "command_exit": 0,
        "validation": "not_requested",
        "validation_exit": None,
        "validation_output": None,
        "worktree_path": None,
        "budget": {
            "state": "not_declared",
            "turns_used": 0,
            "seconds_used": 0.0,
            "turn_limit": None,
            "deadline_seconds": None,
            "token_limit": None,
            "tokens_used": 0,
        },
        "turns": 0,
        "tool_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "guard_firings": {
            "loop_broken": 0,
            "scope_refused": 0,
            "symbol_preserved": 0,
            "command_bounded": 0,
            "command_timed_out": 0,
            "self_test_redirected": 0,
        },
        "carried": {
            "preserve": [],
            "checks": [],
            "infrastructure": [],
            "absent": [],
            "tampered": [],
        },
    }
    assert git(repo, "rev-parse", candidate_ref).stdout.strip().decode() == candidate_commit
    assert git(repo, "rev-parse", f"{candidate_commit}^").stdout == before[0]
    assert git(repo, "show", f"{candidate_commit}:kept.txt").stdout == b"after\n"
    assert git(repo, "show", f"{candidate_commit}:added.txt").stdout == b"new\n"
    assert git(repo, "cat-file", "-e", f"{candidate_commit}:deleted.txt").returncode != 0
    raw_commit = git(repo, "cat-file", "commit", candidate_commit).stdout
    headers, message = raw_commit.split(b"\n\n", 1)
    assert b"gpgsig " not in headers
    assert any(
        line.startswith(b"author satyrn-engine <satyrn-engine@localhost> ")
        for line in headers.splitlines()
    )
    assert any(
        line.startswith(b"committer satyrn-engine <satyrn-engine@localhost> ")
        for line in headers.splitlines()
    )
    assert message == f"candidate: three-paths\n\nbase: {receipt['base_commit']}\n".encode()
    assert not signer_sentinel.exists()
    assert git(repo, "symbolic-ref", "HEAD").stdout == branch
    assert_source_unchanged(repo, before)


def test_engine_git_ignores_caller_date_overrides_but_command_keeps_them(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "date-overrides")
    environment = os.environ.copy()
    environment["GIT_AUTHOR_DATE"] = "not-a-date"
    environment["GIT_COMMITTER_DATE"] = "also-not-a-date"
    script = (
        "from pathlib import Path; import os; "
        "assert os.environ['GIT_AUTHOR_DATE'] == 'not-a-date'; "
        "assert os.environ['GIT_COMMITTER_DATE'] == 'also-not-a-date'; "
        "Path('date.txt').write_text('ok')"
    )
    before = source_snapshot(repo)

    _, receipt = run_delivery(
        repo,
        contract,
        (sys.executable, "-c", script),
        environment=environment,
    )

    assert receipt["code"] == "OK", receipt
    assert git(repo, "show", f"{receipt['candidate_commit']}:date.txt").stdout == b"ok"
    assert_source_unchanged(repo, before)


def test_closed_receipt_pipe_exits_one_without_hiding_published_candidate(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "closed-stdout")
    command = (sys.executable, "-c", "from pathlib import Path; Path('added.txt').write_text('ok')")
    candidate_ref = "refs/satyrn/candidates/closed-stdout/head"
    before = source_snapshot(repo)
    read_fd, write_fd = os.pipe()
    os.close(read_fd)

    try:
        proc = subprocess.Popen(
            delivery_argv(repo, contract, command, 30.0),
            cwd=ROOT,
            stdout=write_fd,
            stderr=subprocess.PIPE,
            text=True,
        )
    finally:
        os.close(write_fd)
    _, stderr = proc.communicate()

    assert proc.returncode == 1
    assert "BrokenPipeError" not in stderr
    assert git(repo, "show-ref", "--verify", candidate_ref).returncode == 0
    assert git(repo, "show", f"{candidate_ref}:added.txt").stdout == b"ok"
    assert_source_unchanged(repo, before)


def test_registration_interrupt_after_real_add_leaves_no_stale_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "repo")
    before = source_snapshot(repo)
    prepared = delivery._preflight(str(repo), "interrupt-registration")
    assert isinstance(prepared, delivery._DeliveryContext)
    real_registered = delivery._worktree_registered
    interrupted = False

    def interrupt_once(context: delivery._DeliveryContext, worktree: Path) -> bool | None:
        nonlocal interrupted
        registered = real_registered(context, worktree)
        if registered and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return registered

    monkeypatch.setattr(delivery, "_worktree_registered", interrupt_once)

    with pytest.raises(KeyboardInterrupt):
        delivery._attempt(prepared, (sys.executable, "-c", "raise AssertionError('must not run')"), 30.0)

    assert interrupted
    assert_source_unchanged(repo, before)


def test_attached_head_at_base_is_discarded_without_candidate(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "attached.yaml", "attached")
    script = (
        "from pathlib import Path; import subprocess; "
        "subprocess.run(['git', 'switch', '--quiet', '-c', 'leaked-branch'], check=True); "
        "Path('attached.txt').write_text('attached')"
    )

    _, receipt = run_delivery(repo, contract, (sys.executable, "-c", script))

    assert receipt["code"] == "COMMAND_CHANGED_HEAD", receipt
    assert git(repo, "show-ref", "--verify", "refs/heads/leaked-branch").returncode == 0
    assert git(repo, "show-ref", "--verify", str(receipt["candidate_ref"])).returncode != 0

    sibling = write_contract(tmp_path / "detached.yaml", "detached-sibling")
    _, accepted = run_delivery(
        repo,
        sibling,
        (sys.executable, "-c", "from pathlib import Path; Path('detached.txt').write_text('ok')"),
    )
    assert accepted["code"] == "OK", accepted


def test_unreadable_isolated_head_is_git_failure_before_cleanup_precedence(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "missing-gitfile")
    script = "from pathlib import Path; Path('.git').unlink()"
    before = source_snapshot(repo)

    _, receipt = run_delivery(repo, contract, (sys.executable, "-c", script))

    assert receipt["code"] == "CLEANUP_FAILED", receipt
    assert "pending result GIT_FAILED" in str(receipt["message"])
    assert git(repo, "show-ref", "--verify", str(receipt["candidate_ref"])).returncode != 0
    retained = Path(str(receipt["worktree_path"]))
    assert retained.parent.is_dir()
    shutil.rmtree(retained.parent)
    assert git(repo, "worktree", "prune").returncode == 0
    assert_source_unchanged(repo, before)


def test_caller_head_can_advance_after_preflight_without_moving_candidate_base(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "caller-advanced")
    original_head = git(repo, "rev-parse", "HEAD").stdout.strip().decode()
    script = (
        "from pathlib import Path; import subprocess, sys; "
        "source = sys.argv[1]; "
        "subprocess.run(['git', '-C', source, 'commit', '--allow-empty', '--quiet', '-m', 'caller advanced'], check=True); "
        "Path('candidate.txt').write_text('candidate')"
    )

    _, receipt = run_delivery(repo, contract, (sys.executable, "-c", script, str(repo)))

    caller_head = git(repo, "rev-parse", "HEAD").stdout.strip().decode()
    candidate_commit = str(receipt["candidate_commit"])
    assert receipt["code"] == "OK", receipt
    assert receipt["base_commit"] == original_head
    assert caller_head != original_head
    assert git(repo, "rev-parse", f"{candidate_commit}^").stdout.strip().decode() == original_head
    assert git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout == b""
    assert len(git(repo, "worktree", "list", "--porcelain", "-z").stdout.split(b"worktree ")) == 2


def test_multichunk_command_output_stays_off_receipt_stdout(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "large-output")
    size = 3 * 64 * 1024 + 17
    before = source_snapshot(repo)

    proc, receipt = run_delivery(
        repo,
        contract,
        (sys.executable, "-c", f"import sys; sys.stdout.buffer.write(b'x' * {size})"),
    )

    assert receipt["code"] == "NO_CHANGES", receipt
    assert proc.stdout.count("\n") == 1
    assert proc.stderr == "x" * size
    assert_source_unchanged(repo, before)


def test_tmpdir_inside_source_cannot_place_isolation_in_source(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "safe-temp")
    environment = os.environ.copy()
    environment["TMPDIR"] = str(repo)
    script = (
        "from pathlib import Path; import sys; "
        "source = Path(sys.argv[1]).resolve(); "
        "assert not Path.cwd().resolve().is_relative_to(source); "
        "Path('safe.txt').write_text('safe')"
    )
    before = source_snapshot(repo)

    repo.chmod(0o555)
    try:
        _, receipt = run_delivery(
            repo,
            contract,
            (sys.executable, "-c", script, str(repo)),
            environment=environment,
        )
    finally:
        repo.chmod(0o755)

    assert receipt["code"] == "OK", receipt
    assert not tuple(repo.glob("satyrn-engine-*"))
    assert_source_unchanged(repo, before)


@pytest.mark.parametrize(
    ("command", "code", "command_exit"),
    [
        (("definitely-not-a-real-e3-command",), "COMMAND_UNAVAILABLE", None),
        ((sys.executable, "-c", "raise SystemExit(9)"), "COMMAND_FAILED", 9),
        (("git", "commit", "--allow-empty", "--quiet", "-m", "moved"), "COMMAND_CHANGED_HEAD", 0),
    ],
    ids=["unavailable", "nonzero", "moved-head"],
)
def test_failed_attempt_is_discarded_without_candidate(
    tmp_path: Path,
    command: tuple[str, ...],
    code: str,
    command_exit: int | None,
) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", code.lower())
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, command)

    assert proc.returncode == 8
    expected_message = {
        "COMMAND_UNAVAILABLE": (
            f"cannot start command: {FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), command[0])}"
        ),
        "COMMAND_FAILED": "command exited with status 9",
        "COMMAND_CHANGED_HEAD": "command changed the isolated worktree HEAD",
    }[code]
    assert receipt == {
        "version": 1,
        "outcome": "refused" if code == "COMMAND_UNAVAILABLE" else "discarded",
        "code": code,
        "message": expected_message,
        "contract_id": code.lower(),
        "repository": os.path.abspath(repo),
        "base_commit": before[0].strip().decode(),
        "candidate_ref": f"refs/satyrn/candidates/{code.lower()}/head",
        "candidate_commit": None,
        "changed_paths": None,
        "command_exit": command_exit,
        "validation": "not_applicable",
        "validation_exit": None,
        "validation_output": None,
        "worktree_path": None,
        "budget": {
            "state": "not_declared",
            "turns_used": 0,
            "seconds_used": 0.0,
            "turn_limit": None,
            "deadline_seconds": None,
            "token_limit": None,
            "tokens_used": 0,
        },
        "turns": 0,
        "tool_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "guard_firings": {
            "loop_broken": 0,
            "scope_refused": 0,
            "symbol_preserved": 0,
            "command_bounded": 0,
            "command_timed_out": 0,
            "self_test_redirected": 0,
        },
        "carried": {
            "preserve": [],
            "checks": [],
            "infrastructure": [],
            "absent": [],
            "tampered": [],
        },
    }
    assert git(repo, "show-ref", "--verify", str(receipt["candidate_ref"])).returncode != 0
    assert_source_unchanged(repo, before)


def test_timeout_kills_same_process_group_descendant(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "timeout")
    sentinel = tmp_path / "late-write"
    child = f"import time, pathlib; time.sleep(0.8); pathlib.Path({str(sentinel)!r}).write_text('late')"
    parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(10)"
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", parent), timeout=0.1)

    assert proc.returncode == 8
    assert receipt == {
        "version": 1,
        "outcome": "discarded",
        "code": "COMMAND_TIMEOUT",
        "message": "command exceeded timeout of 0.1 seconds",
        "contract_id": "timeout",
        "repository": os.path.abspath(repo),
        "base_commit": before[0].strip().decode(),
        "candidate_ref": "refs/satyrn/candidates/timeout/head",
        "candidate_commit": None,
        "changed_paths": None,
        "command_exit": None,
        "validation": "not_applicable",
        "validation_exit": None,
        "validation_output": None,
        "worktree_path": None,
        "budget": {
            "state": "not_declared",
            "turns_used": 0,
            "seconds_used": 0.0,
            "turn_limit": None,
            "deadline_seconds": None,
            "token_limit": None,
            "tokens_used": 0,
        },
        "turns": 0,
        "tool_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "guard_firings": {
            "loop_broken": 0,
            "scope_refused": 0,
            "symbol_preserved": 0,
            "command_bounded": 0,
            "command_timed_out": 0,
            "self_test_redirected": 0,
        },
        "carried": {
            "preserve": [],
            "checks": [],
            "infrastructure": [],
            "absent": [],
            "tampered": [],
        },
    }
    time.sleep(1.0)
    assert not sentinel.exists()
    assert_source_unchanged(repo, before)


def test_two_delivery_processes_publish_exactly_one_candidate(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "race")
    ready_a = tmp_path / "ready-a"
    ready_b = tmp_path / "ready-b"
    script = (
        "import pathlib, sys, time; "
        "mine, other = map(pathlib.Path, sys.argv[1:]); mine.write_text('ready'); "
        "deadline = time.monotonic() + 5; "
        "exec(\"while not other.exists():\\n"
        "    assert time.monotonic() < deadline\\n"
        "    time.sleep(0.01)\"); "
        "pathlib.Path('race.txt').write_text(mine.name)"
    )
    before = source_snapshot(repo)
    commands = [
        (sys.executable, "-c", script, str(ready_a), str(ready_b)),
        (sys.executable, "-c", script, str(ready_b), str(ready_a)),
    ]

    processes = [
        subprocess.Popen(
            delivery_argv(repo, contract, command, 10.0),
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for command in commands
    ]
    completed = [process.communicate(timeout=15) for process in processes]
    receipts = [json.loads(stdout) for stdout, _ in completed]

    assert sorted(receipt["code"] for receipt in receipts) == ["CANDIDATE_EXISTS", "OK"]
    assert sorted(process.returncode for process in processes) == [0, 8]
    assert git(repo, "show-ref", "--verify", "refs/satyrn/candidates/race/head").returncode == 0
    assert_source_unchanged(repo, before)


def test_ancestor_ref_collision_is_git_failure_and_sibling_succeeds(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    assert git(repo, "update-ref", "refs/satyrn/candidates/conflict", "HEAD").returncode == 0
    before = source_snapshot(repo)
    script = "from pathlib import Path; Path('candidate.txt').write_text('candidate')"

    conflict = write_contract(tmp_path / "conflict.yaml", "conflict")
    _, refused = run_delivery(repo, conflict, (sys.executable, "-c", script))
    assert refused["code"] == "GIT_FAILED", refused
    assert refused["candidate_commit"] is not None

    sibling = write_contract(tmp_path / "sibling.yaml", "sibling")
    _, accepted = run_delivery(repo, sibling, (sys.executable, "-c", script))
    assert accepted["code"] == "OK", accepted
    assert_source_unchanged(repo, before)


def test_locked_worktree_reports_retained_path_for_manual_recovery(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "locked")
    before_head, before_status, _ = source_snapshot(repo)

    script = (
        "from pathlib import Path; import subprocess; "
        "Path('kept.txt').write_text('changed\\n'); "
        "subprocess.run(['git', 'worktree', 'lock', '.', '--reason', 'e3-test'], check=True)"
    )
    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", script))

    assert proc.returncode == 8
    assert receipt["outcome"] == "refused"
    assert receipt["code"] == "CLEANUP_FAILED", receipt
    assert "pending result OK" in str(receipt["message"])
    assert receipt["candidate_commit"] is not None
    assert receipt["changed_paths"] == ["kept.txt"]
    assert git(repo, "show-ref", "--verify", str(receipt["candidate_ref"])).returncode != 0
    retained = Path(str(receipt["worktree_path"]))
    assert retained.is_dir()
    assert git(repo, "rev-parse", "HEAD").stdout == before_head
    assert git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout == before_status

    assert git(repo, "worktree", "unlock", str(retained)).returncode == 0
    assert git(repo, "worktree", "remove", "--force", str(retained)).returncode == 0
    shutil.rmtree(retained.parent)


def test_git_routing_environment_is_removed_but_normal_environment_survives(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "environment")
    script = (
        "import os, pathlib, subprocess; "
        "assert 'GIT_DIR' not in os.environ; "
        "assert 'GIT_WORK_TREE' not in os.environ; "
        "assert 'GIT_NAMESPACE' not in os.environ; "
        "assert os.environ['GIT_TERMINAL_PROMPT'] == '0'; "
        "root = pathlib.Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()); "
        "assert root.samefile(pathlib.Path.cwd()); "
        "pathlib.Path('environment.txt').write_text(os.environ['E3_ORDINARY_VALUE'])"
    )
    environment = os.environ.copy()
    environment.update(
        GIT_DIR=str(tmp_path / "wrong-git-dir"),
        GIT_WORK_TREE=str(tmp_path / "wrong-work-tree"),
        GIT_NAMESPACE="wrong-namespace",
        GIT_TERMINAL_PROMPT="1",
        E3_ORDINARY_VALUE="preserved",
    )
    before = source_snapshot(repo)

    _, receipt = run_delivery(repo, contract, (sys.executable, "-c", script), environment=environment)

    assert receipt["code"] == "OK", receipt
    assert git(repo, "show", f"{receipt['candidate_commit']}:environment.txt").stdout == b"preserved"
    assert_source_unchanged(repo, before)


def test_checkout_hook_is_disabled_but_clean_filter_is_preserved(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    hook_sentinel = tmp_path / "hook-fired"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    post_checkout = hooks / "post-checkout"
    post_checkout.write_text(
        f"#!/bin/sh\nprintf '' > {shlex.quote(str(hook_sentinel))}\n",
        encoding="utf-8",
    )
    post_checkout.chmod(0o755)
    assert git(repo, "config", "core.hooksPath", str(hooks)).returncode == 0
    assert git(repo, "config", "filter.e3.clean", "sed 's/dirty/clean/g'").returncode == 0
    assert git(repo, "config", "filter.e3.smudge", "cat").returncode == 0
    (repo / ".gitattributes").write_text("filtered.txt filter=e3\n", encoding="utf-8")
    (repo / "filtered.txt").write_text("base\n", encoding="utf-8")
    assert git(repo, "add", "-A").returncode == 0
    assert git(repo, "commit", "--quiet", "-m", "filter").returncode == 0
    reference_sentinel = tmp_path / "reference-hook-fired"
    reference_transaction = hooks / "reference-transaction"
    reference_transaction.write_text(
        f"#!/bin/sh\nprintf '' > {shlex.quote(str(reference_sentinel))}\n",
        encoding="utf-8",
    )
    reference_transaction.chmod(0o755)
    fsmonitor_sentinel = tmp_path / "fsmonitor-fired"
    fsmonitor = tmp_path / "fsmonitor-hook"
    fsmonitor.write_text(
        f"#!/bin/sh\nprintf '' > {shlex.quote(str(fsmonitor_sentinel))}\n",
        encoding="utf-8",
    )
    fsmonitor.chmod(0o755)
    assert git(repo, "config", "core.fsmonitor", str(fsmonitor)).returncode == 0
    contract = write_contract(tmp_path / "contract.yaml", "filter")
    script = "from pathlib import Path; Path('filtered.txt').write_text('dirty\\n')"
    before = source_snapshot(repo)
    fsmonitor_sentinel.unlink(missing_ok=True)

    _, receipt = run_delivery(repo, contract, (sys.executable, "-c", script))

    assert receipt["code"] == "OK", receipt
    assert not hook_sentinel.exists()
    assert not reference_sentinel.exists()
    assert not fsmonitor_sentinel.exists()
    assert git(repo, "show", f"{receipt['candidate_commit']}:filtered.txt").stdout == b"clean\n"
    assert_source_unchanged(repo, before)


def test_utf8_paths_use_raw_byte_order(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "utf8")
    script = (
        "from pathlib import Path; "
        "[Path(name).write_text(name) for name in ('é.txt', 'z.txt', 'a.txt')]"
    )
    before = source_snapshot(repo)

    _, receipt = run_delivery(repo, contract, (sys.executable, "-c", script))

    assert receipt["code"] == "OK", receipt
    assert receipt["changed_paths"] == ["a.txt", "z.txt", "é.txt"]
    assert_source_unchanged(repo, before)


def test_receipt_is_utf8_when_python_stdio_encoding_is_ascii(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "utf8-receipt")
    script = "from pathlib import Path; Path('é.txt').write_text('utf8')"
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "ascii"
    before = source_snapshot(repo)

    proc, receipt = run_delivery(
        repo,
        contract,
        (sys.executable, "-c", script),
        environment=environment,
    )

    assert proc.returncode == 0
    assert receipt["changed_paths"] == ["é.txt"]
    assert_source_unchanged(repo, before)


def test_non_utf8_path_refuses_publication_and_utf8_sibling_succeeds(tmp_path: Path) -> None:
    probe = os.path.join(os.fsencode(tmp_path), b"non-utf8-\xff-probe")
    try:
        probe_fd = os.open(probe, os.O_CREAT | os.O_WRONLY, 0o600)
    except OSError as exc:
        match exc.errno:
            case errno.EILSEQ | errno.EPERM:
                pytest.skip("the filesystem or execution environment forbids non-UTF-8 filenames")
            case _:
                raise
    else:
        os.close(probe_fd)
        os.unlink(probe)
    repo = make_repo(tmp_path / "repo")
    invalid = write_contract(tmp_path / "invalid.yaml", "non-utf8")
    invalid_script = "import os; fd = os.open(b'\\xff', os.O_CREAT | os.O_WRONLY, 0o600); os.write(fd, b'x'); os.close(fd)"
    before = source_snapshot(repo)

    proc, refused = run_delivery(repo, invalid, (sys.executable, "-c", invalid_script))

    assert refused["code"] == "GIT_FAILED", (refused, proc.stderr)
    assert refused["candidate_commit"] is not None
    assert refused["changed_paths"] is None
    assert git(repo, "show-ref", "--verify", str(refused["candidate_ref"])).returncode != 0

    valid = write_contract(tmp_path / "valid.yaml", "utf8-sibling")
    valid_script = "from pathlib import Path; Path('valid.txt').write_text('valid')"
    _, accepted = run_delivery(repo, valid, (sys.executable, "-c", valid_script))
    assert accepted["code"] == "OK", accepted
    assert_source_unchanged(repo, before)


# --- V4: engine-owned validation against the exact candidate commit --------


def test_validation_failure_retains_candidate_and_evidence(
    tmp_path: Path,
) -> None:
    """The register's key finding, re-earned through the composed route.

    The implementer process exits successfully while its change fails the
    contract's own tests, and it never invokes the tests itself -- the engine
    runs them against the final candidate. The candidate and the failure
    evidence survive cleanup; only the coarse code differs from a clean pass.
    """
    repo = make_repo(tmp_path / "repo")
    contract = write_test_contract(
        tmp_path / "contract.yaml",
        "breaking-edit",
        (
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('app.py').read_text() == 'good'",
        ),
    )
    implementer = (
        "from pathlib import Path; "
        "Path('app.py').write_text('good'); "  # a later breaking edit
        "Path('app.py').write_text('bad')"
    )
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", implementer))

    assert proc.returncode == 13
    assert receipt["outcome"] == "candidate-created"
    assert receipt["code"] == "TESTS_FAILED"
    assert receipt["validation"] == "failed"
    assert receipt["validation_exit"] == 1
    assert receipt["candidate_commit"] is not None
    assert receipt["changed_paths"] == ["app.py"]
    assert "AssertionError" in str(receipt["validation_output"])
    assert git(repo, "show", f"{receipt['candidate_commit']}:app.py").stdout == b"bad"
    assert_source_unchanged(repo, before)


def test_validation_passes_when_the_final_candidate_passes(
    tmp_path: Path,
) -> None:
    """The sibling success test: a candidate that passes its own suite keeps
    code OK and records PASSED."""
    repo = make_repo(tmp_path / "repo")
    contract = write_test_contract(
        tmp_path / "contract.yaml",
        "passing",
        (
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('app.py').read_text() == 'good'",
        ),
    )
    implementer = "from pathlib import Path; Path('app.py').write_text('good')"
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", implementer))

    assert proc.returncode == 0
    assert receipt["code"] == "OK"
    assert receipt["validation"] == "passed"
    assert receipt["validation_exit"] == 0
    assert receipt["candidate_commit"] is not None
    assert git(repo, "show", f"{receipt['candidate_commit']}:app.py").stdout == b"good"
    assert_source_unchanged(repo, before)


def test_validation_timeout_records_timed_out_and_keeps_candidate(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_test_contract(
        tmp_path / "contract.yaml",
        "timed-out",
        (sys.executable, "-c", "import time; time.sleep(2)"),
    )
    implementer = "from pathlib import Path; Path('app.py').write_text('x')"
    before = source_snapshot(repo)

    proc, receipt = run_delivery(
        repo,
        contract,
        (sys.executable, "-c", implementer),
        timeout=0.5,
    )

    assert proc.returncode == 0
    assert receipt["code"] == "OK"
    assert receipt["validation"] == "timed_out"
    assert receipt["validation_exit"] is None
    assert receipt["candidate_commit"] is not None
    assert_source_unchanged(repo, before)


def test_validation_timeout_kills_same_process_group_descendant(
    tmp_path: Path,
) -> None:
    """A timing-out test command tears down its whole process group, so a
    descendant it spawned cannot orphan into the worktree and block removal."""
    repo = make_repo(tmp_path / "repo")
    sentinel = tmp_path / "late-write"
    child = (
        "import time, pathlib; "
        f"time.sleep(0.8); pathlib.Path({str(sentinel)!r}).write_text('late')"
    )
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(10)"
    )
    contract = write_test_contract(
        tmp_path / "contract.yaml",
        "validation-timeout-descendant",
        (sys.executable, "-c", parent),
    )
    implementer = "from pathlib import Path; Path('app.py').write_text('x')"
    before = source_snapshot(repo)

    proc, receipt = run_delivery(
        repo,
        contract,
        (sys.executable, "-c", implementer),
        timeout=0.1,
    )

    assert proc.returncode == 0
    assert receipt["code"] == "OK"
    assert receipt["validation"] == "timed_out"
    assert receipt["candidate_commit"] is not None
    time.sleep(1.0)
    assert not sentinel.exists()
    assert_source_unchanged(repo, before)


def test_validation_unavailable_records_unavailable_and_keeps_candidate(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_test_contract(
        tmp_path / "contract.yaml",
        "unavailable",
        ("definitely-not-a-real-v4-test-command",),
    )
    implementer = "from pathlib import Path; Path('app.py').write_text('x')"
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", implementer))

    assert proc.returncode == 0
    assert receipt["code"] == "OK"
    assert receipt["validation"] == "unavailable"
    assert receipt["validation_exit"] is None
    assert "cannot run test command" in str(receipt["validation_output"])
    assert receipt["candidate_commit"] is not None
    assert_source_unchanged(repo, before)


def test_no_test_command_records_not_requested(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "no-tests")
    before = source_snapshot(repo)

    _, receipt = run_delivery(
        repo,
        contract,
        (sys.executable, "-c", "from pathlib import Path; Path('app.py').write_text('x')"),
    )

    assert receipt["code"] == "OK"
    assert receipt["validation"] == "not_requested"
    assert receipt["validation_exit"] is None
    assert receipt["validation_output"] is None
    assert_source_unchanged(repo, before)


# --- V5: whole-attempt budget against real processes and real Git ----------


def test_deadline_expiry_retains_a_real_partial_candidate(tmp_path: Path) -> None:
    """A real wall-clock deadline stops a still-running implementer, commits
    whatever file it already wrote, and retains that partial candidate.

    The implementer writes ``app.py`` and then sleeps past a 2-second
    deadline; the engine tears the process group down and publishes the
    partial work as a BUDGET_EXHAUSTED candidate-created receipt (exit 14)."""
    repo = make_repo(tmp_path / "repo")
    contract = write_budget_contract(
        tmp_path / "contract.yaml",
        "deadline-partial",
        deadline_seconds=2.0,
    )
    implementer = (
        "from pathlib import Path; "
        "Path('app.py').write_text('partial'); "
        "import time; time.sleep(30)"
    )
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", implementer))

    assert proc.returncode == 14
    assert receipt["outcome"] == "candidate-created"
    assert receipt["code"] == "BUDGET_EXHAUSTED"
    assert receipt["budget"]["state"] == "deadline_exhausted"
    assert receipt["budget"]["turn_limit"] is None
    assert receipt["budget"]["deadline_seconds"] == 2.0
    assert receipt["budget"]["turns_used"] == 0
    assert receipt["budget"]["seconds_used"] > 2.0
    assert receipt["command_exit"] is None
    assert receipt["candidate_commit"] is not None
    assert receipt["changed_paths"] == ["app.py"]
    assert git(repo, "show", f"{receipt['candidate_commit']}:app.py").stdout == b"partial"
    assert_source_unchanged(repo, before)


def test_within_deadline_succeeds(tmp_path: Path) -> None:
    """The success sibling: a change that finishes before the deadline is a
    clean OK, with the budget state WITHIN rather than exhausted."""
    repo = make_repo(tmp_path / "repo")
    contract = write_budget_contract(
        tmp_path / "contract.yaml",
        "deadline-ok",
        deadline_seconds=10.0,
    )
    implementer = "from pathlib import Path; Path('app.py').write_text('done')"
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", implementer))

    assert proc.returncode == 0
    assert receipt["code"] == "OK"
    assert receipt["budget"]["state"] == "within"
    assert receipt["budget"]["deadline_seconds"] == 10.0
    assert receipt["budget"]["seconds_used"] <= 10.0
    assert receipt["candidate_commit"] is not None
    assert git(repo, "show", f"{receipt['candidate_commit']}:app.py").stdout == b"done"
    assert_source_unchanged(repo, before)


def test_turn_limit_stops_a_streamed_implementer_retaining_partial(
    tmp_path: Path,
) -> None:
    """A real turn limit trips on the implementer's own streamed
    ``turn_start`` lines and stops the still-running process, retaining the
    file it wrote before the 4th turn crossed a limit of 3."""
    repo = make_repo(tmp_path / "repo")
    contract = write_budget_contract(
        tmp_path / "contract.yaml",
        "turn-partial",
        turn_budget=3,
    )
    implementer = (
        "from pathlib import Path; "
        "Path('app.py').write_text('partial'); "
        "[print('{\"type\":\"turn_start\"}', flush=True) for _ in range(4)]; "
        "import time; time.sleep(30)"
    )
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", implementer))

    assert proc.returncode == 14
    assert receipt["outcome"] == "candidate-created"
    assert receipt["code"] == "BUDGET_EXHAUSTED"
    assert receipt["budget"]["state"] == "turn_exhausted"
    assert receipt["budget"]["turn_limit"] == 3
    assert receipt["budget"]["deadline_seconds"] is None
    assert receipt["budget"]["turns_used"] == 4
    assert receipt["command_exit"] is None
    assert receipt["candidate_commit"] is not None
    assert receipt["changed_paths"] == ["app.py"]
    assert git(repo, "show", f"{receipt['candidate_commit']}:app.py").stdout == b"partial"
    assert_source_unchanged(repo, before)


def test_within_turn_limit_succeeds(tmp_path: Path) -> None:
    """The success sibling: a streamed implementer that stays under the turn
    limit is a clean OK with the budget state WITHIN."""
    repo = make_repo(tmp_path / "repo")
    contract = write_budget_contract(
        tmp_path / "contract.yaml",
        "turn-ok",
        turn_budget=5,
    )
    implementer = (
        "from pathlib import Path; "
        "Path('app.py').write_text('done'); "
        "[print('{\"type\":\"turn_start\"}', flush=True) for _ in range(3)]"
    )
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", implementer))

    assert proc.returncode == 0
    assert receipt["code"] == "OK"
    assert receipt["budget"]["state"] == "within"
    assert receipt["budget"]["turn_limit"] == 5
    assert receipt["budget"]["turns_used"] == 3
    assert receipt["candidate_commit"] is not None
    assert git(repo, "show", f"{receipt['candidate_commit']}:app.py").stdout == b"done"
    assert_source_unchanged(repo, before)


def test_exhausted_failed_validation_retains_partial_and_failure_evidence(
    tmp_path: Path,
) -> None:
    """When a partial (turn-exhausted) candidate fails the contract's own
    tests, both kinds of evidence survive the worktree cleanup: the partial
    candidate commit/file content, and the failing validation output.

    V5's confirmed reading holds at the process level: the coarse code stays
    BUDGET_EXHAUSTED (not TESTS_FAILED), because an exhausted attempt is
    authoritative regardless of its self-test."""
    repo = make_repo(tmp_path / "repo")
    contract = write_budget_contract(
        tmp_path / "contract.yaml",
        "exhausted-failing",
        turn_budget=2,
        test_command=(
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('app.py').read_text() == 'good'",
        ),
    )
    implementer = (
        "from pathlib import Path; "
        "Path('app.py').write_text('partial'); "
        "[print('{\"type\":\"turn_start\"}', flush=True) for _ in range(3)]; "
        "import time; time.sleep(30)"
    )
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", implementer))

    assert proc.returncode == 14
    assert receipt["code"] == "BUDGET_EXHAUSTED"
    assert receipt["validation"] == "failed"
    assert receipt["validation_exit"] == 1
    assert "AssertionError" in str(receipt["validation_output"])
    assert receipt["candidate_commit"] is not None
    assert git(repo, "show", f"{receipt['candidate_commit']}:app.py").stdout == b"partial"
    # Evidence survives cleanup: the ref resolves, the partial file is
    # readable from the object store, and the isolated worktree is gone.
    assert git(
        repo, "rev-parse", "--verify", f"{receipt['candidate_ref']}^{{commit}}"
    ).returncode == 0
    assert_source_unchanged(repo, before)


def test_exhausted_passed_validation_stays_exhausted_with_evidence(
    tmp_path: Path,
) -> None:
    """The success sibling for the failure case above: when the partial
    candidate passes its self-test, the code is still BUDGET_EXHAUSTED (a
    passed validation is not a completion) and the partial evidence still
    survives cleanup."""
    repo = make_repo(tmp_path / "repo")
    contract = write_budget_contract(
        tmp_path / "contract.yaml",
        "exhausted-passing",
        turn_budget=2,
        test_command=(
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('app.py').read_text() == 'partial'",
        ),
    )
    implementer = (
        "from pathlib import Path; "
        "Path('app.py').write_text('partial'); "
        "[print('{\"type\":\"turn_start\"}', flush=True) for _ in range(3)]; "
        "import time; time.sleep(30)"
    )
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", implementer))

    assert proc.returncode == 14
    assert receipt["code"] == "BUDGET_EXHAUSTED"
    assert receipt["validation"] == "passed"
    assert receipt["validation_exit"] == 0
    assert receipt["candidate_commit"] is not None
    assert git(repo, "show", f"{receipt['candidate_commit']}:app.py").stdout == b"partial"
    assert git(
        repo, "rev-parse", "--verify", f"{receipt['candidate_ref']}^{{commit}}"
    ).returncode == 0
    assert_source_unchanged(repo, before)


# --- Task 8: R9 at validation, and staging excludes residue -----------------


def make_carried_repo(path: Path) -> Path:
    """A fixture repo with a preserve test, a checks file, a conftest fixture
    it depends on, a pytest config, and a ``.gitignore`` for ``.venv/`` (N1)."""
    path.mkdir()
    assert git(path, "init", "--quiet", "--initial-branch=master").returncode == 0
    assert git(path, "config", "user.name", "E3 Test").returncode == 0
    assert git(path, "config", "user.email", "e3@example.invalid").returncode == 0
    (path / "app.py").write_text("value = 1\n", encoding="utf-8")
    (path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-p no:cacheprovider"\n', encoding="utf-8"
    )
    (path / "tests").mkdir()
    (path / "tests" / "conftest.py").write_text(
        "import pytest\n\n\n@pytest.fixture\ndef two():\n    return 2\n", encoding="utf-8"
    )
    (path / "tests" / "test_keep.py").write_text(
        "import app\n\n\ndef test_keep(two):\n    assert two == 2\n    assert app.value == 1\n",
        encoding="utf-8",
    )
    (path / "checks").mkdir()
    (path / "checks" / "check_x.py").write_text(
        "def test_check_x():\n    assert True\n", encoding="utf-8"
    )
    (path / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    assert git(path, "add", "-A").returncode == 0
    assert git(path, "commit", "--quiet", "-m", "base").returncode == 0
    return path


def write_carried_contract(
    path: Path,
    candidate_id: str,
    *,
    test_command: Sequence[str] = (),
    preserve: Sequence[str] = (),
    checks: Sequence[str] = (),
) -> Path:
    data: dict[str, object] = {
        "id": candidate_id,
        "task": "make a bounded change",
    }
    if test_command:
        data["test_command"] = list(test_command)
    if preserve:
        data["preserve"] = list(preserve)
    if checks:
        data["checks"] = list(checks)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_validation_runs_with_the_carried_set_restored_and_checks_as_files(
    tmp_path: Path,
) -> None:
    """The restored ``test_keep.py`` runs with the conftest fixture; the
    model's overwrite of it never counts. ``checks/check_x.py`` runs as an
    explicit file argument, collected regardless of default ``python_files``.
    Residue the attempt creates (``.venv``, ``__pycache__``) never reaches the
    candidate (N1)."""
    repo = make_carried_repo(tmp_path / "repo")
    contract = write_carried_contract(
        tmp_path / "contract.yaml",
        "carried-ok",
        test_command=(sys.executable, "-m", "pytest", "-q"),
        preserve=("tests/test_keep.py",),
        checks=("checks/check_x.py",),
    )
    script = (
        "from pathlib import Path; "
        "p = Path('app.py'); p.write_text(p.read_text() + '\\n# appended\\n'); "
        "Path('tests/test_keep.py').write_text('def test_keep(): assert False\\n'); "
        "Path('.venv/marker').parent.mkdir(parents=True, exist_ok=True); "
        "Path('.venv/marker').write_text(''); "
        "Path('__pycache__').mkdir(exist_ok=True); Path('__pycache__/x.pyc').write_text(''); "
        "Path('tests/__pycache__').mkdir(exist_ok=True); "
        "Path('tests/__pycache__/y.pyc').write_text('')"
    )
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", script))

    assert proc.returncode == 0, receipt
    assert receipt["code"] == "OK"
    assert receipt["validation"] == "passed"
    assert str(receipt["validation_output"]).count("passed") == 3
    assert receipt["changed_paths"] == ["app.py", "tests/test_keep.py"]
    assert receipt["carried"] == {
        "preserve": ["tests/test_keep.py"],
        "checks": ["checks/check_x.py"],
        "infrastructure": ["tests/conftest.py", "pyproject.toml"],
        "absent": [],
        "tampered": ["tests/test_keep.py"],
    }
    assert_source_unchanged(repo, before)


def test_a_written_conftest_cannot_hide_a_failing_preserve_test(tmp_path: Path) -> None:
    """N2: a model that rewrites ``conftest.py`` to ``collect_ignore`` the
    preserve test, and edits ``pyproject.toml`` to deselect it, cannot hide a
    failure -- both are carried infrastructure, restored before the suite
    runs, so the deselection never takes effect."""
    repo = make_carried_repo(tmp_path / "repo")
    contract = write_carried_contract(
        tmp_path / "contract.yaml",
        "carried-tampered",
        test_command=(sys.executable, "-m", "pytest", "-q"),
        preserve=("tests/test_keep.py",),
        checks=("checks/check_x.py",),
    )
    script = (
        "from pathlib import Path; "
        "p = Path('app.py'); p.write_text(p.read_text() + 'value = 2\\n'); "
        "Path('tests/conftest.py').write_text(\"collect_ignore = ['test_keep.py']\\n\"); "
        "Path('pyproject.toml').write_text("
        "'[tool.pytest.ini_options]\\naddopts = \\'--deselect tests/test_keep.py\\'\\n')"
    )
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", script))

    assert proc.returncode == 13, receipt
    assert receipt["code"] == "TESTS_FAILED"
    assert receipt["validation"] == "failed"
    assert "FAILED tests/test_keep.py" in str(receipt["validation_output"])
    assert receipt["carried"]["tampered"] == ["pyproject.toml", "tests/conftest.py"]
    assert_source_unchanged(repo, before)


def test_a_gitignored_venv_does_not_fail_staging(tmp_path: Path) -> None:
    """N1: staging must not fail on a globally-ignored ``.venv`` -- this test
    deliberately runs under the maintainer's own real global Git config
    (no ``GIT_CONFIG_GLOBAL`` override) so a global ``core.excludesFile`` that
    also ignores ``.venv`` is exercised, not just the repo's own
    ``.gitignore``."""
    repo = make_carried_repo(tmp_path / "repo")
    contract = write_contract(tmp_path / "contract.yaml", "venv-residue")
    script = (
        "from pathlib import Path; "
        "p = Path('app.py'); p.write_text(p.read_text() + '\\n# appended\\n'); "
        "Path('.venv/bin').mkdir(parents=True, exist_ok=True); "
        "Path('.venv/bin/python').write_text('#!/bin/sh\\n')"
    )
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", script))

    assert proc.returncode == 0, receipt
    assert receipt["code"] == "OK"
    assert receipt["changed_paths"] == ["app.py"]
    assert ".venv/bin/python" not in (receipt["changed_paths"] or [])
    assert_source_unchanged(repo, before)


def test_a_preserve_path_absent_at_base_lands_in_carried_absent(tmp_path: Path) -> None:
    """The sibling of the carried-restoration tests: a declared ``preserve``
    path that was never tracked at the base is named in ``carried.absent``,
    and validation still runs against whatever the carried set did find."""
    repo = make_carried_repo(tmp_path / "repo")
    contract = write_carried_contract(
        tmp_path / "contract.yaml",
        "carried-absent",
        test_command=(sys.executable, "-m", "pytest", "-q"),
        preserve=("tests/test_missing.py",),
    )
    script = "from pathlib import Path; p = Path('app.py'); p.write_text(p.read_text() + '\\n# noop\\n')"
    before = source_snapshot(repo)

    proc, receipt = run_delivery(repo, contract, (sys.executable, "-c", script))

    assert proc.returncode == 0, receipt
    assert receipt["code"] == "OK"
    assert receipt["validation"] == "passed"
    assert receipt["carried"]["absent"] == ["tests/test_missing.py"]
    assert_source_unchanged(repo, before)
