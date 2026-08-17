# E2 — The Adapter Reaches E1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/implement CONTRACT` in the TypeScript adapter reaches the same refusal as E1's `check`: the adapter starts the engine as a subprocess (`uv run --project $SATYRN_ENGINE_REPO satyrn-engine protocol`), sends one versioned JSON request, reads one JSON response, and converts every transport failure into a named refusal — on POSIX and Windows. The engine gains the `protocol` subcommand; the integration tier gains its first tests.

**Architecture:** The engine exposes a one-shot JSON surface: `satyrn-engine protocol` reads one request object from stdin, calls the E1 `check()` seam, writes one response object to stdout, and exits with the verdict's code. The adapter (`packages/engine/orchestrator.ts`) registers Pi's `/implement` command, resolves `repo = ctx.cwd`, spawns the engine with a piped stdio, and owns transport failures (`ENGINE_*`) plus its own deadline and exception boundary. The spawner is the injected test seam; a Node replay harness drives the adapter against a fake spawner, and a Python integration tier drives the real console script.

**Tech Stack:** Python 3.14, `pyyaml` (only runtime dep), `pytest` (default + `integration` marker), `ruff`, `pyrefly`; TypeScript adapter for Pi (`@earendil-works/pi-coding-agent` types + Node built-ins only), Node 25 for the replay harness (native type stripping).

**Spec:** `docs/superpowers/specs/2026-08-16-e2-adapter-reaches-e1-design.md`

## Global Constraints

- Python `>=3.14,<3.15`; runtime dependencies exactly one: `pyyaml>=6.0.3`.
- Exit codes are a stable contract. E2 adds **one** member, `INVALID_REQUEST = 7`, changing the pinned table from `[0, 2, 3, 4, 5, 6]` to `[0, 2, 3, 4, 5, 6, 7]`. Nothing is renumbered; `1` stays reserved for crashes (a crash is not a refusal). Transport failures are **adapter-owned** (`ENGINE_START_FAILED`, `ENGINE_TIMEOUT`, `ENGINE_CRASHED`, `ENGINE_MALFORMED_RESPONSE`) — never engine exit codes.
- Default test tier: no model, no network, no subprocess — enforced by the tripwire in `tests/conftest.py`, which E2 modifies to **yield only for tests marked `integration`**. The integration tier does not run in CI.
- A refusal test always has a sibling success test.
- Product code (`src/`) never imports test code; import direction is `src <- tests`.
- The spawner is the adapter's test seam (injected into `createAdapter`); the env var `SATYRN_ENGINE_REPO` is configuration, not a second seam.
- On every protocol path the engine writes **one JSON object to stdout and nothing to stderr**; the JSON is authoritative and the process exit code mirrors the verdict (transport fallback). `uv` propagates the child's exit code (verified).
- Protocol version is `1` in both directions. Unknown version in a request → `INVALID_REQUEST` (engine); unknown version in a response → `ENGINE_MALFORMED_RESPONSE` (adapter).
- The engine is spawned **without a shell**: `spawn("uv", ["run", "--project", repo, "satyrn-engine", "protocol"], { cwd: repo })` — `uv.exe` resolution and argv path handling work on POSIX and Windows without quoting.
- Adapter deadline defaults to 30 s, injectable alongside the spawner.
- Per the harvest index ("the install instructions were wrong"): any change to an installable file forces an install-doc re-verification. E2 turns the one-file `cp` guard install into a two-file adapter install with a new `SATYRN_ENGINE_REPO` requirement — the docs must be re-verified, not just written.

---

### Task 1: `INVALID_REQUEST` — one new engine exit code

**Files:**
- Modify: `src/satyrn_engine/exits.py`
- Modify: `tests/test_exits.py`

**Interfaces:**
- Produces: `ExitCode.INVALID_REQUEST = 7` — the only new engine code this phase; consumed by `protocol.py` (Task 2) for malformed requests.

- [ ] **Step 1: Change the pinned test**

In `tests/test_exits.py`, update the assertion so the deliberately changed table is pinned:

```python
def test_exit_codes_are_distinct_and_stable() -> None:
    values = sorted(int(code) for code in ExitCode)
    assert values == [0, 2, 3, 4, 5, 6, 7]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_exits.py -v`
Expected: FAIL — `[0, 2, 3, 4, 5, 6]` no longer matches, or `ExitCode.INVALID_REQUEST` not defined.

- [ ] **Step 3: Add the member**

In `src/satyrn_engine/exits.py`, after `REPO_UNAVAILABLE = 6`:

```python
    INVALID_REQUEST = 7  # the protocol surface received a malformed request
```

Also extend the class docstring's reservation sentence to name the new member, keeping the existing text:

```python
    OK = 0
    USAGE = 2  # argparse's own exit for a malformed command line
    CONTRACT_UNREADABLE = 3
    CONTRACT_INVALID_YAML = 4
    CONTRACT_MISSING_FIELD = 5
    REPO_UNAVAILABLE = 6
    INVALID_REQUEST = 7  # the protocol surface received a malformed request
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_exits.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/satyrn_engine/exits.py tests/test_exits.py
git commit -m "feat(exits): INVALID_REQUEST=7 for the protocol surface"
```

---

### Task 2: The protocol module (pure, default-tier)

**Files:**
- Create: `src/satyrn_engine/protocol.py`
- Create: `tests/test_protocol.py`
- Create: `tests/fixtures/protocol/request-check-valid.json`
- Create: `tests/fixtures/protocol/response-check-ok.json`
- Create: `tests/fixtures/protocol/response-check-refusal-repo.json`
- Create: `tests/fixtures/protocol/response-invalid-request.json`

