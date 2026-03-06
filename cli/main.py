"""Unified CLI for aorta4llm governance framework."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="aorta",
        description="aorta4llm — organizational governance for LLM agents",
    )
    sub = parser.add_subparsers(dest="command")

    from cli.cmd_validate import add_parser as add_validate
    from cli.cmd_dry_run import add_parser as add_dry_run
    from cli.cmd_init import add_parser as add_init

    add_validate(sub)
    add_dry_run(sub)
    add_init(sub)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)
