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
    from cli.cmd_watch import add_parser as add_watch
    from cli.cmd_doctor import add_parser as add_doctor
    from cli.cmd_protect import (
        add_protect_parser, add_readonly_parser,
        add_forbid_parser, add_require_parser,
    )
    from cli.cmd_norm import add_parser as add_remove_norm
    from cli.cmd_template import add_parser as add_template
    from cli.cmd_access import add_parser as add_access
    from cli.cmd_permissions import add_parser as add_permissions

    add_validate(sub)
    add_dry_run(sub)
    add_init(sub)
    add_hook(sub)
    add_status(sub)
    add_reset(sub)
    add_allow_once(sub)
    add_explain(sub)
    add_watch(sub)
    add_doctor(sub)
    add_protect_parser(sub)
    add_readonly_parser(sub)
    add_forbid_parser(sub)
    add_require_parser(sub)
    add_remove_norm(sub)
    add_template(sub)
    add_access(sub)
    add_permissions(sub)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)
