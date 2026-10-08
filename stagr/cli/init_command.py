"""`stagr init`: write a starter `.agentic/config.yml` so a new repository can run `stagr plan`.

`init` asks for the one value Stagr cannot know, the numeric ID of the Stagr publisher GitHub App
(or takes it from `--app-id`), and writes a config for the chosen profile. It never overwrites an
existing config and never writes through a symlink. The config it writes is checked by the same
pipeline `stagr plan` runs, so `init` followed by `plan` always validates.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stagr.core.models import ConfigError, RenderedArtifact
from stagr.core.publisher import derive_publisher_config

from .artifact_files import ArtifactStatus, classify_artifacts
from .plan_apply import PIPELINE_FAILURES, run_pipeline
from .profile_command import build_profile_guide
from .render_pipeline import CONFIG_RELATIVE_PATH

STARTER_PROFILES = ("minimal", "standard")
DEFAULT_STARTER_PROFILE = "standard"
APP_ID_PROMPT = "Numeric ID of your Stagr publisher GitHub App: "


def add_init_arguments(init_parser: argparse.ArgumentParser) -> None:
    init_parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="project root: writes <root>/.agentic/config.yml (default: .)",
    )
    init_parser.add_argument(
        "--app-id",
        help="numeric ID of the Stagr publisher GitHub App; asked for when omitted",
    )
    init_parser.add_argument(
        "--profile",
        choices=STARTER_PROFILES,
        default=DEFAULT_STARTER_PROFILE,
        help=f"starter stage graph (default: {DEFAULT_STARTER_PROFILE})",
    )


def build_starter_config(profile: str, app_id: str) -> str:
    """Return the starter config text for ``profile`` and the publisher App ``app_id``."""
    return (
        "version: 2\n"
        f"{build_profile_guide()}"
        f"profile: {profile}\n"
        "platform:\n"
        "  type: github\n"
        "  publisher:\n"
        f"    app_id: {app_id}\n"
    )


def read_app_id(init_arguments: argparse.Namespace) -> str:
    """Return ``--app-id``, or ask for it on an interactive terminal; reject a non-numeric ID."""
    app_id = init_arguments.app_id
    if app_id is None:
        if not sys.stdin.isatty():
            raise ConfigError("the publisher App ID is required; pass --app-id <numeric ID>")
        app_id = input(APP_ID_PROMPT)
    return derive_publisher_config({"platform": {"publisher": {"app_id": app_id.strip()}}}).app_id


def existing_config_error(project_root: Path) -> ConfigError:
    return ConfigError(
        f"{CONFIG_RELATIVE_PATH.as_posix()} already exists under {project_root}; "
        "edit it, or delete it to start over"
    )


def create_config_exclusively(project_root: Path, config_text: str, created_paths: list[Path]) -> None:
    """Create the config only if no file exists at its path, recording each path this call creates.

    The exclusive open ("x") refuses an existing file, so a config another process wrote first is
    never replaced, and ``created_paths`` names only what this invocation owns.
    """
    config_directory = project_root / CONFIG_RELATIVE_PATH.parent
    if not config_directory.exists():
        config_directory.mkdir(exist_ok=True)
        created_paths.append(config_directory)
    config_file_path = project_root / CONFIG_RELATIVE_PATH
    try:
        with open(config_file_path, "x", encoding="utf-8") as config_file:
            created_paths.append(config_file_path)
            config_file.write(config_text)
    except FileExistsError as error:
        raise existing_config_error(project_root) from error


def remove_created_paths(created_paths: list[Path]) -> None:
    """Remove the config file and, if `init` created it, its now-empty directory (newest first)."""
    for created_path in reversed(created_paths):
        if created_path.is_dir() and not created_path.is_symlink():
            if not any(created_path.iterdir()):
                created_path.rmdir()
        else:
            created_path.unlink(missing_ok=True)


def cmd_init(init_arguments: argparse.Namespace) -> int:
    """`stagr init`: write `.agentic/config.yml`, then check it the way `stagr plan` does.

    Any failure after the write removes what `init` created, so a retry starts from a clean root.
    """
    project_root = init_arguments.root
    config_path = CONFIG_RELATIVE_PATH.as_posix()
    created_paths: list[Path] = []
    try:
        if not project_root.is_dir():
            raise ConfigError(f"project root {project_root} is not a directory")
        app_id = read_app_id(init_arguments)
        starter_artifact = RenderedArtifact(
            config_path, build_starter_config(init_arguments.profile, app_id)
        )
        (config_entry,) = classify_artifacts(project_root, (starter_artifact,))
        if config_entry.status is not ArtifactStatus.NEW:
            raise existing_config_error(project_root)
        create_config_exclusively(project_root, starter_artifact.content, created_paths)
        entries = run_pipeline(project_root)
    except (EOFError, KeyboardInterrupt):
        remove_created_paths(created_paths)
        print("\ninit: cancelled; nothing was written", file=sys.stderr)
        return 1
    except PIPELINE_FAILURES as failure:
        remove_created_paths(created_paths)
        print(f"error: {failure}", file=sys.stderr)
        return 1
    print(
        f"init: wrote {config_path} under {project_root} "
        f"(profile {init_arguments.profile}, publisher App {app_id})"
    )
    print(
        f"next: run `stagr plan` to preview the {len(entries)} pipeline file(s), "
        "then `stagr apply` to write them"
    )
    return 0
