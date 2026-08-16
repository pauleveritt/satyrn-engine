"""The ``satyrn-engine`` command-line interface."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .check import check
from .exits import ExitCode
from .protocol import run_protocol


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="satyrn-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="parse and validate a contract")
    check_parser.add_argument("--repo", required=True, help="working-tree root; must be a directory")
    check_parser.add_argument("contract", help="path to the contract YAML file")

    subparsers.add_parser("protocol", help="serve one JSON request over stdin/stdout")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "protocol":
        return run_protocol(sys.stdin.buffer, sys.stdout.buffer)
    result = check(Path(args.repo), Path(args.contract))
    if result.code != ExitCode.OK:
        print(f"satyrn-engine: {result.code.name}: {result.message}", file=sys.stderr)
    return int(result.code)
