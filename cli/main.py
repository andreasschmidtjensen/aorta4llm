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
    from cli.cmd_hook import add_parser as add_hook
    from cli.cmd_status import add_parser as add_status
    from cli.cmd_reset import add_parser as add_reset
    from cli.cmd_allow_once import add_parser as add_allow_once
    from cli.cmd_explain import add_parser as add_explain

    add_validate(sub)
    add_dry_run(sub)
    add_init(sub)
    add_hook(sub)
    add_status(sub)
    add_reset(sub)
    add_allow_once(sub)
    add_explain(sub)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)
