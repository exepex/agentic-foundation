"""Argument parser wiring, the `help` command, and the `main` entry point."""
from __future__ import annotations

import argparse
import sys

from .init_command import add_init_arguments, cmd_init
from .plan_apply import add_project_root_argument, cmd_apply, cmd_plan
from .profile_command import add_profile_arguments, cmd_profile


def _subparser_choices(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:  # noqa: SLF001 — argparse exposes subparsers only here
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def cmd_help(args: argparse.Namespace) -> int:
    """`stagr help` lists commands; `stagr help <command>` details one."""
    parser = build_parser()
    topic = getattr(args, "topic", None)
    if not topic:
        parser.print_help()
        return 0
    choices = _subparser_choices(parser)
    if topic in choices:
        choices[topic].print_help()
        return 0
    print(f"help: unknown command '{topic}'. Available: {', '.join(sorted(choices))}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stagr", description="stagr — the agentic-foundation control plane CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    help_parser = subparsers.add_parser("help", help="show help for all commands, or `stagr help <command>`")
    help_parser.add_argument("topic", nargs="?", help="a command name to describe in detail")
    help_parser.set_defaults(func=cmd_help)

    init_parser = subparsers.add_parser(
        "init", help="write a starter .agentic/config.yml; asks for the publisher App ID"
    )
    add_init_arguments(init_parser)
    init_parser.set_defaults(func=cmd_init)

    profile_parser = subparsers.add_parser(
        "profile", help="list the profiles, or switch the config to another one"
    )
    add_profile_arguments(profile_parser)
    profile_parser.set_defaults(func=cmd_profile)

    plan_parser = subparsers.add_parser(
        "plan", help="validate the config and list the files apply would write or remove; writes nothing"
    )
    add_project_root_argument(plan_parser)
    plan_parser.set_defaults(func=cmd_plan)

    apply_parser = subparsers.add_parser(
        "apply", help="validate the config, write the generated files, remove stale generated ones"
    )
    add_project_root_argument(apply_parser)
    apply_parser.set_defaults(func=cmd_apply)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Accept `stagr <command> help` as an alias for `stagr help <command>`.
    if len(argv) == 2 and argv[1] == "help" and argv[0] != "help":
        argv = ["help", argv[0]]
    args = build_parser().parse_args(argv)
    return args.func(args)
