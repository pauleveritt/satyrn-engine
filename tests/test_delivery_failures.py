"""Coverage for E3 failures that require broken Git or OS behavior.

Normal delivery is exercised through real Git subprocesses in the integration
tier. Mocks here are limited to failures that would otherwise require
corrupting Git internals or interrupting the Python process.
"""

import io
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import satyrn_engine.delivery as delivery
from satyrn_engine.exits import ExitCode


def git_result(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> delivery._GitResult:
    return delivery._GitResult(returncode, stdout, stderr)


def context(root: Path) -> delivery._DeliveryContext:
    return delivery._DeliveryContext(
        repository=str(root),
        root=root,
        environment=os.environ.copy(),
        contract_id="failure",
        base_commit="a" * 40,
        candidate_ref="refs/satyrn/candidates/failure/head",
    )


def test_deliver_keeps_e1_codes_and_contract_identity(tmp_path: Path) -> None:
    missing = delivery.deliver(tmp_path, tmp_path / "missing.yaml", ("unused",))
    assert missing.code == "CONTRACT_UNREADABLE"
    assert missing.exit_code is ExitCode.CONTRACT_UNREADABLE
    assert missing.contract_id is None

    contract = tmp_path / "contract.yaml"
    contract.write_text("id: known\ntask: test\n", encoding="utf-8")
    unavailable = delivery.deliver(tmp_path / "missing-repo", contract, ("unused",))
    assert unavailable.code == "REPO_UNAVAILABLE"
    assert unavailable.contract_id == "known"


def test_preflight_reports_git_environment_discovery_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delivery, "_sanitized_environment", lambda repository: "git is unavailable")
    receipt = delivery._preflight(str(tmp_path), "candidate")
    assert isinstance(receipt, delivery.DeliveryReceipt)
    assert receipt.code == "GIT_FAILED"


@pytest.mark.parametrize("failure", ["root", "samefile", "head", "status", "ref"])
def test_preflight_names_abnormal_git_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    monkeypatch.setattr(delivery, "_sanitized_environment", lambda repository: {})
    if failure == "samefile":
        monkeypatch.setattr(delivery.os.path, "samefile", lambda left, right: (_ for _ in ()).throw(OSError()))
    if failure == "ref":
        monkeypatch.setattr(delivery, "_ref_exists", lambda root, environment, ref: None)

    def broken_git(cwd: Path, environment: dict[str, str], *args: str, input_bytes: bytes | None = None) -> delivery._GitResult:
        del cwd, environment, input_bytes
        match args:
            case ("rev-parse", "--show-toplevel") if failure == "root":
                return git_result(128, stderr=b"not a repository")
            case ("rev-parse", "--show-toplevel"):
                return git_result(stdout=os.fsencode(tmp_path) + b"\n")
            case ("rev-parse", "--verify", "HEAD^{commit}") if failure == "head":
                return git_result(128, stderr=b"bad HEAD")
            case ("rev-parse", "--verify", "HEAD^{commit}"):
                return git_result(stdout=b"a" * 40 + b"\n")
            case (_, "status", *_) if failure == "status":
                return git_result(128, stderr=b"bad index")
            case (_, "status", *_):
                return git_result()
            case ("check-ref-format", _):
                return git_result()
            case _:
                raise AssertionError(args)

    monkeypatch.setattr(delivery, "_git", broken_git)
    receipt = delivery._preflight(str(tmp_path), "candidate")
    assert isinstance(receipt, delivery.DeliveryReceipt)
    expected = "REPO_NOT_GIT" if failure in {"root", "samefile", "head"} else "GIT_FAILED"
    assert receipt.code == expected