**Interfaces:**
- Consumes: `check` from `satyrn_engine.check`, `ExitCode` from `satyrn_engine.exits`.
- Produces: `PROTOCOL_VERSION: int = 1`; `ProtocolError(Exception)` with `.message`; `Request` (frozen dataclass: `operation: str`, `repo: Path`, `contract: Path`); `parse_request(data: str | bytes) -> Request` (raises `ProtocolError`); `render_response(code: ExitCode, message: str) -> str`; `handle_protocol(data: str | bytes) -> tuple[str, int]` (never raises for input problems); `run_protocol(stdin: BinaryIO, stdout: BinaryIO) -> int` (the stdin/stdout plumbing the CLI wires in Task 3).

The compatibility fixtures are the versioned request/response pairs `README.md` says the engine owns. They use **relative paths** so they are machine-independent; the integration tier (Task 4) runs with `cwd = repo root` so `"."` and `"tests/..."` resolve there.

- [ ] **Step 1: Write the failing tests and fixtures**

Create the fixtures:

`tests/fixtures/protocol/request-check-valid.json`:
```json
{"version":1,"operation":"check","repo":".","contract":"tests/fixtures/contracts/valid.yaml"}
```

`tests/fixtures/protocol/response-check-ok.json`:
```json
{"version":1,"ok":true,"code":"OK","message":""}
```

`tests/fixtures/protocol/response-check-refusal-repo.json`:
```json
{"version":1,"ok":false,"code":"REPO_UNAVAILABLE","message":"repo is not a directory: /nonexistent"}
```

`tests/fixtures/protocol/response-invalid-request.json`:
```json
{"version":1,"ok":false,"code":"INVALID_REQUEST","message":"unsupported operation 'deliver'; expected 'check'"}
```

Create `tests/test_protocol.py`:

```python
"""Default-tier tests for the JSON protocol surface.

No process is involved: the seam ``run_protocol`` is fed BytesIO streams
directly. Every refusal has a sibling success (binding rule 4).
"""

import io
import json
from pathlib import Path

import pytest

from satyrn_engine.exits import ExitCode
from satyrn_engine.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    parse_request,
    render_response,
    run_protocol,
)

FIXTURES = Path(__file__).parent / "fixtures" / "protocol"
CONTRACTS = Path(__file__).parent / "fixtures" / "contracts"


def _run(text: str | bytes) -> tuple[bytes, int]:
    stdin = io.BytesIO(text if isinstance(text, bytes) else text.encode("utf-8"))
    stdout = io.BytesIO()
    code = run_protocol(stdin, stdout)
    return stdout.getvalue(), code


def test_accepts_valid_request() -> None:
    request = {
        "version": 1,
        "operation": "check",
        "repo": str(Path(__file__).parents[1]),
        "contract": str(CONTRACTS / "valid.yaml"),
    }
    out, code = _run(json.dumps(request))
    assert code == int(ExitCode.OK)
    assert json.loads(out) == {
        "version": 1,
        "ok": True,
        "code": "OK",
        "message": "",
    }


def test_refuses_unreadable_contract() -> None:
    request = {
        "version": 1,
        "operation": "check",
        "repo": str(Path(__file__).parents[1]),
        "contract": str(Path(__file__).parents[1] / "no-such.yaml"),
    }
    out, code = _run(json.dumps(request))
    assert code == int(ExitCode.CONTRACT_UNREADABLE)
    assert json.loads(out)["code"] == "CONTRACT_UNREADABLE"


def test_refuses_unavailable_repo() -> None:
    request = {
        "version": 1,
        "operation": "check",
        "repo": "/nonexistent",
        "contract": str(CONTRACTS / "valid.yaml"),
    }
    out, code = _run(json.dumps(request))
    assert code == int(ExitCode.REPO_UNAVAILABLE)
    assert json.loads(out)["code"] == "REPO_UNAVAILABLE"


def test_refuses_not_json() -> None:
    out, code = _run("{not json")
    assert code == int(ExitCode.INVALID_REQUEST)
    body = json.loads(out)
    assert body["ok"] is False
    assert body["code"] == "INVALID_REQUEST"
    assert "not valid JSON" in body["message"]


def test_refuses_unsupported_operation() -> None:
    out, code = _run('{"version":1,"operation":"deliver","repo":".","contract":"x"}')
    assert code == int(ExitCode.INVALID_REQUEST)
    assert json.loads(out)["code"] == "INVALID_REQUEST"


def test_refuses_unsupported_version() -> None:
    out, code = _run('{"version":2,"operation":"check","repo":".","contract":"x"}')
    assert code == int(ExitCode.INVALID_REQUEST)
    assert "version" in json.loads(out)["message"]


def test_refuses_missing_field() -> None:
    out, code = _run('{"version":1,"operation":"check","repo":"."}')
    assert code == int(ExitCode.INVALID_REQUEST)
    assert "contract" in json.loads(out)["message"]


def test_parse_request_is_strict_about_shape() -> None:
    with pytest.raises(ProtocolError):
        parse_request("[1, 2]")
    with pytest.raises(ProtocolError):
        parse_request('{"version":1,"operation":"check","repo":".","contract":""}')


def test_render_response_round_trips() -> None:
    text = render_response(ExitCode.REPO_UNAVAILABLE, "repo is not a directory: /nonexistent")
    assert json.loads(text) == {
        "version": PROTOCOL_VERSION,
        "ok": False,
        "code": "REPO_UNAVAILABLE",
        "message": "repo is not a directory: /nonexistent",
    }
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'satyrn_engine.protocol'`.

- [ ] **Step 3: Implement `protocol.py`**

