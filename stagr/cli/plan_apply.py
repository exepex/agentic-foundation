"""`stagr plan` and `stagr apply`: one pipeline, two thin commands.

Both commands call ``run_pipeline`` and differ only in the last step: ``plan`` prints what would be
written, ``apply`` writes it and then prints the same list. Everything up to that step (load,
validate, render, compare with disk) is shared, so an error that stops one stops the other.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stagr.core.backend_renderer_registry import BackendRendererNotFoundError
from stagr.core.models import ConfigError

from .artifact_files import (
    ArtifactEntry,
    ArtifactWriteError,
    classify_artifacts,
    find_stale_artifacts,
    write_artifacts,
)
from .render_pipeline import PLATFORM_RENDERER_CLASSES, load_render_inputs, render_artifacts

# Every way the shared pipeline reports a bad config or an unwritable target. ValueError covers
# ConfigSyntaxError, ConfigVersionError, ConfigSchemaError, StaticValidationError and the renderers'
# own rejections.
PIPELINE_FAILURES = (
    ValueError,
    ConfigError,
    BackendRendererNotFoundError,
    ArtifactWriteError,
    OSError,
)

_HASH_DISPLAY_LENGTH = 12


def add_project_root_argument(command_parser: argparse.ArgumentParser) -> None:
    command_parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="project root: reads <root>/.agentic/config.yml and writes artifacts under <root> (default: .)",
    )


def run_pipeline(project_root: Path) -> tuple[ArtifactEntry, ...]:
    """Load, validate, render, and compare with disk. Prints warnings; writes nothing.

    Returns one entry per produced file, then one ``REMOVE`` entry per generated file the config no
    longer produces.
    """
    render_inputs = load_render_inputs(project_root)
    for warning in render_inputs.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    artifacts = render_artifacts(render_inputs)
    platform_renderer_class = PLATFORM_RENDERER_CLASSES[render_inputs.render_context.platform]
    stale_entries = find_stale_artifacts(
        project_root,
        platform_renderer_class.ARTIFACT_DIRECTORY,
        platform_renderer_class.GENERATED_FILE_HEADER,
        frozenset(artifact.path for artifact in artifacts),
    )
    return (*classify_artifacts(project_root, artifacts), *stale_entries)


def format_entries(entries: tuple[ArtifactEntry, ...]) -> str:
    return "\n".join(
        f"  {entry.status.value:<9} {entry.artifact.path}  {entry.size_in_bytes} bytes  "
        f"sha256:{entry.sha256_hex[:_HASH_DISPLAY_LENGTH]}"
        for entry in entries
    )


def cmd_plan(args: argparse.Namespace) -> int:
    """`stagr plan`: list the files the config produces, and write nothing."""
    try:
        entries = run_pipeline(args.root)
    except PIPELINE_FAILURES as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1
    print(f"plan: {len(entries)} file(s) under {args.root}; nothing was written")
    print(format_entries(entries))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """`stagr apply`: write the files `stagr plan` lists."""
    try:
        entries = run_pipeline(args.root)
        write_artifacts(args.root, entries)
    except PIPELINE_FAILURES as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1
    print(f"apply: {len(entries)} file(s) under {args.root}")
    print(format_entries(entries))
    return 0
