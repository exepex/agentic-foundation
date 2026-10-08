"""Tests for `stagr profile` and the profile guide `stagr init` writes."""
from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .harness import CONFIG_FILE_PATH, check, run_cli


@contextmanager
def initialized_project(profile: str = "standard") -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        project_root = Path(temporary_directory)
        run_cli(["init", "--root", str(project_root), "--app-id", "7", "--profile", profile])
        yield project_root


def read_config(project_root: Path) -> str:
    return (project_root / CONFIG_FILE_PATH).read_text(encoding="utf-8")


def test_init_writes_a_guide_naming_every_profile() -> None:
    with initialized_project() as project_root:
        config_text = read_config(project_root)
        check(
            all(f"#   {profile_name}" in config_text for profile_name in ("minimal", "standard", "custom"))
            and "stagr profile <name>" in config_text,
            "init: the config explains every profile and how to switch",
        )
        check("security (after review)" in config_text, "init: the guide describes the stages a profile expands to")


def test_profile_lists_the_profiles_and_marks_the_current_one() -> None:
    with initialized_project("minimal") as project_root:
        exit_code, stdout, _ = run_cli(["profile", "--root", str(project_root)])
        check(exit_code == 0 and "* minimal" in stdout and "  standard" in stdout, "profile: lists profiles, marks the current one")
        check(read_config(project_root).count("profile: minimal") == 1, "profile: listing changes nothing")


def test_profile_switches_only_the_profile_line() -> None:
    with initialized_project() as project_root:
        original_text = read_config(project_root)
        exit_code, stdout, _ = run_cli(["profile", "minimal", "--root", str(project_root)])
        updated_text = read_config(project_root)
        check(exit_code == 0 and "standard -> minimal" in stdout, "profile: switching exits 0 and says what changed")
        check(
            updated_text == original_text.replace("\nprofile: standard\n", "\nprofile: minimal\n"),
            "profile: only the profile line changes; the guide and other lines are kept",
        )


def test_profile_switch_then_apply_removes_the_dropped_stage() -> None:
    with initialized_project() as project_root:
        run_cli(["apply", "--root", str(project_root)])
        run_cli(["profile", "minimal", "--root", str(project_root)])
        run_cli(["apply", "--root", str(project_root)])
        check(
            not (project_root / ".github" / "workflows" / "stage-security.yml").exists()
            and (project_root / ".github" / "workflows" / "stage-review.yml").exists(),
            "profile: after switching to minimal, apply removes the security workflow and keeps review",
        )


def test_profile_rejects_a_switch_that_does_not_validate() -> None:
    with initialized_project() as project_root:
        original_text = read_config(project_root)
        exit_code, _, stderr = run_cli(["profile", "custom", "--root", str(project_root)])
        check(exit_code == 1 and "no enabled stage" in stderr, "profile: a switch that fails validation exits 1")
        check(read_config(project_root) == original_text, "profile: a failed switch restores the config")


def test_profile_refuses_a_key_spelling_it_cannot_rewrite() -> None:
    with initialized_project() as project_root:
        config_path = project_root / CONFIG_FILE_PATH
        quoted_text = read_config(project_root).replace("\nprofile: standard\n", '\n"profile": standard\n')
        config_path.write_text(quoted_text, encoding="utf-8")
        exit_code, _, stderr = run_cli(["profile", "minimal", "--root", str(project_root)])
        check(exit_code == 1 and "plain top-level" in stderr, "profile: a key it cannot rewrite exits 1, not a false success")
        check(read_config(project_root) == quoted_text, "profile: the config is restored when the switch did not take")


def test_profile_without_a_config_fails() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        exit_code, _, stderr = run_cli(["profile", "--root", temporary_directory])
        check(exit_code == 1 and "stagr init" in stderr, "profile: without a config exits 1 and points to init")


PROFILE_TESTS = (
    test_init_writes_a_guide_naming_every_profile,
    test_profile_lists_the_profiles_and_marks_the_current_one,
    test_profile_switches_only_the_profile_line,
    test_profile_switch_then_apply_removes_the_dropped_stage,
    test_profile_rejects_a_switch_that_does_not_validate,
    test_profile_refuses_a_key_spelling_it_cannot_rewrite,
    test_profile_without_a_config_fails,
)