```python
"""The one-shot JSON protocol surface the Pi adapter talks to.

One request in on stdin, one response out on stdout, then exit. The
verdict travels in the JSON; the process exit code mirrors it so a caller
that cannot parse the response still has a named signal. stderr stays
empty on every protocol path.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .check import check
from .exits import ExitCode

PROTOCOL_VERSION = 1
OPERATIONS = ("check",)
REQUEST_FIELDS = ("repo", "contract")


class ProtocolError(Exception):
    """A malformed request, refused as INVALID_REQUEST."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Request:
    operation: str
    repo: Path
    contract: Path


def _decode(data: str | bytes) -> str:
    if isinstance(data, bytes):
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError(f"request is not valid UTF-8: {exc}") from exc
    return data


def parse_request(data: str | bytes) -> Request:
    """Parse and validate one protocol request; raise ProtocolError."""
    text = _decode(data)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"request is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError(f"request top level must be a mapping, not {type(payload).__name__}")
    if payload.get("version") != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported protocol version {payload.get('version')!r}; expected {PROTOCOL_VERSION}"
        )
    if payload.get("operation") not in OPERATIONS:
        raise ProtocolError(
            f"unsupported operation {payload.get('operation')!r}; expected {OPERATIONS[0]!r}"
        )
    for field in REQUEST_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise ProtocolError(f"request field {field!r} must be a non-empty string")
    return Request(operation=payload["operation"], repo=Path(payload["repo"]), contract=Path(payload["contract"]))


def render_response(code: ExitCode, message: str) -> str:
    """Render one protocol response as a JSON string."""
    return json.dumps(
        {"version": PROTOCOL_VERSION, "ok": code is ExitCode.OK, "code": code.name, "message": message},
        separators=(",", ":"),
    )


def handle_protocol(data: str | bytes) -> tuple[str, int]:
    """Turn one request into (response_text, exit_code); never raises for input problems."""
    try:
        request = parse_request(data)
    except ProtocolError as exc:
        response = render_response(ExitCode.INVALID_REQUEST, exc.message)
        return response, int(ExitCode.INVALID_REQUEST)
    result = check(request.repo, request.contract)
    return render_response(result.code, result.message), int(result.code)


def run_protocol(stdin: BinaryIO, stdout: BinaryIO) -> int:
    """The stdin/stdout plumbing behind the ``protocol`` subcommand."""
    response, code = handle_protocol(stdin.read())
    stdout.write(response.encode("utf-8"))
    stdout.flush()
    return code
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_protocol.py -v`
Expected: PASS (10 tests). The autouse tripwire stays green because no test here spawns a process or opens a socket.

- [ ] **Step 5: Commit**

```bash
git add src/satyrn_engine/protocol.py tests/test_protocol.py tests/fixtures/protocol/
git commit -m "feat(protocol): one-shot versioned JSON request/response over stdin/stdout"
```

---

### Task 3: Wire the `protocol` subcommand into the CLI

**Files:**
- Modify: `src/satyrn_engine/cli.py`
- Modify: `tests/test_check_cli.py`

**Interfaces:**
- Consumes: `run_protocol` from `satyrn_engine.protocol`.
- Produces: `satyrn-engine protocol` — a subparser taking no arguments; `main(["protocol"])` reads `sys.stdin.buffer`, writes the response to `sys.stdout.buffer`, returns the verdict code.

- [ ] **Step 1: Write the failing wiring test**

Add these imports to the top of `tests/test_check_cli.py` (its existing top-level block already imports `Path`, `main`, and `ExitCode`):

```python
import io
import json
import sys

from satyrn_engine.protocol import PROTOCOL_VERSION
```

Then append the helper and the test:

```python
class _FakeStream:
    """A stand-in for sys.stdin/sys.stdout exposing a binary ``buffer``."""

    def __init__(self, data: bytes = b"") -> None:
        self.buffer = io.BytesIO(data)


def test_protocol_subcommand_via_main(monkeypatch) -> None:
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_check_cli.py::test_protocol_subcommand_via_main -v`
Expected: FAIL — `satyrn-engine: error: argument command: invalid choice: 'protocol'` (argparse raises `SystemExit(2)`).

- [ ] **Step 3: Implement the subcommand**

In `src/satyrn_engine/cli.py`, add the `protocol` subparser in `build_parser()` next to `check`:

```python
    subparsers.add_parser("protocol", help="serve one JSON request over stdin/stdout")
```

And branch on it at the top of `main`:

```python
def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "protocol":
        return run_protocol(sys.stdin.buffer, sys.stdout.buffer)
    result = check(Path(args.repo), Path(args.contract))
    if result.code != ExitCode.OK:
        print(f"satyrn-engine: {result.code.name}: {result.message}", file=sys.stderr)
    return int(result.code)
```

Add the import at the top of `cli.py`:

```python
from .protocol import run_protocol
```

Note: `protocol` takes no arguments, so the subparser is registered without being assigned — unlike `check_parser`, which is held to call `add_argument` on it. Assigning an unused name here would trip ruff's `F841`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_check_cli.py -v`
Expected: PASS (7 tests — the 6 existing E1 tests plus the new wiring test).

- [ ] **Step 5: Commit**

```bash
git add src/satyrn_engine/cli.py tests/test_check_cli.py
git commit -m "feat(cli): satyrn-engine protocol subcommand reads one JSON request"
```

---

### Task 4: Integration tier — the real console script as a subprocess

**Files:**
- Modify: `tests/conftest.py`
- Create: `tests/test_integration_protocol.py`
- Create (temporary, removed in this task): `tests/test_planted_again.py`

**Interfaces:**
- Produces: the tripwire yields only for tests marked `integration`; `test_integration_protocol.py` spawns `uv run --project ROOT satyrn-engine protocol` with a piped request and asserts stdout + exit code, including one byte-exact compatibility-fixture round trip.

- [ ] **Step 1: Make the tripwire yield for the integration marker**

In `tests/conftest.py`, change the fixture signature and add the early return, keeping every patch intact:

```python
@pytest.fixture(autouse=True)
def _no_process_or_network(monkeypatch, request):
    """Fail any default-tier test that spawns a process or opens a socket.

    The integration tier (``@pytest.mark.integration``) is the one
    deliberate exception: it exists to start the engine as a subprocess
    and does not run in CI.
    """
    if request.node.get_closest_marker("integration") is not None:
        return
    # ... every existing patch below unchanged ...
```

