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
entries (every top-level tracked file that `runner.select_carried` would not
carry -- `preserve`, `checks`, a tracked `conftest.py` at any depth, or a
tracked `INFRASTRUCTURE` file -- plus `<dir>/*` for every top-level tracked
directory except `checks`) so the model is never locked out of source by an
R1-rung request that names only a test command. A directory whose only
tracked files are test files and test-support files (`conftest.py`,
`__init__.py`, a pytest config) still counts as test-only; a directory that
also carries a real source module does not. A request naming a source file
or directory is unaffected. Only an empty repository (no tracked files)
still raises `DeriveError`.
"""

import hashlib
import re
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch

import yaml

from .contract import Contract
from .runner import select_carried

DEFAULT_TOKEN_BUDGET = 32000
DEFAULT_TURN_BUDGET = 48
DEFAULT_SELF_TEST = ("uv", "run", "python", "-m", "pytest", "-q")
_TOKEN = re.compile(r"[A-Za-z0-9_./-]+")
_PRESERVE_PATTERNS = ("tests/test_*.py", "tests/*/test_*.py", "tests/*_test.py", "tests/*/*_test.py")
_TEST_SUPPORT_BASENAMES = ("conftest.py", "__init__.py", "pytest.ini", "setup.cfg", "tox.ini")


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


def _is_test_support(path: str) -> bool:
    return path.rsplit("/", 1)[-1] in _TEST_SUPPORT_BASENAMES


def _is_test_only(chosen: tuple[str, ...], tracked: tuple[str, ...]) -> bool:
    """True when every entry in `chosen` is a test file or a test directory
    pattern -- a directory whose tracked files are all test files once test
    support files (`conftest.py`, `__init__.py`, a pytest config) are set
    aside -- including the vacuous case where `chosen` is empty, and the case
    where a directory holds nothing but support files (e.g. `tests/conftest.py`
    and `tests/__init__.py`, no `test_*.py` yet). That is the trigger for the
    top-level fallback: the request left the model nothing to write but tests.
    A directory that also carries a real source module is not test-only.
    """
    for entry in chosen:
        if entry.endswith("/*"):
            prefix = entry[:-2]
            under = [p for p in tracked if p == prefix or p.startswith(f"{prefix}/")]
            if not under:
                return False
            non_support = [p for p in under if not _is_test_support(p)]
            if not all(_is_test_file(p) for p in non_support):
                return False
        elif not _is_test_file(entry):
            return False
    return True


def _fallback_paths(tracked: tuple[str, ...], preserve: tuple[str, ...], checks: tuple[str, ...]) -> tuple[str, ...]:
    """The repository's top-level entries: every top-level tracked file that
    `select_carried` (runner.py) would not carry -- `preserve`, `checks`, a
    tracked `conftest.py` at any depth, or a tracked `INFRASTRUCTURE` file --
    plus `<dir>/*` for every top-level tracked directory except `checks`
    (test directories like `tests/*` stay in, since the model may add
    tests). `dir/*` patterns are left as-is: scope.ts refuses a carried file
    inside one regardless of what the pattern would otherwise admit. Kept to
    top-level entries only so the prompt stays small on large repositories.
    """
    carried = set(select_carried(Contract(id="", task="", preserve=preserve, checks=checks), tracked))
    top_files = sorted(p for p in tracked if "/" not in p and p not in carried)
    top_dirs = sorted({p.split("/", 1)[0] for p in tracked if "/" in p} - {"checks"})
    return tuple(sorted(top_files + [f"{d}/*" for d in top_dirs]))


_FILES_HEADER = re.compile(r"^Files:\s*$", re.MULTILINE)
_NEXT_HEADER = re.compile(r"^[A-Z][A-Za-z ]{0,40}:\s*$", re.MULTILINE)

#: The medium class, measured mechanically (design §6; plan Ruling 6). Two
#: clauses, because the design's stated predicate ("one module named in
#: `Files:`, one test module") was checked against the six self-hosted tasks
#: and refuses `selfhost-run-record-gate`, a claim task whose `Files:` block
#: names two modules, while admitting `selfhost-cell-loop`, which names one,
#: and `selfhost-speed-probe`, which names two (`scripts/speed_probe.py` and
#: `ROADMAP.md`). The field that does separate the census's tiers is the
#: declared interface: `Produces:` names 5, 2, 7, 1 and 0 symbols on the five
#: admitted tasks against 23 and 16 on the two large-tier ones (the design's
#: own prose says 24 for `selfhost-cell-loop`; the mechanical count from
#: `produces_names` on the live manifest is 23 -- most likely the design
#: counted the backticked span `` `.name` ``, a leading-dot fragment rather
#: than a dotted identifier). The file clause is kept because three or more
#: modules is above the tier on its face.
MEDIUM_MODULE_CAP = 2
MEDIUM_PRODUCES_CAP = 10
_PRODUCES = re.compile(r"^-?\s*Produces[^:]*:(.*)$", re.MULTILINE)
_SPAN = re.compile(r"`([^`]+)`")
_KEYWORD = re.compile(r"^(?:class|type|def|@dataclass)\s+")
_DOTTED = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")


def produces_names(request: str) -> tuple[str, ...]:
    """The symbols the request's ``Interfaces:`` ``Produces:`` lines name.

    Each backticked span has a leading ``class``/``type``/``def``/
    ``@dataclass`` keyword stripped, is cut at the first ``(``, space,
    ``=`` or ``:``, and is kept when what remains is a dotted identifier
    that neither ends in ``.py`` nor holds a ``/`` -- so a module path, a
    shell command or a quoted literal never counts as a produced symbol.
    Order-preserving and deduplicated.
    """
    names: list[str] = []
    for line in _PRODUCES.findall(request):
        for span in _SPAN.findall(line):
            candidate = _KEYWORD.sub("", span.strip())
            for stop in ("(", " ", "=", ":"):
                candidate = candidate.split(stop, 1)[0]
            candidate = candidate.strip()
            if not _DOTTED.match(candidate) or candidate.endswith(".py") or "/" in candidate:
                continue
            if candidate not in names:
                names.append(candidate)
    return tuple(names)


def _files_paths(request: str) -> tuple[str, ...]:
    block = files_block(request)
    if block is None:
        return ()
    found: list[str] = []
    for span in _SPAN.findall(block):
        token = span.strip().strip("`")
        if "/" not in token and "." not in token:
            continue
        if token not in found:
            found.append(token)
    return tuple(found)


def size_refusal(request: str) -> str | None:
    """The developer-facing refusal when a request is above the medium class.

    ``None`` when the request is within it. Advisory (plan Ruling 7): the
    caller still writes the contract and still runs, because §7's runaway
    resume is measured on a large-tier task, and because §6 says the Engine
    "returns the contract with a refusal", not instead of it.
    """
    modules = tuple(p for p in _files_paths(request)
                    if not _is_test_file(p) and not p.rstrip("/").endswith("tests"))
    if len(modules) > MEDIUM_MODULE_CAP:
        return (f"This request names {len(modules)} non-test paths in its Files: block "
                f"({', '.join(modules)}). The Engine is built for one or two modules and their "
                "tests; above that, 18 of 18 measured cells reached no passing state -- split "
                "the request into one bounded change per module and run them in order.")
    produced = produces_names(request)
    if len(produced) > MEDIUM_PRODUCES_CAP:
        return (f"This request's Interfaces: block declares {len(produced)} symbols "
                f"({', '.join(produced[:5])}, ...). The Engine is built for a change of up to "
                f"{MEDIUM_PRODUCES_CAP}; above that, 18 of 18 measured cells reached no passing "
                "state -- split the request into bounded changes and run them in order.")
    return None


def files_block(request: str) -> str | None:
    """The text of the request's ``Files:`` block, or ``None``.

    The block runs from the line after ``Files:`` to the next header line
    (``Word:`` alone on a line) or to the end. Design §5.3: derive must
    admit only the paths the request's ``Files:`` block names, so it cannot
    admit a path the grader rejects -- release one's run-record-gate cells
    were invited into ``errors.py``, which is outside the task's
    ``source_paths``, purely because the word appeared later in the prompt.
    A request with no ``Files:`` block (an AgentClinic repair, say) reads as
    before: the whole request is tokenized.
    """
    header = _FILES_HEADER.search(request)
    if header is None:
        return None
    rest = request[header.end():]
    following = _NEXT_HEADER.search(rest)
    return rest[: following.start()] if following else rest


def _writable_paths(request: str, tracked: tuple[str, ...], preserve: tuple[str, ...],
                    checks: tuple[str, ...]) -> tuple[str, ...]:
    files, directories = set(tracked), _directories(tracked)
    chosen: list[str] = []
    block = files_block(request)
    source = request if block is None else block
    for raw in _TOKEN.findall(source):
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
        fallback = _fallback_paths(tracked, preserve, checks)
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
                    writable_paths=_writable_paths(text, facts.tracked, preserve, checks), test_command=command,
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
