"""The ``satyrn-engine`` command-line interface."""

import argparse
import math
import os
import signal
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import FrameType

from .attempt import MODEL_ENV, AttemptCode, SubprocessPiRunner, attempt
from .check import check
from .delivery import DEFAULT_TIMEOUT, base_is_wellformed, deliver
from .exits import ExitCode
from .protocol import run_protocol


class _DeliveryTerminationRequested(BaseException):
    """Cooperatively unwind E3 so its isolated command group is reaped."""


def _request_delivery_termination(
    signum: int,
    frame: FrameType | None,
) -> None:
    del signum, frame
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise _DeliveryTerminationRequested


@contextmanager
def _delivery_termination_guard() -> Iterator[None]:
    previous = signal.signal(signal.SIGTERM, _request_delivery_termination)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextmanager
def _attempt_termination_guard(runner: SubprocessPiRunner) -> Iterator[None]:
    """Keep SIGTERM/SIGHUP from bypassing artifact finalization.

    The temporary Python handlers forward a direct signal to Pi's separate
    process group, then return. `attempt` forwards Pi's stdout live as it is
    written (E10), and the pump keeps draining that stream into whichever
    sink still works even if one sink fails (R21), so Pi never blocks on a
    full pipe. This Engine process waits for Pi to exit, then publishes the
    patch before returning.
    """
    def request_finalization(signum: int, frame: FrameType | None) -> None:
        del frame
        runner.request_termination(signum)

    previous_term = signal.signal(signal.SIGTERM, request_finalization)
    hup = getattr(signal, "SIGHUP", None)
    try:
        previous_hup = (
            signal.signal(hup, request_finalization) if hup is not None else None
        )
    except ValueError:  # Windows does not permit every POSIX signal.
        previous_hup = None
    try:
        yield
    finally:
        if hup is not None and previous_hup is not None:
            signal.signal(hup, previous_hup)
        signal.signal(signal.SIGTERM, previous_term)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="satyrn-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="parse and validate a contract")
    check_parser.add_argument("--repo", required=True, help="working-tree root; must be a directory")
    check_parser.add_argument("contract", help="path to the contract YAML file")

    derive_parser = subparsers.add_parser("derive", help="derive a contract from a request and the repository",
                                          usage="satyrn-engine derive --repo REPO -- REQUEST...")
    derive_parser.add_argument("--repo", required=True, help="working-tree root of a Git repository")
    derive_parser.add_argument(
        "--token-budget",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "output-token limit written into the contract (default: the "
            "product default 32000). An eval record passes its own limit so "
            "the Engine has no stop the record does not name."
        ),
    )
    derive_parser.add_argument(
        "--turn-budget",
        type=_positive_int,
        default=None,
        metavar="N",
        help="turn limit written into the contract (default: the product default 48).",
    )
    derive_parser.add_argument("request", nargs="+", help="the developer's request, as words, after --")

    deliver_parser = subparsers.add_parser(
        "deliver",
        help="run one command in an isolated worktree and create a candidate",
        usage="satyrn-engine deliver --repo REPO [--timeout SECONDS] CONTRACT -- COMMAND [ARG ...]",
    )
    deliver_parser.add_argument("--repo", required=True, help="clean Git working-tree root")
    deliver_parser.add_argument(
        "--timeout",
        type=_positive_finite_timeout,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=(
            f"command timeout in seconds (default: {DEFAULT_TIMEOUT:g}). "
            "When --deadline-seconds is declared, the effective command "
            "deadline is max(timeout, deadline) so the budget deadline can "
            "fire and retain a partial candidate instead of the command "
            "timeout discarding it."
        ),
    )
    deliver_parser.add_argument(
        "--base",
        type=_nonblank_base,
        default=None,
        metavar="COMMIT_ISH",
        help=(
            "base commit-ish to branch delivery from, resolved with "
            "rev-parse --verify (default: the caller's HEAD). An external "
            "caller loops this flag across an ordered sequence of "
            "deliveries -- phase 1 from HEAD, phase N from phase N-1's own "
            "candidate commit -- to compose HP3's chained isolation without "
            "importing this package."
        ),
    )
    deliver_parser.add_argument(
        "--turn-limit",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "whole-attempt turn limit, counted from the implementer's own "
            "stream (default: the contract's turn_budget, or no limit)"
        ),
    )
    deliver_parser.add_argument(
        "--token-limit",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "whole-attempt output-token limit, counted from the implementer's "
            "own stream (default: the contract's token_budget, or no limit)"
        ),
    )
    deliver_parser.add_argument(
        "--deadline-seconds",
        type=_positive_finite_timeout,
        default=None,
        metavar="SECONDS",
        help=(
            "whole-attempt wall-clock deadline in seconds "
            "(default: the contract's deadline_seconds, or no limit). "
            "The effective command deadline is max(timeout, deadline), so a "
            "deadline longer than --timeout still fires and retains the "
            "partial candidate."
        ),
    )

    attempt_parser = subparsers.add_parser(
        "attempt",
        help="run one Pi model attempt in the current disposable worktree",
    )
    attempt_parser.add_argument("--model", help=f"Pi model string; defaults to ${MODEL_ENV}")
    attempt_parser.add_argument("contract", help="path to the contract YAML file")
    deliver_parser.add_argument("contract", help="path to the contract YAML file")
    deliver_parser.add_argument(
        "attempt_command",
        nargs=argparse.REMAINDER,
        help=argparse.SUPPRESS,
    )

    subparsers.add_parser("protocol", help="serve one JSON request over stdin/stdout")

    return parser