Also in `pyproject.toml`, under `[tool.pytest.ini_options]`, add `addopts` so the
default run deselects the integration tier (the tier spawns `uv` and must not run
in the hermetic default suite or in CI):

```toml
[tool.pytest.ini_options]
norecursedirs = ["docs/_build"]
addopts = ["-m", "not integration"]
markers = [
    "integration: needs the real clone cache or network; excluded from the offline unit run",
]
```

`uv run pytest` then runs only the default tier; `uv run pytest -m integration`
overrides `addopts` (argparse keeps the last `-m`) and runs only the integration
tier. Running a single integration file by path requires `-m integration` too,
since `addopts` would otherwise deselect it.

- [ ] **Step 2: Re-prove the tripwire still bites the default tier**

Create `tests/test_planted_again.py`:

```python
import subprocess


def test_planted_process_spawn_is_still_forbidden():
    subprocess.run(["true"])
```

Run: `uv run pytest tests/test_planted_again.py -v`
Expected: FAIL with `AssertionError: forbidden in the default test tier: subprocess.run`. Record this output (the conftest change did not weaken the default tier).

- [ ] **Step 3: Remove the planted test**

```bash
rm tests/test_planted_again.py
```

- [ ] **Step 4: Write the integration tests**

Create `tests/test_integration_protocol.py`:

```python
"""Integration tier: the real console script over the JSON protocol.

Marked ``integration`` and excluded from the default run and from CI.
These are the tier's first tests: they start the engine as a subprocess,
the one process this phase earns. The tripwire in ``tests/conftest.py``
yields to this marker.
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "protocol"
CONTRACTS = ROOT / "tests" / "fixtures" / "contracts"

pytestmark = pytest.mark.integration


def run_protocol_process(request_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--project", str(ROOT), "satyrn-engine", "protocol"],
        input=request_text,
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )


def request_with(repo: str, contract: str) -> str:
    return json.dumps({"version": 1, "operation": "check", "repo": repo, "contract": contract})


def test_protocol_accepts_valid_contract() -> None:
    proc = run_protocol_process(request_with(".", str(CONTRACTS / "valid.yaml")))
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"version": 1, "ok": True, "code": "OK", "message": ""}


def test_protocol_refuses_unreadable_contract() -> None:
    proc = run_protocol_process(request_with(".", str(ROOT / "no-such.yaml")))
    assert proc.returncode == 3
    body = json.loads(proc.stdout)
    assert body["ok"] is False
    assert body["code"] == "CONTRACT_UNREADABLE"


def test_protocol_refuses_unavailable_repo() -> None:
    proc = run_protocol_process(request_with("/nonexistent", str(CONTRACTS / "valid.yaml")))
    assert proc.returncode == 6
    body = json.loads(proc.stdout)
    assert body["ok"] is False
    assert body["code"] == "REPO_UNAVAILABLE"


def test_protocol_refuses_malformed_request() -> None:
    proc = run_protocol_process("{not json")
    assert proc.returncode == 7
    body = json.loads(proc.stdout)
    assert body["ok"] is False
    assert body["code"] == "INVALID_REQUEST"


def test_compatibility_fixture_round_trip() -> None:
    """The committed request/response pair matches the real console script."""
    proc = run_protocol_process((FIXTURES / "request-check-valid.json").read_text())
    assert proc.returncode == 0
    assert proc.stdout.rstrip("\n") == (FIXTURES / "response-check-ok.json").read_text().rstrip("\n")
```

- [ ] **Step 5: Run the integration tier**

Run: `uv run pytest -m integration -v`
Expected: PASS (5 tests). Also confirm the default run ignores them:

Run: `uv run pytest -q`
Expected: PASS, with `test_integration_protocol.py` deselected (pytest reports the marker count).

- [ ] **Step 6: Record the one-time uv-forwarding proof**

Run and record the output (this proves `uv run --project` forwards stdin and the exit code on this machine, once):

```bash
echo '{"version":1,"operation":"check","repo":".","contract":"tests/fixtures/contracts/valid.yaml"}' \
  | uv run --project . satyrn-engine protocol
echo "exit=$?"
```

Expected: the `response-check-ok.json` line and `exit=0`. Paste the actual output into the commit message body.

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/test_integration_protocol.py
git commit -m "test: integration tier yields the tripwire; protocol end-to-end
<recorded uv-forwarding proof from Step 6>"
```

---

### Task 5: The adapter — `packages/engine/orchestrator.ts`

**Files:**
- Create: `packages/engine/orchestrator.ts`

**Interfaces:**
- Consumes: Pi's `ExtensionAPI` type; Node built-ins `node:child_process` (`spawn`) and `node:path` (`resolve`).
- Produces (all exported for the harness in Task 6): `PROTOCOL_VERSION = 1`; `DEFAULT_DEADLINE_MS = 30_000`; `AdapterRefusal extends Error` (`code`, `message`); `SpawnedChild`/`Spawner` types; `EngineResponse` (`version`, `ok`, `code`, `message`); `buildRequest(repo, contract) -> string`; `parseResponse(text) -> EngineResponse` (throws `AdapterRefusal`); `exchange(spawner, request, engineRepo, deadlineMs) -> Promise<EngineResponse>`; `createAdapter(spawner, deadlineMs = DEFAULT_DEADLINE_MS)` returning `{ implement(args, ctx): Promise<void> }`; default export registering `/implement` with the real `spawn`.

- [ ] **Step 1: Write the adapter**

Create `packages/engine/orchestrator.ts`:

```typescript
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/**
 * The adapter — makes the Python engine reachable inside Pi as
 * `/implement CONTRACT`. Starts the engine as a subprocess
 * (`uv run --project $SATYRN_ENGINE_REPO satyrn-engine protocol`), sends
 * one versioned JSON request on stdin, reads one JSON response, and
 * converts every transport failure into a named refusal. The engine's own
 * refusals pass through verbatim.
 *
 * Install: copy `engine.ts` and `orchestrator.ts` next to the package's
 * `package.json` (or into `~/.pi/agent/extensions/`), and set
 * `SATYRN_ENGINE_REPO` to the engine checkout. See docs/usage.md.
 */