@pytest.mark.parametrize("registered", [False, True])
def test_worktree_add_failure_cleans_temporary_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registered: bool,
) -> None:
    parent = tmp_path / "owned"
    parent.mkdir()
    monkeypatch.setattr(delivery, "_temporary_parent", lambda ctx: parent)
    monkeypatch.setattr(delivery, "_git", lambda *args, **kwargs: git_result(128, stderr=b"add failed"))
    monkeypatch.setattr(delivery, "_worktree_registered", lambda *args: registered)
    monkeypatch.setattr(delivery, "_remove_worktree", lambda *args: None)

    receipt = delivery._attempt(context(tmp_path), ("unused",), 1.0)

    assert receipt.code == "GIT_FAILED"
    assert not parent.exists()


def test_attempt_names_temporary_allocation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delivery, "_temporary_parent", lambda ctx: "no safe temporary directory")
    receipt = delivery._attempt(context(tmp_path), ("unused",), 1.0)
    assert receipt.code == "GIT_FAILED"
    assert receipt.message == "no safe temporary directory"


@pytest.mark.parametrize("cleanup_failure", [None, "locked", "raises"])
def test_unexpected_attempt_exception_uses_the_same_cleanup_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cleanup_failure: str | None,
) -> None:
    parent = tmp_path / "owned"
    parent.mkdir()
    monkeypatch.setattr(delivery, "_temporary_parent", lambda ctx: parent)
    monkeypatch.setattr(delivery, "_git", lambda *args, **kwargs: git_result())
    monkeypatch.setattr(delivery, "_worktree_registered", lambda *args: True)
    monkeypatch.setattr(delivery, "_run_and_commit", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))
    if cleanup_failure == "raises":
        monkeypatch.setattr(delivery, "_remove_worktree", lambda *args: (_ for _ in ()).throw(OSError("cleanup")))
    else:
        monkeypatch.setattr(delivery, "_remove_worktree", lambda *args: cleanup_failure)

    with pytest.raises(RuntimeError, match="boom"):
        delivery._attempt(context(tmp_path), ("unused",), 1.0)

    if cleanup_failure is None:
        assert not parent.exists()
        assert capsys.readouterr().err == ""
    elif cleanup_failure == "locked":
        assert parent.exists()
        assert "retained path" in capsys.readouterr().err
        shutil.rmtree(parent)
    else:
        assert parent.exists()
        assert "cleanup raised" in capsys.readouterr().err
        shutil.rmtree(parent)


def test_registration_lookup_exception_retains_uncertain_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parent = tmp_path / "owned"
    parent.mkdir()
    monkeypatch.setattr(delivery, "_temporary_parent", lambda ctx: parent)
    monkeypatch.setattr(delivery, "_git", lambda *args, **kwargs: git_result())
    monkeypatch.setattr(delivery, "_worktree_registered", lambda *args: (_ for _ in ()).throw(RuntimeError("list")))

    with pytest.raises(RuntimeError, match="list"):
        delivery._attempt(context(tmp_path), ("unused",), 1.0)
    assert parent.exists()
    assert "retained path" in capsys.readouterr().err
    shutil.rmtree(parent)


