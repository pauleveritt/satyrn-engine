"""The ``satyrn-engine`` command-line interface."""

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from .check import check
from .delivery import DEFAULT_TIMEOUT
from .exits import ExitCode
from .protocol import run_protocol


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
    if args.command == "deliver":
        raise RuntimeError("delivery execution is not wired until E3 Task 2")
    result = check(Path(args.repo), Path(args.contract))
    if result.code != ExitCode.OK:
        print(f"satyrn-engine: {result.code.name}: {result.message}", file=sys.stderr)
    return int(result.code)
