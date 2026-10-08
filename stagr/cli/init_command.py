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

from .artifact_files import ArtifactStatus, classify_artifacts, write_artifacts
from .plan_apply import PIPELINE_FAILURES, run_pipeline
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
        f"profile: {profile}\n"
        "platform:\n"
        "  type: github\n"
        "  publisher:\n"
        f"    app_id: {app_id}\n"
    )


def read_app_id(args: argparse.Namespace) -> str:
    """Return ``--app-id``, or ask for it on an interactive terminal; reject a non-numeric ID."""
    app_id = args.app_id
    if app_id is None:
        if not sys.stdin.isatty():
            raise ConfigError("the publisher App ID is required; pass --app-id <numeric ID>")
        app_id = input(APP_ID_PROMPT)
    return derive_publisher_config({"platform": {"publisher": {"app_id": app_id.strip()}}}).app_id


def cmd_init(args: argparse.Namespace) -> int:
    """`stagr init`: write `.agentic/config.yml`, then check it the way `stagr plan` does."""
    config_path = CONFIG_RELATIVE_PATH.as_posix()
    try:
        if not args.root.is_dir():
            raise ConfigError(f"project root {args.root} is not a directory")
        app_id = read_app_id(args)
        starter_artifact = RenderedArtifact(config_path, build_starter_config(args.profile, app_id))
        (config_entry,) = classify_artifacts(args.root, (starter_artifact,))
        if config_entry.status is not ArtifactStatus.NEW:
            raise ConfigError(
                f"{config_path} already exists under {args.root}; edit it, or delete it to start over"
            )
        write_artifacts(args.root, (config_entry,))
        entries = run_pipeline(args.root)
    except (EOFError, KeyboardInterrupt):
        print("\ninit: cancelled; nothing was written", file=sys.stderr)
        return 1
    except PIPELINE_FAILURES as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1
    print(f"init: wrote {config_path} under {args.root} (profile {args.profile}, publisher App {app_id})")
    print(
        f"next: run `stagr plan` to preview the {len(entries)} pipeline file(s), "
        "then `stagr apply` to write them"
    )
    return 0
