"""Prove the toolchain is wired up before any product code lands.

A fresh checkout should have a green `uv run pytest`, `uv run ruff check`,
and `uv run pyrefly check`. The roadmap phases define what the package
actually contains.
"""


def test_package_imports() -> None:
    import satyrn_engine

    assert satyrn_engine.__version__ == "0.1.0"
