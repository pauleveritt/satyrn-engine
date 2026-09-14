"""The ``check`` operation: accept or refuse a contract and its target repo."""

from dataclasses import dataclass
from pathlib import Path

from .contract import Contract, ContractError, load_contract
from .exits import ExitCode


@dataclass(frozen=True)
class CheckResult:
    """The verdict of a ``check`` run."""

    code: ExitCode
    message: str = ""
    contract: Contract | None = None


def check(repo: Path, contract_path: Path) -> CheckResult:
    """Parse, validate, and path-lint, returning a named verdict.

    Order of checks is part of the stable behavior (see the E1 spec):
    contract first (read, parse, validate), then the repo path.
    """
    try:
        contract = load_contract(contract_path)
    except ContractError as exc:
        return CheckResult(code=exc.code, message=exc.message)

    if not repo.is_dir():
        return CheckResult(
            code=ExitCode.REPO_UNAVAILABLE,
            message=f"repo is not a directory: {repo}",
            contract=contract,
        )

    return CheckResult(code=ExitCode.OK, contract=contract)