def test_uncertain_registration_never_runs_command_and_cleans_conservatively(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "owned"
    parent.mkdir()
    command_called = False
    removed_worktrees: list[Path] = []

    def unexpected_command(*args: object) -> delivery.DeliveryReceipt:
        nonlocal command_called
        command_called = True
        raise AssertionError(args)

    monkeypatch.setattr(delivery, "_temporary_parent", lambda ctx: parent)
    monkeypatch.setattr(delivery, "_git", lambda *args, **kwargs: git_result())
    monkeypatch.setattr(delivery, "_worktree_registered", lambda *args: None)
    monkeypatch.setattr(
        delivery,
        "_remove_worktree",
        lambda ctx, worktree: removed_worktrees.append(worktree),
    )
    monkeypatch.setattr(delivery, "_run_and_commit", unexpected_command)

    receipt = delivery._attempt(context(tmp_path), ("unused",), 1.0)

    assert receipt.code == "GIT_FAILED"
    assert receipt.message == "cannot confirm that Git registered the isolated worktree"
    assert not command_called
    assert removed_worktrees == [parent / "worktree"]
    assert not parent.exists()


def test_temporary_parent_removal_failure_is_a_visible_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "owned"
    parent.mkdir()
    real_rmtree = shutil.rmtree
    monkeypatch.setattr(delivery, "_temporary_parent", lambda ctx: parent)
    monkeypatch.setattr(delivery, "_git", lambda *args, **kwargs: git_result())
    monkeypatch.setattr(delivery, "_worktree_registered", lambda *args: True)
    monkeypatch.setattr(delivery, "_remove_worktree", lambda *args: None)
    monkeypatch.setattr(
        delivery,
        "_run_and_commit",
        lambda ctx, *args: delivery._context_receipt(
            ctx,
            delivery.DeliveryCode.NO_CHANGES,
            "command produced no changes",
            changed_paths=(),
            command_exit=0,
        ),
    )
    monkeypatch.setattr(delivery.shutil, "rmtree", lambda path: (_ for _ in ()).throw(OSError("busy")))

    receipt = delivery._attempt(context(tmp_path), ("unused",), 1.0)

    assert receipt.code == "CLEANUP_FAILED"
    assert receipt.worktree_path == str(parent)
    real_rmtree(parent)


class SuccessfulProcess:
    pid = 123
    returncode = 0

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        raise AssertionError("completed process must not be killed")


@pytest.mark.parametrize(
    ("failure", "expected_commit"),
    [
        ("head", None),
        ("symbolic-head", None),
        ("add", None),
        ("write-tree", None),
        ("base-tree", None),
        ("commit-tree", None),
        ("diff-tree", "c" * 40),
        ("non-utf8", "c" * 40),
    ],
)
def test_run_and_commit_reports_internal_git_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_commit: str | None,
) -> None:
    ctx = context(tmp_path)
    monkeypatch.setattr(delivery.subprocess, "Popen", lambda *args, **kwargs: SuccessfulProcess())

    def broken_git(cwd: Path, environment: dict[str, str], *args: str, input_bytes: bytes | None = None) -> delivery._GitResult:
        del cwd, environment, input_bytes
        match args:
            case ("rev-parse", "--verify", "HEAD^{commit}") if failure == "head":
                return git_result(128, stderr=b"head unreadable")
            case ("rev-parse", "--verify", "HEAD^{commit}"):
                return git_result(stdout=ctx.base_commit.encode() + b"\n")
            case ("symbolic-ref", "--quiet", "HEAD") if failure == "symbolic-head":
                return git_result(128, stderr=b"attachment unreadable")
            case ("symbolic-ref", "--quiet", "HEAD"):
                return git_result(1)
            case ("add", "-A") if failure == "add":
                return git_result(128, stderr=b"add failed")
            case ("add", "-A"):
                return git_result()
            case ("write-tree",) if failure == "write-tree":
                return git_result(128, stderr=b"write-tree failed")
            case ("write-tree",):
                return git_result(stdout=b"b" * 40 + b"\n")
            case ("rev-parse", _) if failure == "base-tree":
                return git_result(128, stderr=b"base tree failed")
            case ("rev-parse", _):
                return git_result(stdout=b"a" * 40 + b"\n")
            case (_, _, "commit-tree", *_) if failure == "commit-tree":
                return git_result(128, stderr=b"commit-tree failed")
            case (_, _, "commit-tree", *_):
                return git_result(stdout=b"c" * 40 + b"\n")
            case ("diff-tree", *_) if failure == "diff-tree":
                return git_result(128, stderr=b"diff-tree failed")
            case ("diff-tree", *_) if failure == "non-utf8":
                return git_result(stdout=b"\xff\0")
            case _:
                raise AssertionError(args)

    monkeypatch.setattr(delivery, "_git", broken_git)
    state = delivery._AttemptState(tmp_path, tmp_path, parent_exists=False)
    receipt = delivery._run_and_commit(ctx, state, ("unused",), 1.0)
    assert receipt.code == "GIT_FAILED"
    assert receipt.candidate_commit == expected_commit


