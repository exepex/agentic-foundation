"""V-S16: a built-in profile's stages are the pipeline's required minimum.

A profile names the stages every pull request must pass. The config may add stages and may change a
required stage's other settings (skill, triggers, model), but every stage the profile defines must
stay enabled and blocking; otherwise `stagr plan` and `stagr apply` fail. ``custom`` defines no
stages, so it requires none.
"""
from __future__ import annotations

from typing import Any

from .models import StaticValidationError
from .normalize import DEFAULT_PROFILE_NAME, expand_profile_defaults

_REQUIRED_GATE = "blocking"


def validate_profile_stage_requirements(config: dict[str, Any]) -> None:
    """Raise ``StaticValidationError`` (V-S16) when a stage the profile requires is off or not blocking."""
    profile_name: str = config.get("profile") or DEFAULT_PROFILE_NAME
    required_stage_ids = [stage["id"] for stage in expand_profile_defaults(profile_name, [])]
    merged_stages = {
        stage["id"]: stage for stage in expand_profile_defaults(profile_name, list(config.get("stages") or []))
    }
    violations = []
    for stage_id in required_stage_ids:
        stage = merged_stages[stage_id]
        if stage.get("enabled", True) is False:
            violations.append(f"stage '{stage_id}' is disabled")
        elif stage.get("gate") != _REQUIRED_GATE:
            violations.append(f"stage '{stage_id}' is not blocking (gate: {stage.get('gate', 'advisory')})")
    if violations:
        raise StaticValidationError(
            f"V-S16: profile '{profile_name}' requires the stages {', '.join(required_stage_ids)} to be "
            f"enabled and blocking, but {'; '.join(violations)}. Keep them on, or choose another profile "
            "(`stagr profile <name>`)."
        )