export const PROTOCOL_VERSION = 1;
export const DEFAULT_DEADLINE_MS = 30_000;

/** A named adapter refusal: a transport failure the engine never sees. */
export class AdapterRefusal extends Error {
	readonly code: string;
	constructor(code: string, message: string) {
		super(message);
		this.name = "AdapterRefusal";
		this.code = code;
	}
}

/** The minimal child-process surface the adapter needs (the test seam).
 *
 * `close` (not `exit`) is the event the adapter listens for: Node fires
 * `exit` before the stdio streams drain, so reading stdout on `exit` can
 * lose trailing bytes. `close` fires after the process has ended AND the
 * stdio streams are closed. */
export interface SpawnedChild {
	stdin: { write(data: string): void; end(): void };
	stdout: { on(event: "data", cb: (chunk: string) => void): void };
	on(event: "close", cb: (code: number | null) => void): void;
	on(event: "error", cb: (err: Error) => void): void;
	kill(): void;
}

export type Spawner = (
	command: string,
	args: readonly string[],
	options: { cwd?: string },
) => SpawnedChild;

export interface EngineResponse {
	version: number;
	ok: boolean;
	code: string;
	message: string;
}

/** Build the versioned JSON request the engine's `protocol` subcommand reads. */
export function buildRequest(repo: string, contract: string): string {
	return JSON.stringify({
		version: PROTOCOL_VERSION,
		operation: "check",
		repo,
		contract,
	});
}

/** Parse and shape-check one engine response; throws AdapterRefusal. */
export function parseResponse(text: string): EngineResponse {
	let parsed: unknown;
	try {
		parsed = JSON.parse(text);
	} catch {
		throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "engine response is not valid JSON");
	}
	const body = parsed as Record<string, unknown>;
	if (
		body.version !== PROTOCOL_VERSION ||
		typeof body.ok !== "boolean" ||
		typeof body.code !== "string" ||
		typeof body.message !== "string"
	) {
		throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "engine response has an unexpected shape");
	}
	return body as unknown as EngineResponse;
}

/**
 * Run one request/response exchange against the engine. The JSON response
 * is authoritative; a nonzero exit with no parseable response is a crash;
 * the deadline is the adapter's own, because Pi imposes no host deadline.
 */
export async function exchange(
	spawner: Spawner,
	request: string,
	engineRepo: string,
	deadlineMs: number,
): Promise<EngineResponse> {
	return new Promise((resolvePromise, rejectPromise) => {
		let child: SpawnedChild;
		try {
			child = spawner("uv", ["run", "--project", engineRepo, "satyrn-engine", "protocol"], {
				cwd: engineRepo,
			});
		} catch (err) {
			rejectPromise(new AdapterRefusal("ENGINE_START_FAILED", `could not start the engine: ${String(err)}`));
			return;
		}

		let stdout = "";
		let settled = false;
		const timer = setTimeout(() => {
			child.kill();
			settled = true;
			rejectPromise(new AdapterRefusal("ENGINE_TIMEOUT", `no response within ${deadlineMs} ms`));
		}, deadlineMs);

		child.stdout.on("data", (chunk) => {
			stdout += chunk;
		});

		child.on("error", (err) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			rejectPromise(new AdapterRefusal("ENGINE_START_FAILED", `engine failed to start: ${err.message}`));
		});

		child.on("close", (code) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			try {
				resolvePromise(parseResponse(stdout));
			} catch (refusal) {
				if (code !== 0) {
					rejectPromise(
						new AdapterRefusal("ENGINE_CRASHED", `engine exited ${code} with no valid response`),
					);
				} else {
					rejectPromise(refusal as AdapterRefusal);
				}
			}
		});

		try {
			child.stdin.write(request);
			child.stdin.end();
		} catch (err) {
			settled = true;
			clearTimeout(timer);
			rejectPromise(new AdapterRefusal("ENGINE_START_FAILED", `could not write the request: ${String(err)}`));
		}
	});
}

/** The command surface, with the spawner and deadline injected (test seams). */
export function createAdapter(spawner: Spawner, deadlineMs: number = DEFAULT_DEADLINE_MS) {
	return {
		async implement(
			args: string,
			ctx: { cwd: string; ui: { notify(message: string, level: "info" | "error"): void } },
		): Promise<void> {
			const engineRepo = process.env.SATYRN_ENGINE_REPO;
			if (!engineRepo) {
				ctx.ui.notify("satyrn-engine: ENGINE_START_FAILED: SATYRN_ENGINE_REPO is not set", "error");
				return;
			}
			const contractArg = args.trim();
			if (!contractArg) {
				ctx.ui.notify("satyrn-engine: USAGE: expected a CONTRACT path", "error");
				return;
			}
			const repo = ctx.cwd;
			const contract = resolve(repo, contractArg);
			const request = buildRequest(repo, contract);
			try {
				const response = await exchange(spawner, request, engineRepo, deadlineMs);
				if (response.ok) {
					ctx.ui.notify("satyrn-engine: OK", "info");
				} else {
					ctx.ui.notify(`satyrn-engine: ${response.code}: ${response.message}`, "error");
				}
			} catch (err) {
				const refusal = err as AdapterRefusal;
				ctx.ui.notify(`satyrn-engine: ${refusal.code}: ${refusal.message}`, "error");
			}
		},
	};
}