def test_unexpected_command_wait_exception_terminates_before_reraising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InterruptedProcess:
        returncode = None
        calls = 0

        def wait(self, timeout: float | None = None) -> int:
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt
            return 0

    process = InterruptedProcess()
    monkeypatch.setattr(delivery.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        delivery,
        "_teardown_process_group",
        lambda candidate: delivery._TeardownResult(delivery._GroupState.GONE, True),
    )

    state = delivery._AttemptState(tmp_path, tmp_path, parent_exists=False)
    with pytest.raises(KeyboardInterrupt):
        delivery._run_and_commit(context(tmp_path), state, ("unused",), 1.0)
    assert state.cleanup_gate is delivery._CleanupGate.OPEN


def test_teardown_and_output_close_never_mask_original_wait_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OriginalFailure(BaseException):
        pass

    original = OriginalFailure("original")

    class InterruptedProcess:
        pid = 123
        returncode = None

        def poll(self) -> int | None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            raise original

        def kill(self) -> None:
            raise AssertionError("teardown is replaced in this test")

    class BrokenCloseOutput(io.BytesIO):
        def close(self) -> None:
            super().close()
            raise OSError("close failed")

    class BrokenStderr:
        def write(self, text: str) -> int:
            raise BrokenPipeError

        def flush(self) -> None:
            raise BrokenPipeError

    parent = tmp_path / "owned"
    parent.mkdir()
    remove_called = False

    def unexpected_remove(*args: object) -> str | None:
        nonlocal remove_called
        remove_called = True
        return None

    monkeypatch.setattr(delivery, "_temporary_parent", lambda ctx: parent)
    monkeypatch.setattr(delivery, "_git", lambda *args, **kwargs: git_result())
    monkeypatch.setattr(delivery, "_worktree_registered", lambda *args: True)
    monkeypatch.setattr(delivery.tempfile, "TemporaryFile", lambda **kwargs: BrokenCloseOutput())
    monkeypatch.setattr(delivery.subprocess, "Popen", lambda *args, **kwargs: InterruptedProcess())
    monkeypatch.setattr(
        delivery,
        "_teardown_process_group",
        lambda process: (_ for _ in ()).throw(RuntimeError("teardown failed")),
    )
    monkeypatch.setattr(delivery, "_remove_worktree", unexpected_remove)
    monkeypatch.setattr(delivery.sys, "stderr", BrokenStderr())

    with pytest.raises(OriginalFailure) as excinfo:
        delivery._attempt(context(tmp_path), ("unused",), 1.0)

    assert excinfo.value is original
    assert not remove_called
    assert parent.exists()
    parent.rmdir()


def test_low_level_git_and_ref_failures_are_conservative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = context(tmp_path)
    real_registered = delivery._worktree_registered
    monkeypatch.setattr(delivery, "_git", lambda *args, **kwargs: git_result())
    monkeypatch.setattr(delivery, "_worktree_registered", lambda *args: True)
    assert delivery._remove_worktree(ctx, tmp_path) == "Git still reports the worktree as registered"
    monkeypatch.setattr(delivery, "_worktree_registered", lambda *args: None)
    assert delivery._remove_worktree(ctx, tmp_path) == "cannot confirm that Git removed the worktree registration"
    monkeypatch.setattr(delivery, "_worktree_registered", lambda *args: False)
    assert delivery._remove_worktree(ctx, tmp_path) is None

    monkeypatch.setattr(delivery, "_worktree_registered", real_registered)
    monkeypatch.setattr(delivery, "_git", lambda *args, **kwargs: git_result(128))
    assert delivery._worktree_registered(ctx, tmp_path) is None
    assert delivery._ref_exists(tmp_path, {}, ctx.candidate_ref) is None

    calls = 0

    def fail_for_each_ref(*args: object, **kwargs: object) -> delivery._GitResult:
        nonlocal calls
        calls += 1
        return git_result(1 if calls == 1 else 128)

    monkeypatch.setattr(delivery, "_git", fail_for_each_ref)
    assert delivery._ref_exists(tmp_path, {}, ctx.candidate_ref) is None

    monkeypatch.setattr(delivery, "_git", lambda *args, **kwargs: git_result(2))
    assert delivery._ref_exists(tmp_path, {}, ctx.candidate_ref) is None

    empty_state = delivery._AttemptState(tmp_path, tmp_path / "worktree", parent_exists=False)
    assert delivery._cleanup_attempt(ctx, empty_state) is None


