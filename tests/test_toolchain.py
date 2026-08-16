"""Prove the toolchain is wired up before any product code lands.

A fresh checkout should have a green `uv run pytest`, `uv run ruff check`,
and `uv run pyrefly check`. The roadmap phases define what the package
actually contains.
"""

import socket
import subprocess

import pytest


def test_package_imports() -> None:
    import satyrn_engine

    assert satyrn_engine.__version__ == "0.1.0"


def test_tripwire_is_armed() -> None:
    """The no-process/no-network tripwire must stay armed.

    The one-time planted proof was removed after it fired, so nothing else
    re-verifies the tripwire: if it were silently weakened, every test
    would still pass while the "zero model calls, zero processes"
    invariant went unenforced. Check one entry point per enforcement
    category — process spawning and network sockets.
    """
    with pytest.raises(AssertionError, match="forbidden in the default test tier"):
        subprocess.run(["true"])
    with pytest.raises(AssertionError, match="forbidden in the default test tier"):
        socket.socket()
