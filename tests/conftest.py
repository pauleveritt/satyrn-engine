"""Test-root fixtures.

The autouse tripwire below enforces binding rule 3 mechanically: the
default tier must not spawn a process or open a network connection. Any
test — or product code it calls — that does either is failed immediately,
before it can produce a green that silently depends on a model or a shell.
"""

import os
import socket
import subprocess
from collections.abc import Callable
from typing import NoReturn

import pytest


def _forbid(label: str) -> Callable[..., NoReturn]:
    def _explode(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError(f"forbidden in the default test tier: {label}")

    return _explode


@pytest.fixture(autouse=True)
def _no_process_or_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Fail any default-tier test that spawns a process or opens a socket.

    The integration tier (``@pytest.mark.integration``) is the one
    deliberate exception: it exists to start the engine as a subprocess
    and does not run in CI.
    """
    if request.node.get_closest_marker("integration") is not None:
        return
    # subprocess.run/call/check_* route through Popen, but patching the
    # entry points too keeps the failure message specific to the call made.
    monkeypatch.setattr(subprocess, "Popen", _forbid("subprocess.Popen"))
    for name in ("run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"):
        if hasattr(subprocess, name):
            monkeypatch.setattr(subprocess, name, _forbid(f"subprocess.{name}"))
    for name in ("system", "popen", "popen2", "popen3", "popen4"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _forbid(f"os.{name}"))
    for name in (
        "fork", "forkpty", "posix_spawn", "posix_spawnp",
        "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
        "execl", "execle", "execlp", "execlpe",
        "execv", "execve", "execvp", "execvpe",
    ):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _forbid(f"os.{name}"))
    monkeypatch.setattr(socket, "socket", _forbid("socket.socket"))
