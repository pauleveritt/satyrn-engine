# Every gate, in order, stopping at the first non-zero exit. Read this
# recipe's own exit code; never pipe it into anything.
gates:
    uv run pytest -q
    uv run ruff check
    node --test --experimental-strip-types tests/test_loop_breaker.mjs tests/test_mutator.mjs tests/test_runner.mjs tests/test_runner_prompt.mjs tests/test_orchestrator.mjs tests/test_transport.mjs
    node --experimental-strip-types tools/replay_guards.mjs
    just lint-docs
    uv run python tools/provenance.py check

lint-docs:
    uv run python tools/lint_docs.py

# The marked tier: real subprocesses and Git. Not in CI.
integration:
    uv run pytest -m integration -q

# Launch pi with the package wired to this checkout (install once with
# `pi install /path/to/satyrn-engine/packages/engine`; see docs/usage.md).
pi-engine:
    SATYRN_ENGINE_REPO=$$PWD pi
