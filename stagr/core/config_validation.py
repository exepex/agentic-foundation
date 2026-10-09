"""The config front door: everything that can be checked about a config without rendering.

``validate_config`` runs, in order:

1. the JSON Schema (``stagr/config.schema.json``);
2. ``platform.publisher`` derivation (App id and secret NAME), when the block is present;
3. V-S16 (the profile's stages stay enabled and blocking), before normalization so a disabled
   required stage is reported as such;
4. normalization: profile (V-S01), dependency references (V-S05), acyclicity (V-S04) and
   provider resolution;
5. V-S06 (skill files exist), when a ``project_root`` is given.

It has no side effects and never reads a secret value. The checks that need the renderers
(V-S07 to V-S09) live in ``static_validator.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .errors import ConfigSchemaError
from .pipeline import expand_active_stages, normalize_config
from .profile_requirements import validate_profile_stage_requirements
from .publisher import derive_publisher_config
from .skill_validator import validate_skill_file_existence

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "config.schema.json"


def describe_schema_error(error: Any) -> str:
    """Return a schema error's text, WITHOUT the rejected value when it sits in a secret-name field.

    jsonschema messages quote the offending instance ("'<value>' does not match ..."). A field that
    holds a secret NAME (any value inside the ``secrets`` block) is exactly where an
    operator may paste the secret VALUE by mistake (for example a private key), and that value must
    never reach CLI or CI logs.
    """
    path_parts = [str(part) for part in error.path]
    # A value under the `secrets` block, not an error about the block itself (an unknown key).
    if "secrets" in path_parts[:-1]:
        return "is not a valid secret NAME (value withheld; store the value as a CI secret and put only its name here)"
    return error.message


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_config_schema(config: dict[str, Any]) -> None:
    """Raise ``ConfigSchemaError`` listing every schema violation in ``config``."""
    schema_errors = sorted(
        Draft202012Validator(load_schema()).iter_errors(config),
        key=lambda error: list(error.path),
    )
    if not schema_errors:
        return
    violations = "; ".join(
        f"{'/'.join(str(part) for part in error.path) or '(root)'}: {describe_schema_error(error)}"
        for error in schema_errors
    )
    raise ConfigSchemaError(f"config does not conform to schema: {violations}")


def validate_config(config: dict[str, Any], project_root: Path | None = None) -> None:
    """Validate ``config``; raise on the first failing step.

    Raises:
        ConfigSchemaError: the config does not conform to the schema.
        ConfigError: ``platform.publisher`` is invalid, or a stage's provider or backend
            cannot be resolved.
        StaticValidationError: a dependency reference is unknown (V-S05), the graph has a
            cycle (V-S04), a stage the profile requires is off or not blocking (V-S16), or a skill
            file is missing (V-S06).
        ValueError: the profile is unrecognised (V-S01), or a stage has no id or a duplicate id.

    ``project_root`` is the directory that contains ``.agentic/``. When it is ``None`` the
    file-system check (V-S06) is skipped, which suits in-memory configs in unit tests.
    """
    validate_config_schema(config)
    if "publisher" in (config.get("platform") or {}):
        derive_publisher_config(config)
    # V-S16 first: disabling a required stage would otherwise surface as a dependency error (V-S05).
    validate_profile_stage_requirements(config)
    normalize_config(config)
    if project_root is not None:
        validate_skill_file_existence(expand_active_stages(config), project_root)
