"""Tests for `stagr init`, which writes the starter config `stagr plan` then reads."""
from __future__ import annotations

import builtins
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .harness import AGENTIC_DIRECTORY, CONFIG_FILE_PATH, check, run_cli

PUBLISHER_APP_ID = "5239405"


@contextmanager
def empty_project() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        yield Path(temporary_directory)


@contextmanager
def interactive_terminal(typed_answer: str) -> Iterator[None]:
    """Pretend stdin is a terminal on which the user types ``typed_answer``."""
    original_isatty, original_input = sys.stdin.isatty, builtins.input
    sys.stdin.isatty = lambda: True  # type: ignore[method-assign]
    builtins.input = lambda prompt="": typed_answer
    try:
        yield
    finally:
        sys.stdin.isatty = original_isatty  # type: ignore[method-assign]
        builtins.input = original_input


def test_init_writes_a_config_that_plan_accepts() -> None:
    with empty_project() as project_root:
        exit_code, stdout, _ = run_cli(["init", "--root", str(project_root), "--app-id", PUBLISHER_APP_ID])
        config_text = (project_root / CONFIG_FILE_PATH).read_text(encoding="utf-8")
        check(exit_code == 0 and "wrote .agentic/config.yml" in stdout, "init: writes .agentic/config.yml")
        check("profile: standard" in config_text, "init: the default profile is standard")
        check(f"app_id: {PUBLISHER_APP_ID}" in config_text, "init: the config carries the App ID")
        check("stagr plan" in stdout, "init: tells the user to run `stagr plan` next")
        plan_exit_code, plan_stdout, _ = run_cli(["plan", "--root", str(project_root)])
        check(plan_exit_code == 0 and "4 file(s)" in plan_stdout, "init: `stagr plan` accepts the standard starter config")


def test_init_minimal_profile() -> None:
    with empty_project() as project_root:
        exit_code, _, _ = run_cli(
            ["init", "--root", str(project_root), "--app-id", PUBLISHER_APP_ID, "--profile", "minimal"]
        )
        plan_exit_code, plan_stdout, _ = run_cli(["plan", "--root", str(project_root)])
        check(exit_code == 0 and plan_exit_code == 0 and "3 file(s)" in plan_stdout, "init: the minimal profile plans 3 files")


def test_init_asks_for_the_app_id_on_a_terminal() -> None:
    with empty_project() as project_root:
        with interactive_terminal(f" {PUBLISHER_APP_ID} "):
            exit_code, _, _ = run_cli(["init", "--root", str(project_root)])
        config_text = (project_root / CONFIG_FILE_PATH).read_text(encoding="utf-8")
        check(
            exit_code == 0 and f"app_id: {PUBLISHER_APP_ID}" in config_text,
            "init: asks for the App ID when --app-id is omitted on a terminal",
        )


def test_init_without_app_id_off_a_terminal_fails() -> None:
    with empty_project() as project_root:
        original_isatty = sys.stdin.isatty
        sys.stdin.isatty = lambda: False  # type: ignore[method-assign]
        try:
            exit_code, _, stderr = run_cli(["init", "--root", str(project_root)])
        finally:
            sys.stdin.isatty = original_isatty  # type: ignore[method-assign]
        check(exit_code == 1 and "--app-id" in stderr, "init: without a terminal, a missing App ID exits 1")
        check(not (project_root / AGENTIC_DIRECTORY).exists(), "init: a missing App ID writes nothing")


def test_init_rejects_an_invalid_app_id() -> None:
    with empty_project() as project_root:
        exit_code, _, stderr = run_cli(["init", "--root", str(project_root), "--app-id", "not-a-number"])
        check(exit_code == 1 and "invalid" in stderr, "init: a non-numeric App ID exits 1")
        check(not (project_root / AGENTIC_DIRECTORY).exists(), "init: an invalid App ID writes nothing")


def test_init_never_overwrites_an_existing_config() -> None:
    with empty_project() as project_root:
        (project_root / AGENTIC_DIRECTORY).mkdir()
        (project_root / CONFIG_FILE_PATH).write_text("version: 2\n", encoding="utf-8")
        exit_code, _, stderr = run_cli(["init", "--root", str(project_root), "--app-id", PUBLISHER_APP_ID])
        kept_text = (project_root / CONFIG_FILE_PATH).read_text(encoding="utf-8")
        check(exit_code == 1 and "already exists" in stderr, "init: an existing config exits 1")
        check(kept_text == "version: 2\n", "init: an existing config is left untouched")