def _positive_finite_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a finite number greater than zero") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite number greater than zero")
    return timeout


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("turn limit must be a positive integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("turn limit must be a positive integer")
    return number


def _nonblank_base(value: str) -> str:
    """CLI-level sibling of ``delivery.base_is_wellformed``: refuse a blank
    ``--base`` at the parser, before it can reach ``deliver`` and be
    resolved against ``HEAD`` by a caller who forgot to check it -- blank is
    not absent, and mapping it to ``HEAD`` would silently base a chained
    phase on the caller's head instead of its predecessor's commit."""
    if not base_is_wellformed(value):
        raise argparse.ArgumentTypeError("base must not be blank")
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the CLI while preserving E3's literal ``--`` command boundary."""
    tokens = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(tokens)
    if args.command != "deliver":
        return args

    try:
        separator = tokens.index("--")
    except ValueError:
        parser.error("deliver requires '--' before COMMAND")
    command = tokens[separator + 1 :]
    if not command:
        parser.error("deliver requires at least one COMMAND token after '--'")
    if args.attempt_command != command:
        parser.error("deliver requires '--' before COMMAND")
    args.attempt_command = tuple(command)
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "protocol":
        return run_protocol(sys.stdin.buffer, sys.stdout.buffer)
    if args.command == "attempt":
        model = args.model or os.environ.get(MODEL_ENV)
        if not model:
            print(f"satyrn-engine: USAGE: --model or ${MODEL_ENV} is required", file=sys.stderr)
            return int(ExitCode.USAGE)
        runner = SubprocessPiRunner()
        try:
            with _attempt_termination_guard(runner):
                result = attempt(Path.cwd(), Path(args.contract), model, pi_runner=runner)
        except BrokenPipeError:
            _silence_broken_stdout()
            return 1
        if result.code is not AttemptCode.OK:
            print(f"satyrn-engine: {result.code}: {result.message}", file=sys.stderr)
        return int(result.exit_code)
    if args.command == "deliver":
        try:
            with _delivery_termination_guard():
                receipt = deliver(
                    Path(args.repo),
                    Path(args.contract),
                    args.attempt_command,
                    args.timeout,
                    base=args.base,
                    turn_limit=args.turn_limit,
                    deadline_seconds=args.deadline_seconds,
                    token_limit=args.token_limit,
                )
        except _DeliveryTerminationRequested:
            return 128 + signal.SIGTERM
        rendered = receipt.render()
        if not _write_receipt(rendered):
            return 1
        return int(receipt.exit_code)
    if args.command == "derive":
        return _derive(
            Path(args.repo),
            " ".join(args.request),
            token_budget=args.token_budget,
            turn_budget=args.turn_budget,
        )
    result = check(Path(args.repo), Path(args.contract))
    if result.code != ExitCode.OK:
        print(f"satyrn-engine: {result.code.name}: {result.message}", file=sys.stderr)
    return int(result.code)


def _derive(
    repo: Path,
    request: str,
    *,
    token_budget: int | None = None,
    turn_budget: int | None = None,
) -> int:
    from .derive import (
        DeriveError,
        RepoFacts,
        derive_contract,
        render_contract,
        size_refusal,
    )

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", os.fspath(repo), *args], check=True, capture_output=True, text=True).stdout

    try:
        tracked = tuple(line for line in git("ls-files").splitlines() if line)
        head = git("rev-parse", "--verify", "HEAD^{commit}").strip()
        git_dir = Path(git("rev-parse", "--path-format=absolute", "--git-dir").strip())
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"satyrn-engine: REPO_UNAVAILABLE: {exc}", file=sys.stderr)
        return int(ExitCode.REPO_UNAVAILABLE)
    pyproject = repo / "pyproject.toml"
    facts = RepoFacts(tracked, pyproject.read_text(encoding="utf-8") if pyproject.is_file() else "", head)
    try:
        budgets: dict[str, int] = {}
        if token_budget is not None:
            budgets["token_budget"] = token_budget
        if turn_budget is not None:
            budgets["turn_budget"] = turn_budget
        contract = derive_contract(request, facts, **budgets)
    except DeriveError as exc:
        print(f"satyrn-engine: DERIVE: {exc.message}", file=sys.stderr)
        return int(ExitCode.CONTRACT_MISSING_FIELD)
    rendered = render_contract(contract)
    target = git_dir / "satyrn" / "contracts" / f"{contract.id}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    print(f"satyrn-engine: contract {target}", file=sys.stderr)
    refusal = size_refusal(request)
    if refusal is not None:
        print(f"satyrn-engine: {refusal}", file=sys.stderr)
    return 0


def _write_receipt(rendered: str) -> bool:
    """Write one receipt, suppressing interpreter noise for a closed pipe."""
    try:
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(rendered.encode("utf-8"))
            sys.stdout.buffer.flush()
        else:
            sys.stdout.write(rendered)
            sys.stdout.flush()
    except BrokenPipeError:
        _silence_broken_stdout()
        return False
    return True


def _silence_broken_stdout() -> None:
    """Prevent Python's shutdown flush from reporting the same broken pipe."""
    try:
        with open(os.devnull, "w", encoding="utf-8") as devnull:
            os.dup2(devnull.fileno(), sys.stdout.fileno())
    except (AttributeError, OSError, ValueError):
        pass
