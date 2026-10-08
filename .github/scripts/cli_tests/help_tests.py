"""Tests for `stagr help`."""
from __future__ import annotations

from .harness import check, run_cli


def test_help_lists_the_commands() -> None:
    exit_code, stdout, _ = run_cli(["help"])
    check(exit_code == 0 and "help" in stdout, "help: `stagr help` lists the available commands")
    for offered_command in ("init", "plan", "apply"):
        check(offered_command in stdout, f"help: '{offered_command}' is offered")
    check("\n    doctor" not in stdout, "help: 'doctor' is not offered yet")


def test_help_for_a_topic() -> None:
    exit_code, stdout, _ = run_cli(["help", "help"])
    check(exit_code == 0 and "topic" in stdout, "help: `stagr help help` details the command")
    exit_code, stdout, _ = run_cli(["help", "plan"])
    check(exit_code == 0 and "--root" in stdout, "help: `stagr help plan` details the command")
    exit_code, stdout, _ = run_cli(["help", "init"])
    check(exit_code == 0 and "--app-id" in stdout, "help: `stagr help init` details the command")
    exit_code, stdout, _ = run_cli(["apply", "help"])
    check(exit_code == 0 and "--root" in stdout, "help: `stagr apply help` is an alias for `stagr help apply`")


def test_unoffered_command_is_rejected() -> None:
    exit_code, _, stderr = run_cli(["doctor"])
    check(exit_code != 0 and "invalid choice" in stderr, "'doctor' exits non-zero")


def test_help_for_unknown_topic_fails() -> None:
    exit_code, _, stderr = run_cli(["help", "nonexistent"])
    check(exit_code == 1 and "unknown command" in stderr, "help: an unknown topic exits 1 and says so")


HELP_TESTS = (
    test_help_lists_the_commands,
    test_help_for_a_topic,
    test_unoffered_command_is_rejected,
    test_help_for_unknown_topic_fails,
)
