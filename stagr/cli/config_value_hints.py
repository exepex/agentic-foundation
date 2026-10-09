"""Allowed-value hints for the `stagr init` template, built from Stagr's own definitions.

Each hint names the values a field accepts, so a team can choose without opening the guide. The
values come from the config schema (fixed lists), the backend registry (the providers and stage
types Stagr can render today) and the shipped skills directory, so a hint cannot drift from what
`stagr plan` accepts.
"""
from __future__ import annotations

import re

from stagr.core.backend_names import DEFAULT_BACKEND_BY_PROVIDER
from stagr.core.config_validation import load_schema
from stagr.core.enums import StageGate
from stagr.core.skill_validator import SHIPPED_SKILLS_DIRECTORY

from .render_pipeline import build_backend_registry

# Inline hints start at this column, so a block's hints line up.
HINT_COLUMN = 38

_SETTING_KEY_PATTERN = re.compile(r"^\s*(?:-\s+)?([a-z_]+):")


def _join(values: list[str]) -> str:
    return ", ".join(values)


def build_stage_hints() -> dict[str, str]:
    """Return the hint for each stage field, keyed by field name."""
    stage_properties = load_schema()["$defs"]["stage"]["properties"]
    renderers = build_backend_registry().list_renderers()
    stage_types = sorted({kind.value for renderer in renderers for kind in renderer.supported_stage_kinds})
    # The config spells a gate `advisory` or `blocking`; the core names them NON_BLOCKING and BLOCKING.
    gates = sorted(
        {"blocking" if gate is StageGate.BLOCKING else "advisory" for renderer in renderers for gate in renderer.supported_gates}
    )
    backends_by_provider: dict[str, list[str]] = {}
    for renderer in renderers:
        backends_by_provider.setdefault(renderer.provider, []).append(renderer.backend)
    backends = [
        _describe_backends(provider, provider_backends)
        for provider, provider_backends in backends_by_provider.items()
    ]
    shipped_skills = sorted(path.name for path in SHIPPED_SKILLS_DIRECTORY.iterdir() if path.is_dir())
    return {
        "id": "unique; lowercase letters, digits, - and _",
        "type": f"supported today: {_join(stage_types)}",
        "provider": f"supported today: {_join(list(backends_by_provider))}",
        "backend": f"supported today: {'; '.join(backends)}",
        "skill": f"shipped: {_join(shipped_skills)}, or your own",
        "gate": f"supported today: {_join(gates)}",
        "triggers": f"any of: {_join(stage_properties['triggers']['items']['enum'])}",
        "depends_on": "ids of stages that must pass first",
    }


def _describe_backends(provider: str, backends: list[str]) -> str:
    """``codex (default) or codex-api``, with ``for openai`` when more than one provider exists."""
    default_backend = DEFAULT_BACKEND_BY_PROVIDER.get(provider)
    ordered = sorted(backends, key=lambda backend: backend != default_backend)
    described = " or ".join(
        f"{backend} (default)" if backend == default_backend else backend for backend in ordered
    )
    return described if len(DEFAULT_BACKEND_BY_PROVIDER) == 1 else f"{described} for {provider}"


def build_platform_hints() -> dict[str, str]:
    """Return the hint for each platform field, keyed by field name."""
    platform_properties = load_schema()["properties"]["platform"]["properties"]
    return {
        "type": f"supported: {_join(platform_properties['type']['enum'])}",
        "same_repo_only": "true or false",
        "trusted_roles": f"any of: {_join(platform_properties['trusted_roles']['items']['enum'])}",
    }


def build_remediation_hints() -> dict[str, str]:
    """Return the hint for each remediation field, keyed by field name."""
    remediation_properties = load_schema()["properties"]["remediation"]["properties"]
    max_rounds = remediation_properties["max_rounds"]
    return {
        "provider": f"supported: {_join(remediation_properties['provider']['enum'])} (Claude Code Action)",
        "max_rounds": f"{max_rounds['minimum']} to {max_rounds['maximum']}",
    }


def add_hints(lines: list[str], hints: dict[str, str]) -> list[str]:
    """Append each field's hint to the first line that sets it; later lines stay plain."""
    remaining_hints = dict(hints)
    hinted_lines = []
    for line in lines:
        key_match = _SETTING_KEY_PATTERN.match(line)
        hint = remaining_hints.pop(key_match.group(1), None) if key_match else None
        # At least two spaces before `#`, so YAML reads the hint as a comment, not part of the value.
        padding = " " * max(2, HINT_COLUMN - len(line))
        hinted_lines.append(f"{line}{padding}# {hint}" if hint else line)
    return hinted_lines
