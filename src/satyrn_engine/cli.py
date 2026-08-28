"""The ``satyrn-engine`` command-line interface."""

import argparse
import math
import os
import signal
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import FrameType

from .attempt import MODEL_ENV, AttemptCode, attempt
from .check import check
from .delivery import DEFAULT_TIMEOUT, deliver
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="satyrn-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="parse and validate a contract")
    check_parser.add_argument("--repo", required=True, help="working-tree root; must be a directory")
    check_parser.add_argument("contract", help="path to the contract YAML file")

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
        help=f"command timeout in seconds (default: {DEFAULT_TIMEOUT:g})",
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
        try:
            result = attempt(Path.cwd(), Path(args.contract), model)
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
                )
        except _DeliveryTerminationRequested:
            return 128 + signal.SIGTERM
        rendered = receipt.render()
        if not _write_receipt(rendered):
            return 1
        return int(receipt.exit_code)
    result = check(Path(args.repo), Path(args.contract))
    if result.code != ExitCode.OK:
        print(f"satyrn-engine: {result.code.name}: {result.message}", file=sys.stderr)
    return int(result.code)


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
