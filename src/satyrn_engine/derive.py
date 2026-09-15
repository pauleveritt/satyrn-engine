"""Derive a contract from one request and the facts of one repository.

Deterministic and pure: the request is the only free input; every other
value is read from the tracked file list, the pyproject text and HEAD the
CLI hands in. The developer confirms the result; nobody hand-writes it.

Two rules restate HP1's builder in satyrn-evals (engine_contract.py:33-84
and :86-94 at 00c3c18): a directory renders as `dir/*`, and admission is
fnmatch with `*` spanning `/`. Nothing is imported from that tree.

When the request's tokens yield no writable path other than test files or
test directories -- naming nothing, naming only a preserved test, or naming
only tests/ -- `writable_paths` falls back to the repository's top-level
entries (every top-level tracked file not preserved and not under `checks/`,
plus `<dir>/*` for every top-level tracked directory except `checks`) so the
model is never locked out of source by an R1-rung request that names only a
test command. A request naming a source file or directory is unaffected.
Only an empty repository (no tracked files) still raises `DeriveError`.
"""

import hashlib
import re
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch

import yaml

from .contract import Contract

DEFAULT_TOKEN_BUDGET = 32000
DEFAULT_TURN_BUDGET = 48
DEFAULT_SELF_TEST = ("uv", "run", "python", "-m", "pytest", "-q")
_TOKEN = re.compile(r"[A-Za-z0-9_./-]+")
_PRESERVE_PATTERNS = ("tests/test_*.py", "tests/*/test_*.py", "tests/*_test.py", "tests/*/*_test.py")


class DeriveError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class RepoFacts:
    tracked: tuple[str, ...]
    pyproject: str
    head: str


def contract_id(request: str, head: str) -> str:
    digest = hashlib.sha256(f"{head}\n{request.strip()}".encode()).hexdigest()
    return f"implement-{digest[:12]}"


def self_test_command(pyproject: str) -> tuple[str, ...]:
    if not pyproject.strip():
        raise DeriveError("no pyproject.toml; declare [tool.satyrn] self_test or add one")
    try:
        declared = tomllib.loads(pyproject).get("tool", {}).get("satyrn", {}).get("self_test")
    except tomllib.TOMLDecodeError as exc:
        raise DeriveError(f"pyproject.toml is not valid TOML: {exc}") from exc
    if declared is None:
        return DEFAULT_SELF_TEST
    if not isinstance(declared, list) or not declared or not all(isinstance(t, str) and t for t in declared):
        raise DeriveError("[tool.satyrn] self_test must be a non-empty list of strings")
    return tuple(declared)


def _directories(tracked: tuple[str, ...]) -> set[str]:
    found: set[str] = set()
    for path in tracked:
        parts = path.split("/")
        found.update("/".join(parts[:depth]) for depth in range(1, len(parts)))
    return found


def _test_for(module: str, tracked: tuple[str, ...]) -> str:
    stem = module.rsplit("/", 1)[-1][:-3]
    return next((p for p in tracked if p.endswith(f"/test_{stem}.py")), f"tests/test_{stem}.py")


def _is_test_file(token: str) -> bool:
    basename = token.rsplit("/", 1)[-1]
    return basename.startswith("test_") or basename.endswith("_test.py")


def _is_test_only(chosen: tuple[str, ...], tracked: tuple[str, ...]) -> bool:
    """True when every entry in `chosen` is a test file or a test directory
    pattern (a directory whose tracked files are all test files) -- including
    the vacuous case where `chosen` is empty. That is the trigger for the
    top-level fallback: the request left the model nothing to write but
    tests.
    """
    for entry in chosen:
        if entry.endswith("/*"):
            prefix = entry[:-2]
            under = [p for p in tracked if p == prefix or p.startswith(f"{prefix}/")]
            if not under or not all(_is_test_file(p) for p in under):
                return False
        elif not _is_test_file(entry):
            return False
    return True


def _fallback_paths(tracked: tuple[str, ...], preserve: tuple[str, ...]) -> tuple[str, ...]:
    """The repository's top-level entries: every top-level tracked file not
    preserved and not under `checks/`, plus `<dir>/*` for every top-level
    tracked directory except `checks` (test directories like `tests/*` stay
    in, since the model may add tests). Kept to top-level entries only so the
    prompt stays small on large repositories.
    """
    top_files = sorted(p for p in tracked if "/" not in p and p not in preserve)
    top_dirs = sorted({p.split("/", 1)[0] for p in tracked if "/" in p} - {"checks"})
    return tuple(sorted(top_files + [f"{d}/*" for d in top_dirs]))


def _writable_paths(request: str, tracked: tuple[str, ...], preserve: tuple[str, ...]) -> tuple[str, ...]:
    files, directories = set(tracked), _directories(tracked)
    chosen: list[str] = []
    for raw in _TOKEN.findall(request):
        token = raw if raw in files else raw.strip("./").rstrip("/")
        candidates: list[str] = []
        if token in files:
            if token not in preserve:
                candidates.append(token)
        elif token in directories:
            candidates.append(f"{token}/*")
        elif "/" in token and token.rsplit("/", 1)[0] in directories and token not in preserve:
            candidates.append(token)                       # a file the task will create
        if candidates and token.endswith(".py") and not _is_test_file(token):
            test = _test_for(token, tracked)
            if test not in preserve:
                candidates.append(test)
        chosen.extend(c for c in candidates if c not in chosen)
    if _is_test_only(tuple(chosen), tracked):
        # The request named no source path -- either nothing at all, only a
        # preserved test, or only test files/directories. Fall back to the
        # repository's top-level entries so the model can still reach source.
        fallback = _fallback_paths(tracked, preserve)
        if not fallback:
            raise DeriveError("name at least one tracked file, tracked directory, or new file under a tracked directory in the request")
        return fallback
    return tuple(chosen)


def derive_contract(request: str, facts: RepoFacts, *, token_budget: int = DEFAULT_TOKEN_BUDGET,
                    turn_budget: int = DEFAULT_TURN_BUDGET) -> Contract:
    text = request.strip()
    if not text:
        raise DeriveError("the request is empty")
    command = self_test_command(facts.pyproject)
    preserve = tuple(sorted(p for p in facts.tracked if any(fnmatch(p, pat) for pat in _PRESERVE_PATTERNS)))
    checks = tuple(sorted(p for p in facts.tracked if p.startswith("checks/")))
    return Contract(id=contract_id(text, facts.head), task=text,
                    writable_paths=_writable_paths(text, facts.tracked, preserve), test_command=command,
                    turn_budget=turn_budget, preserve=preserve, checks=checks, token_budget=token_budget)


def render_contract(contract: Contract) -> str:
    body = {
        "id": contract.id, "task": contract.task,
        "writable_paths": list(contract.writable_paths), "test_command": list(contract.test_command),
        "preserve": list(contract.preserve), "checks": list(contract.checks),
        "token_budget": contract.token_budget, "turn_budget": contract.turn_budget,
        "deadline_seconds": contract.deadline_seconds,
    }
    return yaml.safe_dump({k: v for k, v in body.items() if v is not None}, sort_keys=False, allow_unicode=True)
