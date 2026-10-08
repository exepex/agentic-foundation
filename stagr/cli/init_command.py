"""`stagr init`: write a starter `.agentic/config.yml` so a new repository can run `stagr plan`.

`init` asks for the one value Stagr cannot know, the numeric ID of the Stagr publisher GitHub App
(or takes it from `--app-id`), and writes a config for the chosen profile. Without `--force` it
never overwrites an existing config; with `--force` it regenerates the full template, keeping the
existing profile and App ID unless they are passed, and names every active setting it turned off.
It never writes through a symlink. The config it writes is checked by the same
validation `stagr plan` runs, so the config itself always validates; `init` does not read the
workflow files, so `plan` can still refuse one Stagr did not generate (see `--force`).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from stagr.core.models import ConfigError, RenderedArtifact
from stagr.core.publisher import derive_publisher_config

from .artifact_files import ArtifactStatus, classify_artifacts, write_artifacts
from .plan_apply import PIPELINE_FAILURES, validate_and_render
from .config_template import build_config_template
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
        help=f"starter stage graph (default: the existing config's profile with --force, else {DEFAULT_STARTER_PROFILE})",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="regenerate an existing config as the full template (keeps its profile and App ID)",
    )


def read_existing_config(project_root: Path) -> dict[str, Any]:
    """Return the existing config as parsed YAML, or an empty dict when there is none or it is unreadable."""
    config_file_path = project_root / CONFIG_RELATIVE_PATH
    if not config_file_path.is_file():
        return {}
    try:
        existing_config = yaml.safe_load(config_file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return existing_config if isinstance(existing_config, dict) else {}


def choose_profile(init_arguments: argparse.Namespace, existing_config: dict[str, Any]) -> str:
    """``--profile``, else the existing config's starter profile, else the default."""
    if init_arguments.profile:
        return init_arguments.profile
    existing_profile = existing_config.get("profile")
    if existing_profile in STARTER_PROFILES:
        return existing_profile
    if existing_profile is not None:
        raise ConfigError(
            f"the existing config uses profile {existing_profile!r}, which has no starter template; "
            f"pass --profile {' or '.join(STARTER_PROFILES)}"
        )
    return DEFAULT_STARTER_PROFILE


def list_settings_turned_off(existing_config: dict[str, Any], regenerated_config: dict[str, Any]) -> list[str]:
    """Return the dotted names of active settings in ``existing_config`` the regenerated one lacks."""
    turned_off: list[str] = []

    def walk(existing_section: dict[str, Any], regenerated_section: dict[str, Any], prefix: str) -> None:
        for key, value in existing_section.items():
            name = f"{prefix}{key}"
            if key not in regenerated_section:
                turned_off.append(name)
            elif isinstance(value, dict) and isinstance(regenerated_section[key], dict):
                walk(value, regenerated_section[key], f"{name}.")

    walk(existing_config, regenerated_config, "")
    return turned_off


def read_app_id(init_arguments: argparse.Namespace, existing_config: dict[str, Any]) -> str:
    """Return ``--app-id``, the existing config's App ID, or ask on a terminal; reject a non-numeric ID."""
    app_id = init_arguments.app_id
    if app_id is None:
        existing_app_id = ((existing_config.get("platform") or {}).get("publisher") or {}).get("app_id")
        app_id = None if existing_app_id is None else str(existing_app_id)
    if app_id is None:
        if not sys.stdin.isatty():
            raise ConfigError("the publisher App ID is required; pass --app-id <numeric ID>")
        app_id = input(APP_ID_PROMPT)
    return derive_publisher_config({"platform": {"publisher": {"app_id": app_id.strip()}}}).app_id


def existing_config_error(project_root: Path) -> ConfigError:
    return ConfigError(
        f"{CONFIG_RELATIVE_PATH.as_posix()} already exists under {project_root}; "
        "edit it, or regenerate the full template with `stagr init --force`"
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


def regenerate_config(project_root: Path, config_text: str) -> None:
    """Overwrite the existing config with ``config_text``; refuses a symlink like every other write."""
    config_artifact = RenderedArtifact(CONFIG_RELATIVE_PATH.as_posix(), config_text)
    write_artifacts(project_root, classify_artifacts(project_root, (config_artifact,)))


def cmd_init(init_arguments: argparse.Namespace) -> int:
    """`stagr init`: write `.agentic/config.yml`, then check it the way `stagr plan` does.

    Any failure after the write removes what `init` created, or restores the config `--force`
    replaced, so a retry starts from where the user was.
    """
    project_root = init_arguments.root
    config_path = CONFIG_RELATIVE_PATH.as_posix()
    created_paths: list[Path] = []
    replaced_text: str | None = None
    try:
        if not project_root.is_dir():
            raise ConfigError(f"project root {project_root} is not a directory")
        existing_config = read_existing_config(project_root) if init_arguments.force else {}
        profile = choose_profile(init_arguments, existing_config)
        app_id = read_app_id(init_arguments, existing_config)
        config_text = build_config_template(profile, app_id)
        (config_entry,) = classify_artifacts(project_root, (RenderedArtifact(config_path, config_text),))
        if config_entry.status is ArtifactStatus.NEW:
            create_config_exclusively(project_root, config_text, created_paths)
        elif init_arguments.force:
            replaced_text = (project_root / CONFIG_RELATIVE_PATH).read_text(encoding="utf-8")
            regenerate_config(project_root, config_text)
        else:
            raise existing_config_error(project_root)
        entries = validate_and_render(project_root)
    except (EOFError, KeyboardInterrupt):
        remove_created_paths(created_paths)
        print("\ninit: cancelled; nothing was written", file=sys.stderr)
        return 1
    except PIPELINE_FAILURES as failure:
        remove_created_paths(created_paths)
        if replaced_text is not None:
            regenerate_config(project_root, replaced_text)
        print(f"error: {failure}", file=sys.stderr)
        return 1
    action = "regenerated" if replaced_text is not None else "wrote"
    print(f"init: {action} {config_path} under {project_root} (profile {profile}, publisher App {app_id})")
    if replaced_text is not None:
        turned_off = list_settings_turned_off(existing_config, yaml.safe_load(config_text))
        if turned_off:
            print(f"init: now commented out in the template: {', '.join(turned_off)}; uncomment what you still want")
    print(
        f"next: the config produces {len(entries)} pipeline file(s); run `stagr plan` to preview them, "
        "then `stagr apply` to write them"
    )
    return 0