def test_temporary_parent_refuses_when_no_location_is_outside_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = context(tmp_path)
    monkeypatch.setattr(delivery, "_worktree_paths", lambda context: (Path("/"),))
    assert delivery._temporary_parent(ctx) == "cannot allocate an isolated directory outside repository worktrees"

    monkeypatch.setattr(delivery, "_worktree_paths", lambda context: None)
    assert delivery._temporary_parent(ctx) == "cannot inspect linked worktrees before allocating isolation"


def test_temporary_parent_skips_duplicate_missing_and_unwritable_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = context(tmp_path)
    monkeypatch.setattr(delivery, "_worktree_paths", lambda context: ())
    monkeypatch.setattr(delivery.tempfile, "gettempdir", lambda: "/tmp")
    monkeypatch.setattr(
        delivery.tempfile,
        "mkdtemp",
        lambda **kwargs: (_ for _ in ()).throw(OSError("unwritable")),
    )
    assert delivery._temporary_parent(ctx) == "cannot allocate an isolated directory outside repository worktrees"

    monkeypatch.setattr(delivery.tempfile, "gettempdir", lambda: tmp_path / "missing")
    assert delivery._temporary_parent(ctx) == "cannot allocate an isolated directory outside repository worktrees"


def test_temporary_parent_removes_an_unexpected_inside_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = context(tmp_path)
    monkeypatch.setattr(delivery, "_worktree_paths", lambda context: (tmp_path,))
    monkeypatch.setattr(delivery.tempfile, "gettempdir", lambda: tmp_path)

    def allocate_inside(**kwargs: object) -> str:
        del kwargs
        parent = tmp_path / "unexpected-inside"
        parent.mkdir()
        return str(parent)

    monkeypatch.setattr(delivery.tempfile, "mkdtemp", allocate_inside)
    assert delivery._temporary_parent(ctx) == "cannot allocate an isolated directory outside repository worktrees"
    assert not (tmp_path / "unexpected-inside").exists()


def test_git_environment_and_process_creation_failures_are_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_git = delivery._git
    monkeypatch.setattr(delivery, "_git", lambda *args, **kwargs: git_result(127, stderr=b"missing git"))
    assert delivery._sanitized_environment(str(tmp_path)) == "cannot discover Git local environment variables: missing git"

    monkeypatch.setattr(delivery, "_git", real_git)
    monkeypatch.setattr(delivery.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("missing")))
    result = delivery._git(tmp_path, {}, "status")
    assert result.returncode == 127
    assert result.stderr == b"missing"


def test_embedded_stderr_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    embedded = io.StringIO()
    monkeypatch.setattr(delivery.sys, "stderr", embedded)
    delivery._write_attempt_output(io.BytesIO(b"not-utf8:\xff"))
    assert embedded.getvalue() == "not-utf8:�"

    class BrokenStderr:
        def write(self, text: str) -> int:
            raise BrokenPipeError

        def flush(self) -> None:
            raise BrokenPipeError

    monkeypatch.setattr(delivery.sys, "stderr", BrokenStderr())
    delivery._write_attempt_output(io.BytesIO(b"ignored"))
    assert delivery._git_message("plain", git_result()) == "plain"