def test_init_refuses_a_symlinked_config_directory() -> None:
    with empty_project() as project_root, empty_project() as outside_directory:
        os.symlink(outside_directory, project_root / AGENTIC_DIRECTORY)
        exit_code, _, stderr = run_cli(["init", "--root", str(project_root), "--app-id", PUBLISHER_APP_ID])
        check(exit_code == 1 and "symlink" in stderr, "init: a symlinked .agentic directory exits 1")
        check(not any(outside_directory.iterdir()), "init: nothing is written through the symlink")


def test_init_requires_an_existing_root() -> None:
    with empty_project() as project_root:
        missing_root = project_root / "missing"
        exit_code, _, stderr = run_cli(["init", "--root", str(missing_root), "--app-id", PUBLISHER_APP_ID])
        check(exit_code == 1 and "not a directory" in stderr, "init: a missing project root exits 1")
        check(not missing_root.exists(), "init: a missing project root is not created")


def make_review_workflow_a_symlink(project_root: Path, link_target: Path) -> None:
    """Make a target `stagr plan` checks unsafe, so the check after the write fails."""
    workflows_directory = project_root / ".github" / "workflows"
    workflows_directory.mkdir(parents=True)
    os.symlink(link_target, workflows_directory / "stage-review.yml")


def test_init_removes_its_config_when_the_check_fails() -> None:
    with empty_project() as project_root, empty_project() as outside_directory:
        make_review_workflow_a_symlink(project_root, outside_directory / "target.yml")
        exit_code, _, stderr = run_cli(["init", "--root", str(project_root), "--app-id", PUBLISHER_APP_ID])
        check(exit_code == 1 and "symlink" in stderr, "init: a failing check after the write exits 1")
        check(
            not (project_root / AGENTIC_DIRECTORY).exists(),
            "init: a failing check removes the config and the .agentic directory init created",
        )


def test_init_keeps_an_existing_agentic_directory_on_failure() -> None:
    with empty_project() as project_root, empty_project() as outside_directory:
        (project_root / AGENTIC_DIRECTORY).mkdir()
        (project_root / AGENTIC_DIRECTORY / "notes.txt").write_text("keep", encoding="utf-8")
        make_review_workflow_a_symlink(project_root, outside_directory / "target.yml")
        exit_code, _, _ = run_cli(["init", "--root", str(project_root), "--app-id", PUBLISHER_APP_ID])
        check(
            exit_code == 1
            and not (project_root / CONFIG_FILE_PATH).exists()
            and (project_root / AGENTIC_DIRECTORY / "notes.txt").exists(),
            "init: a failing check removes only the config, not an .agentic directory that existed",
        )


def test_init_never_replaces_a_config_written_after_its_check() -> None:
    """A config that appears between init's existence check and its write is kept (exclusive create)."""
    from stagr.cli import init_command

    original_classify = init_command.classify_artifacts

    def classify_then_race(project_root, artifacts):  # another process writes the config first
        entries = original_classify(project_root, artifacts)
        (project_root / AGENTIC_DIRECTORY).mkdir(exist_ok=True)
        (project_root / CONFIG_FILE_PATH).write_text("version: 2\n", encoding="utf-8")
        return entries

    with empty_project() as project_root:
        init_command.classify_artifacts = classify_then_race
        try:
            exit_code, _, stderr = run_cli(["init", "--root", str(project_root), "--app-id", PUBLISHER_APP_ID])
        finally:
            init_command.classify_artifacts = original_classify
        kept_text = (project_root / CONFIG_FILE_PATH).read_text(encoding="utf-8")
        check(exit_code == 1 and "already exists" in stderr, "init: a config written concurrently exits 1")
        check(kept_text == "version: 2\n", "init: a config written concurrently is neither replaced nor removed")


INIT_TESTS = (
    test_init_writes_a_config_that_plan_accepts,
    test_init_minimal_profile,
    test_init_asks_for_the_app_id_on_a_terminal,
    test_init_without_app_id_off_a_terminal_fails,
    test_init_rejects_an_invalid_app_id,
    test_init_never_overwrites_an_existing_config,
    test_init_refuses_a_symlinked_config_directory,
    test_init_requires_an_existing_root,
    test_init_removes_its_config_when_the_check_fails,
    test_init_keeps_an_existing_agentic_directory_on_failure,
    test_init_never_replaces_a_config_written_after_its_check,
)