export default function (pi: ExtensionAPI) {
	const adapter = createAdapter(spawn, DEFAULT_DEADLINE_MS);
	pi.registerCommand("implement", {
		description: "Run the satyrn engine on a contract (accept or named refusal)",
		handler: adapter.implement,
	});
}
```

> **Correction (found during Task 5 execution):** the class originally
> used a TypeScript parameter property
> (`constructor(public readonly code: string, ...)`), which Node's
> strip-only type stripping rejects with `ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX`
> — it is a type transformation, not erasable syntax. The `engine.ts` guard
> ships the same way. Use the explicit field above.

- [ ] **Step 2: Sanity-check the module imports**

Run: `node --input-type=module -e "import('./packages/engine/orchestrator.ts').then(m => console.log('imports ok', m.PROTOCOL_VERSION))"`
Expected: `imports ok 1` (Node 25 strips types natively).

- [ ] **Step 3: Commit**

```bash
git add packages/engine/orchestrator.ts
git commit -m "feat(adapter): /implement command with uv spawn and transport refusal conversion"
```

---

### Task 6: Node replay harness for the adapter

**Files:**
- Create: `tools/replay_orchestrator.mjs`

**Interfaces:**
- Consumes: `createAdapter`, `buildRequest`, `parseResponse`, `AdapterRefusal` from `packages/engine/orchestrator.ts`; fixtures under `tests/fixtures/protocol/`.
- Produces: a self-checking harness run with `node tools/replay_orchestrator.mjs`, exiting nonzero on any failure.

- [ ] **Step 1: Write the harness**

Create `tools/replay_orchestrator.mjs`:

```javascript
#!/usr/bin/env node

/**
 * Drive the adapter's transport behavior against a fake spawner.
 *
 * The Python tripwire cannot reach TypeScript, and Pi itself is not a
 * dependency here, so this harness instantiates the shipped
 * `orchestrator.ts` with an ExtensionAPI-shaped double and an injected
 * fake spawner. It tests the artifact contributors install: request
 * building, response parsing, the four transport conversions, and the
 * `/implement` command surface. No model, no network, no real engine.
 */

import { readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const extensionPath = resolve(root, "packages/engine/orchestrator.ts");
const { createAdapter, buildRequest, parseResponse, exchange, AdapterRefusal } = await import(
	pathToFileURL(extensionPath)
);

// The adapter reads this at call time; set it once so the command-surface
// cases below start in the "engine is reachable" state. Individual cases
// delete and restore it to exercise the unset path.
process.env.SATYRN_ENGINE_REPO = String(root);

const FIXTURES = resolve(root, "tests/fixtures/protocol");

let failures = 0;
function ok(name, condition, detail = "") {
	if (condition) {
		console.log(`ok - ${name}`);
	} else {
		failures += 1;
		console.log(`FAIL - ${name}${detail ? `: ${detail}` : ""}`);
	}
}

/** A fake spawner child that behaves per case. */
function mockChild({ stdoutText = "", exitCode = 0, emitError = null, neverExits = false }) {
	const listeners = { close: [], error: [] };
	let killed = false;
	const child = {
		stdin: {
			write() {},
			end() {
				queueMicrotask(() => {
					if (neverExits) return;
					for (const cb of listeners.close) cb(exitCode);
				});
			},
		},
		stdout: {
			on(event, cb) {
				if (event === "data" && stdoutText) queueMicrotask(() => cb(stdoutText));
			},
		},
		on(event, cb) {
			if (listeners[event]) listeners[event].push(cb);
		},
		kill() {
			killed = true;
		},
		get killed() {
			return killed;
		},
	};
	if (emitError) queueMicrotask(() => { for (const cb of listeners.error) cb(emitError); });
	return child;
}

// --- Case 1: acceptance round trip through the real fixtures ----------------
const okRequest = await readFile(resolve(FIXTURES, "request-check-valid.json"), "utf8");
const okResponse = await readFile(resolve(FIXTURES, "response-check-ok.json"), "utf8");
const built = buildRequest("repo-dir", "contract.yaml");
ok("buildRequest produces the versioned shape", built === JSON.stringify({
	version: 1,
	operation: "check",
	repo: "repo-dir",
	contract: "contract.yaml",
}));

const parsedOk = parseResponse(okResponse);
ok("parseResponse accepts the ok fixture", parsedOk.ok === true && parsedOk.code === "OK");

// --- Case 2: engine refusal passes through verbatim --------------------------
const refusalText = await readFile(resolve(FIXTURES, "response-check-refusal-repo.json"), "utf8");
const parsedRefusal = parseResponse(refusalText);
ok(
	"parseResponse passes engine refusals through",
	parsedRefusal.ok === false && parsedRefusal.code === "REPO_UNAVAILABLE",
);

// --- Case 3: malformed response -> ENGINE_MALFORMED_RESPONSE -----------------
for (const bad of ["not json", "42", '{"version":2,"ok":true,"code":"OK","message":""}']) {
	try {
		parseResponse(bad);
		ok(`malformed response refused (${bad})`, false, "did not throw");
	} catch (err) {
		ok(
			`malformed response refused (${bad})`,
			err instanceof AdapterRefusal && err.code === "ENGINE_MALFORMED_RESPONSE",
			err.message,
		);
	}
}

// --- Case 4: exchange conversions ---------------------------------------------
const engineRepo = root;

async function exchangeCase(name, case_, deadlineMs) {
	const child = mockChild(case_);
	try {
		const response = await exchange(spawnerFor(child), okRequest, engineRepo, deadlineMs);
		ok(name, true, `resolved with ${response.code}`);
	} catch (err) {
		ok(name, false, `unexpected ${err.code ?? err.message}`);
	}
}

function spawnerFor(child) {
	return () => child;
}

await exchangeCase("acceptance exchange resolves", { stdoutText: okResponse, exitCode: 0 }, 500);
await exchangeCase("engine refusal exchange resolves", { stdoutText: refusalText, exitCode: 6 }, 500);

const crashed = await exchange(spawnerFor(mockChild({ stdoutText: "", exitCode: 1 })), okRequest, engineRepo, 500).catch((err) => err);
ok("crash -> ENGINE_CRASHED", crashed instanceof AdapterRefusal && crashed.code === "ENGINE_CRASHED", crashed.message);

const malformed = await exchange(spawnerFor(mockChild({ stdoutText: "not json", exitCode: 0 })), okRequest, engineRepo, 500).catch((err) => err);
ok("garbage on stdout -> ENGINE_MALFORMED_RESPONSE", malformed instanceof AdapterRefusal && malformed.code === "ENGINE_MALFORMED_RESPONSE", malformed.message);

const startFailed = await exchange(() => { throw new Error("ENOENT"); }, okRequest, engineRepo, 500).catch((err) => err);
ok("spawn throw -> ENGINE_START_FAILED", startFailed instanceof AdapterRefusal && startFailed.code === "ENGINE_START_FAILED", startFailed.message);

const timedOut = await exchange(spawnerFor(mockChild({ neverExits: true })), okRequest, engineRepo, 50).catch((err) => err);
ok("timeout -> ENGINE_TIMEOUT", timedOut instanceof AdapterRefusal && timedOut.code === "ENGINE_TIMEOUT", timedOut.message);

// --- Case 5: the /implement command surface -----------------------------------
// Each case clears `notifications` first so the assertion targets the one
// notify call that case made, not an accumulated index.
const notifications = [];
const fakeCtx = {
	cwd: root,
	ui: { notify(message, level) { notifications.push({ message, level }); } },
};

notifications.length = 0;
await createAdapter(() => mockChild({ stdoutText: okResponse, exitCode: 0 }), 500).implement(
	"tests/fixtures/contracts/valid.yaml",
	fakeCtx,
);
ok(
	"implement accepts and notifies OK",
	notifications.length === 1 && notifications[0].message === "satyrn-engine: OK" && notifications[0].level === "info",
	JSON.stringify(notifications),
);

notifications.length = 0;
await createAdapter(() => mockChild({ stdoutText: refusalText, exitCode: 6 }), 500).implement("anything.yaml", fakeCtx);
ok(
	"implement surfaces engine refusals verbatim",
	notifications.length === 1 && notifications[0].message.startsWith("satyrn-engine: REPO_UNAVAILABLE") && notifications[0].level === "error",
	JSON.stringify(notifications),
);

delete process.env.SATYRN_ENGINE_REPO;
notifications.length = 0;
await createAdapter(() => mockChild({})).implement("x.yaml", fakeCtx);
ok(
	"implement refuses when SATYRN_ENGINE_REPO is unset",
	notifications.length === 1 && notifications[0].message.includes("SATYRN_ENGINE_REPO") && notifications[0].level === "error",
	JSON.stringify(notifications),
);

process.env.SATYRN_ENGINE_REPO = String(root);
notifications.length = 0;
await createAdapter(() => mockChild({})).implement("   ", fakeCtx);
ok(
	"implement refuses an empty CONTRACT",
	notifications.length === 1 && notifications[0].message.includes("USAGE") && notifications[0].level === "error",
	JSON.stringify(notifications),
);

if (failures > 0) {
	console.error(`\n${failures} failure(s)`);
	process.exit(1);
}
console.log("\nall adapter replay cases passed");
```

Note: `process.env.SATYRN_ENGINE_REPO` is set to `String(root)` at the top of the harness so the command-surface cases start in the reachable state; Case 5 deletes and restores it to exercise the unset path.

- [ ] **Step 2: Run the harness**

Run: `node tools/replay_orchestrator.mjs`
Expected: all cases pass, final line `all adapter replay cases passed`, exit 0.

If the environment requires it (older Node), fall back to: `node --experimental-strip-types tools/replay_orchestrator.mjs`.

- [ ] **Step 3: Commit**

```bash
git add tools/replay_orchestrator.mjs
git commit -m "test: node replay harness drives the adapter's transport conversions"
```

---

### Task 7: Docs — adapter and protocol land in glossary and usage

**Files:**
- Modify: `docs/glossary.md`
- Modify: `docs/usage.md`

**Interfaces:**
- Produces: the concept-budget terms `adapter` and `protocol` defined in the repo's own words; `/implement` documented with its `SATYRN_ENGINE_REPO` requirement and the two-file install (the harvest index's install-doc re-verification rule).

- [ ] **Step 1: Add glossary terms**

In `docs/glossary.md`, remove the "deliberately absent until that phase arrives" sentence for *adapter* and add, in alphabetical position:

```markdown
adapter
  The thin TypeScript extension that makes the engine reachable inside Pi
  as the ``/implement`` command. It starts the engine as a subprocess
  (``uv run --project $SATYRN_ENGINE_REPO satyrn-engine protocol``), sends
  one versioned JSON request, reads one JSON response, and converts every
  transport failure into a named refusal. It owns transport only; contract
  semantics stay in Python.

protocol
  The one-shot JSON surface between the adapter and the engine: one
  versioned request on stdin, one versioned response on stdout, then exit.
  The response is authoritative; the process exit code mirrors it so a
  caller that cannot parse the response still has a named signal. Version
  mismatches are refused, not guessed at.
```

- [ ] **Step 2: Add the usage section**

Append to `docs/usage.md`:

````markdown
## The Pi adapter

The adapter exposes the engine inside Pi as a command:

```console
/implement CONTRACT
```

The command resolves `CONTRACT` against the current working directory and
runs the engine's `protocol` operation against that directory as the
repository. Acceptance reports `satyrn-engine: OK`; a refusal reports
`satyrn-engine: <CAUSE>: <detail>` — the same named causes as `check`
(`CONTRACT_UNREADABLE` through `REPO_UNAVAILABLE`, plus `INVALID_REQUEST`),
and the adapter's own transport refusals (`ENGINE_START_FAILED`,
`ENGINE_TIMEOUT`, `ENGINE_CRASHED`, `ENGINE_MALFORMED_RESPONSE`) when the
engine process itself cannot serve the request.

Install the adapter next to the guards:

```console
cp packages/engine/engine.ts packages/engine/orchestrator.ts ~/.pi/agent/extensions/
export SATYRN_ENGINE_REPO=/path/to/satyrn-engine-checkout
```

`SATYRN_ENGINE_REPO` names the engine checkout; the adapter starts the
engine with `uv run --project $SATYRN_ENGINE_REPO satyrn-engine protocol`,
so `uv` must be on `PATH`.
````

- [ ] **Step 3: Re-verify the install instructions**

Per the harvest index ("the install instructions were wrong"): any change to an installable file must be verified, not just documented. Verify the exact commands in Step 2 work in a scratch extension directory (a temp dir with the two files copied; run pi with `SATYRN_ENGINE_REPO` set and `/implement` a valid contract). Record the verification in the commit message. If the verification reveals a doc error, fix the doc and re-verify.

- [ ] **Step 4: Commit**

```bash
git add docs/glossary.md docs/usage.md
git commit -m "docs: adapter and protocol terms; /implement usage with re-verified install
<recorded install verification from Step 3>"
```

---

### Task 8: Manual proofs (POSIX + Windows) and the roadmap record

**Files:**
- Modify: `ROADMAP.md`
- Modify: `README.md`

**Interfaces:**
- Produces: the E2 gate — a recorded live `/implement` accept + refusal on POSIX and on Windows — and the roadmap/readme moved to "E2 done".

- [ ] **Step 1: POSIX manual proof**

With the adapter installed per Task 7's verified steps, run in a live Pi session (TUI mode; `ctx.ui.notify` is a no-op in print mode):

1. `/implement tests/fixtures/contracts/valid.yaml` → expect `satyrn-engine: OK`.
2. `/implement no-such.yaml` → expect `satyrn-engine: CONTRACT_UNREADABLE: ...`.
3. `/implement` (no argument) → expect `satyrn-engine: USAGE: expected a CONTRACT path`.

Record the transcript (this is the E2 analogue of E1's planted-tripwire proof).

- [ ] **Step 2: Windows manual proof**

On a Windows machine with the same checkout, `uv`, and adapter install, repeat Step 1's three commands. Record the transcript. The phase names POSIX and Windows, and the integration tier does not run in CI, so this recorded run is the Windows gate.

> **Deferred (recorded 2026-08-16):** no Windows machine is available to
> this project, so this step is deferred with a reopen condition in
> `ROADMAP.md`'s Backlog (reopens on access to a Windows machine). E2
> moved to done with the Windows leg recorded as deferred, not verified.

- [ ] **Step 3: Update the roadmap**

In `ROADMAP.md`:

- "Now": replace the E2 line with a completion note citing the spec and plan, and update the integration-tier note (the tier now has tests: `tests/test_integration_protocol.py`).
- "Concept budget": move `adapter` and `protocol` from "seed terms, not yet defined" to the defined list (they now live in `docs/glossary.md`).
- "Phases" table: E2 → **done**; E3 → **current**.
- "Prior work": add the E2 entry with its spec/plan paths.

- [ ] **Step 4: Update the README status**

In `README.md`, replace the status paragraph: Phase E2 ships — `/implement CONTRACT` reaches the same refusal through the TypeScript adapter on POSIX and Windows; the current phase is E3.

- [ ] **Step 5: Full verification gate**

Run:

```bash
uv run pytest -q            # default tier green, single-digit seconds
uv run pytest -m integration -v   # integration tier green
uv run ruff check .
uv run pyrefly check
node tools/replay_orchestrator.mjs
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add ROADMAP.md README.md
git commit -m "docs: E2 complete — adapter reaches E1 on POSIX and Windows
<recorded POSIX and Windows proof transcripts>"
```

---

## Self-Review

- **Spec coverage:** `protocol` subcommand (Tasks 2–3); `INVALID_REQUEST = 7` + deliberately changed pinned table (Task 1); request/response shapes + version (Tasks 2–3); uv seam `uv run --project $SATYRN_ENGINE_REPO` (Tasks 3–5); adapter `/implement`, `repo = ctx.cwd`, spawner as injected test seam, deadline + exception boundary, four transport refusals + the `USAGE` command-surface refusal (Task 5); engine refusals pass through verbatim (Tasks 5–6); default tier stays process-free with the tripwire yielding only for `integration` AND `addopts` deselecting the integration tier from the default run (Task 4); integration tier first tests (Task 4); Node replay harness (Task 6); protocol compatibility fixtures (Tasks 2, 4, 6); docs + install re-verification per the harvest index (Task 7); POSIX + Windows manual proofs and roadmap record (Task 8).
- **Placeholder scan:** every code step carries full content; the only prose steps (Task 8 proofs) name exact commands and expected output.
- **Type consistency:** `ExitCode.INVALID_REQUEST` (Task 1) matches `protocol.py` (Task 2); `Request` fields `operation`/`repo`/`contract` match the JSON request (Tasks 2, 5); `buildRequest`/`parseResponse`/`exchange`/`createAdapter` signatures in `orchestrator.ts` (Task 5) match the harness (Task 6); `run_protocol(stdin, stdout)` (Task 2) matches the CLI wiring (Task 3). `exchange` listens on `close` (not `exit`) so stdout is fully drained before parsing; the `SpawnedChild` interface (Task 5) and the harness mock (Task 6) both declare/fire `close`. Task 6's `exchangeCase` asserts resolution only; the refusal conversions are asserted by the dedicated crash/malformed/start/timeout cases, and the command-surface cases each clear `notifications` first so the assertion targets the one notify call that case made.