class FakeProcess:
    pid = 123
    returncode: int | None = None

    def __init__(self, *, reap_after_kill: bool = True) -> None:
        self.killed = False
        self.reap_after_kill = reap_after_kill
        self.waits = 0

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.waits += 1
        if self.killed and self.reap_after_kill:
            self.returncode = -9
            return -9
        assert timeout is not None
        raise subprocess.TimeoutExpired("command", timeout)

    def kill(self) -> None:
        self.killed = True


def test_teardown_confirms_gone_group_and_reaped_child(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess()
    monkeypatch.setattr(delivery.os, "killpg", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))

    result = delivery._teardown_process_group(process)

    assert result == delivery._TeardownResult(delivery._GroupState.GONE, True)
    assert result.cleanup_safe


def test_teardown_escalates_and_checks_group_after_reaping(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess()
    signals: list[int] = []
    probes = 0

    def killpg(pid: int, process_signal: int) -> None:
        nonlocal probes
        signals.append(process_signal)
        if process_signal == 0:
            probes += 1
            if probes == 1:
                raise ProcessLookupError

    monotonic = iter((0.0, 6.0))
    monkeypatch.setattr(delivery.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(delivery.os, "killpg", killpg)

    result = delivery._teardown_process_group(process)

    assert signals == [delivery.signal.SIGTERM, delivery.signal.SIGKILL, 0]
    assert result.cleanup_safe


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("broken")])
def test_teardown_signal_failure_never_opens_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
) -> None:
    process = FakeProcess()
    monkeypatch.setattr(delivery.os, "killpg", lambda pid, sig: (_ for _ in ()).throw(error))

    result = delivery._teardown_process_group(process)

    assert result.group is delivery._GroupState.UNKNOWN
    assert result.child_reaped
    assert not result.cleanup_safe
    assert "cannot signal process group" in str(result.detail)


def test_unreaped_child_keeps_cleanup_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(reap_after_kill=False)
    monkeypatch.setattr(delivery.os, "killpg", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))

    result = delivery._teardown_process_group(process)

    assert result.group is delivery._GroupState.GONE
    assert not result.child_reaped
    assert not result.cleanup_safe
    assert result.detail == "direct child did not exit after SIGKILL"


def test_closed_process_gate_withholds_git_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "owned"
    parent.mkdir()
    state = delivery._AttemptState(
        parent,
        parent / "worktree",
        registration=delivery._Registration.PRESENT_CONFIRMED,
        cleanup_gate=delivery._CleanupGate.CLOSED,
        process_detail="direct child did not exit after SIGKILL",
    )
    removed = False

    def unexpected_remove(*args: object) -> str | None:
        nonlocal removed
        removed = True
        return None

    monkeypatch.setattr(delivery, "_remove_worktree", unexpected_remove)

    assert delivery._cleanup_attempt(context(tmp_path), state) == (
        "direct child did not exit after SIGKILL",
        state.worktree,
    )
    assert not removed
    assert parent.exists()


def test_unconfirmed_process_teardown_supersedes_pending_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "owned"
    parent.mkdir()
    remove_called = False

    def unsafe_timeout(
        ctx: delivery._DeliveryContext,
        state: delivery._AttemptState,
        command: tuple[str, ...],
        timeout: float,
    ) -> delivery.DeliveryReceipt:
        del command, timeout
        state.cleanup_gate = delivery._CleanupGate.CLOSED
        state.process_detail = "direct child did not exit after SIGKILL"
        return delivery._context_receipt(ctx, delivery.DeliveryCode.COMMAND_TIMEOUT, "timed out")

    def unexpected_remove(*args: object) -> str | None:
        nonlocal remove_called
        remove_called = True
        return None

    monkeypatch.setattr(delivery, "_temporary_parent", lambda ctx: parent)
    monkeypatch.setattr(delivery, "_git", lambda *args, **kwargs: git_result())
    monkeypatch.setattr(delivery, "_worktree_registered", lambda *args: True)
    monkeypatch.setattr(delivery, "_run_and_commit", unsafe_timeout)
    monkeypatch.setattr(delivery, "_remove_worktree", unexpected_remove)

    receipt = delivery._attempt(context(tmp_path), ("unused",), 1.0)

    assert receipt.code is delivery.DeliveryCode.CLEANUP_FAILED
    assert "pending result COMMAND_TIMEOUT" in receipt.message
    assert receipt.worktree_path == str(parent / "worktree")
    assert not remove_called
    assert parent.exists()
    shutil.rmtree(parent)


