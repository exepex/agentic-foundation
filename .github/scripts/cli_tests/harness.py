"""Shared helpers for the CLI tests: result tracking, running the CLI, building throwaway projects."""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable, Iterator

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))

from stagr import cli  # noqa: E402

failures: list[str] = []

AGENTIC_DIRECTORY = Path(".agentic")
CONFIG_FILE_PATH = AGENTIC_DIRECTORY / "config.yml"

EXPECTED_DOGFOOD_WORKFLOW_PATHS = (
    ".github/workflows/stage-review.yml",
    ".github/workflows/stage-security.yml",
    ".github/workflows/routing.yml",
    ".github/workflows/governance.yml",
    ".github/workflows/resolve-outdated-threads.yml",
)


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"OK  {message}")
    else:
        failures.append(message)
        print(f"FAIL {message}", file=sys.stderr)


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            exit_code = cli.main(argv)
        except SystemExit as exit_request:
            exit_code = int(exit_request.code or 0)
    return exit_code, stdout.getvalue(), stderr.getvalue()


@contextmanager
def dogfood_project(mutate_config: Callable[[dict[str, Any]], None] | None = None) -> Iterator[Path]:
    """A temporary project root holding a copy of this repository's own `.agentic/` directory.

    ``mutate_config`` edits the parsed config before it is written, to build an invalid variant.
    """
    with tempfile.TemporaryDirectory() as temporary_directory:
        project_root = Path(temporary_directory)
        shutil.copytree(REPOSITORY_ROOT / AGENTIC_DIRECTORY, project_root / AGENTIC_DIRECTORY)
        if mutate_config is not None:
            config_path = project_root / CONFIG_FILE_PATH
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            mutate_config(config)
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        yield project_root


@contextmanager
def starter_project(config_text: str) -> Iterator[Path]:
    """A project built the way the docs tell a new user to: an empty repo and the config."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        project_root = Path(temporary_directory)
        (project_root / AGENTIC_DIRECTORY).mkdir()
        (project_root / CONFIG_FILE_PATH).write_text(config_text, encoding="utf-8")
        yield project_root


def snapshot_tree(project_root: Path) -> dict[str, tuple[int, int]]:
    """Every file under the root as {relative path: (size, modification time in ns)}."""
    return {
        path.relative_to(project_root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(project_root.rglob("*"))
        if path.is_file()
    }
