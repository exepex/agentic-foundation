"""`stagr profile`: list the profiles, or switch `.agentic/config.yml` to another one.

`stagr profile` prints every profile with the stages it expands to and marks the one the config
uses. `stagr profile <name>` rewrites only the config's top-level `profile:` line, keeping every
other line and comment, then checks the result with the same pipeline as `stagr plan`; a config
that does not validate is restored. The profile descriptions come from the profile definitions
(`describe_profile`), so the listing, the guide `stagr init` writes, and the real stage graph agree.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from stagr.core.config_parser import parse_config
from stagr.core.models import ConfigError, RenderedArtifact
from stagr.core.normalize import DEFAULT_PROFILE_NAME, PROFILE_NAMES, describe_profile

from .artifact_files import classify_artifacts, write_artifacts
from .plan_apply import PIPELINE_FAILURES, run_pipeline
from .render_pipeline import CONFIG_RELATIVE_PATH

PROFILE_LINE_PATTERN = re.compile(r"^profile:.*$", re.MULTILINE)
VERSION_LINE_PATTERN = re.compile(r"^version:.*$", re.MULTILINE)
PROFILE_NAME_WIDTH = max(len(profile_name) for profile_name in PROFILE_NAMES)


def build_profile_guide() -> str:
    """Return the comment block `stagr init` writes above the `profile:` line."""
    guide_lines = ["# Profiles. Switch with `stagr profile <name>`, then run `stagr plan` and `stagr apply`."]
    guide_lines += [
        f"#   {profile_name:<{PROFILE_NAME_WIDTH}}  {describe_profile(profile_name)}"
        for profile_name in PROFILE_NAMES
    ]
    return "\n".join(guide_lines) + "\n"


def set_profile_line(config_text: str, profile_name: str) -> str:
    """Return ``config_text`` with its top-level `profile:` line set to ``profile_name``."""
    profile_line = f"profile: {profile_name}"
    if PROFILE_LINE_PATTERN.search(config_text):
        return PROFILE_LINE_PATTERN.sub(profile_line, config_text, count=1)
    version_match = VERSION_LINE_PATTERN.search(config_text)
    if version_match:
        insert_at = version_match.end()
        return f"{config_text[:insert_at]}\n{profile_line}{config_text[insert_at:]}"
    return f"{profile_line}\n{config_text}"


def add_profile_arguments(profile_parser: argparse.ArgumentParser) -> None:
    profile_parser.add_argument(
        "profile_name",
        nargs="?",
        choices=PROFILE_NAMES,
        help="the profile to switch to; omit it to list the profiles",
    )
    profile_parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="project root: reads and updates <root>/.agentic/config.yml (default: .)",
    )


def print_profiles(current_profile: str) -> None:
    print(f"Profiles (the config uses: {current_profile})")
    for profile_name in PROFILE_NAMES:
        current_marker = "*" if profile_name == current_profile else " "
        print(f"{current_marker} {profile_name:<{PROFILE_NAME_WIDTH}}  {describe_profile(profile_name)}")
    print("Switch with `stagr profile <name>`.")


def switch_profile(project_root: Path, config_text: str, profile_name: str) -> int:
    """Write the new profile, check it like `stagr plan`, and restore the old text on failure."""
    config_path = CONFIG_RELATIVE_PATH.as_posix()
    updated_artifact = RenderedArtifact(config_path, set_profile_line(config_text, profile_name))
    write_artifacts(project_root, classify_artifacts(project_root, (updated_artifact,)))
    try:
        entries = run_pipeline(project_root)
    except PIPELINE_FAILURES:
        original_artifact = RenderedArtifact(config_path, config_text)
        write_artifacts(project_root, classify_artifacts(project_root, (original_artifact,)))
        raise
    return len(entries)


def cmd_profile(profile_arguments: argparse.Namespace) -> int:
    """`stagr profile [<name>]`: list the profiles, or switch the config to ``<name>``."""
    project_root = profile_arguments.root
    config_file_path = project_root / CONFIG_RELATIVE_PATH
    try:
        if not config_file_path.is_file():
            raise ConfigError(f"config file not found: {config_file_path}; run `stagr init` first")
        current_profile = parse_config(config_file_path).get("profile", DEFAULT_PROFILE_NAME)
        new_profile = profile_arguments.profile_name
        if new_profile is None:
            print_profiles(current_profile)
            return 0
        if new_profile == current_profile:
            print(f"profile: the config already uses {new_profile}; nothing was changed")
            return 0
        config_text = config_file_path.read_text(encoding="utf-8")
        try:
            entry_count = switch_profile(project_root, config_text, new_profile)
        except PIPELINE_FAILURES as failure:
            raise ConfigError(f"{failure}; the config was left on profile {current_profile}") from failure
    except PIPELINE_FAILURES as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1
    print(f"profile: {current_profile} -> {new_profile} in {CONFIG_RELATIVE_PATH.as_posix()}")
    print(f"next: run `stagr plan` to preview the {entry_count} pipeline file(s), then `stagr apply`")
    return 0