def test_output_spool_oserror_is_a_named_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = delivery._AttemptState(tmp_path, tmp_path, parent_exists=False)
    monkeypatch.setattr(
        delivery.tempfile,
        "TemporaryFile",
        lambda **kwargs: (_ for _ in ()).throw(OSError("full")),
    )

    receipt = delivery._run_and_commit(context(tmp_path), state, ("unused",), 1.0)

    assert receipt.code is delivery.DeliveryCode.COMMAND_UNAVAILABLE
    assert receipt.message == "cannot create command output spool: full"
    assert state.cleanup_gate is delivery._CleanupGate.OPEN


def test_reap_direct_child_reports_kill_and_wait_failures() -> None:
    class VanishedProcess(FakeProcess):
        def kill(self) -> None:
            self.killed = True
            raise ProcessLookupError

    assert delivery._reap_direct_child(VanishedProcess()) == (True, None)

    class UnkillableProcess(FakeProcess):
        def kill(self) -> None:
            raise PermissionError("denied")

    reaped, detail = delivery._reap_direct_child(UnkillableProcess())
    assert not reaped
    assert detail == "cannot kill direct child: denied"

    class BrokenWaitProcess(FakeProcess):
        def wait(self, timeout: float | None = None) -> int:
            raise OSError("wait failed")

    assert delivery._reap_direct_child(BrokenWaitProcess()) == (
        False,
        "cannot reap direct child: wait failed",
    )

    class BrokenSecondWaitProcess(FakeProcess):
        def wait(self, timeout: float | None = None) -> int:
            self.waits += 1
            if self.waits == 1:
                assert timeout is not None
                raise subprocess.TimeoutExpired("command", timeout)
            raise OSError("second wait failed")

    assert delivery._reap_direct_child(BrokenSecondWaitProcess()) == (
        False,
        "cannot reap direct child: second wait failed",
    )


def test_teardown_waits_during_grace_and_records_probe_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess()
    signals: list[int] = []
    sleeps: list[float] = []
    probes = 0

    def killpg(pid: int, process_signal: int) -> None:
        nonlocal probes
        signals.append(process_signal)
        if process_signal == 0:
            probes += 1
            if probes == 2:
                raise PermissionError("probe denied")
            if probes == 3:
                raise ProcessLookupError

    monotonic = iter((0.0, 1.0, 2.0))
    monkeypatch.setattr(delivery.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(delivery.time, "sleep", sleeps.append)
    monkeypatch.setattr(delivery.os, "killpg", killpg)

    result = delivery._teardown_process_group(process)

    assert signals == [delivery.signal.SIGTERM, 0, 0, delivery.signal.SIGKILL, 0]
    assert sleeps == [0.05]
    assert result.cleanup_safe
    assert "probe denied" in str(result.detail)


def test_teardown_reports_group_that_survives_final_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess()
    monotonic = iter((0.0, 6.0))
    monkeypatch.setattr(delivery.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(delivery.os, "killpg", lambda pid, sig: None)

    result = delivery._teardown_process_group(process)

    assert result.group is delivery._GroupState.PRESENT
    assert not result.cleanup_safe
    assert result.detail == "process group still exists after SIGKILL"


def test_cleanup_diagnostic_never_masks_the_original_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenStderr:
        def write(self, text: str) -> int:
            raise BrokenPipeError

        def flush(self) -> None:
            raise BrokenPipeError

    monkeypatch.setattr(delivery.sys, "stderr", BrokenStderr())
    delivery._write_cleanup_diagnostic("ignored")
