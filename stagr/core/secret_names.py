"""The one home of every credential's secret name: the config's top-level ``secrets`` block.

Each credential Stagr uses has a setting under ``secrets`` naming the repository secret that holds
it, never the value, and a default name used when the setting is absent. A backend asks for a
credential by its alias (``SecretRef.alias``); ``SECRET_SETTING_BY_ALIAS`` says which setting
names it. An alias with no setting is its own secret name.
"""
from __future__ import annotations

from typing import Any

# Setting under `secrets` -> default secret name.
DEFAULT_SECRET_NAMES: dict[str, str] = {
    "app_private_key": "STAGR_APP_PRIVATE_KEY",
    "platform_token": "REMEDIATION_TOKEN",
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
}

# Backend secret alias -> the `secrets` setting that names it.
SECRET_SETTING_BY_ALIAS: dict[str, str] = {
    "TRUSTED_COMMENTER_TOKEN": "platform_token",
    "OPENAI_API_KEY": "openai_api_key",
}


def resolve_secret_name(raw_config: dict[str, Any], setting: str) -> str:
    """Return the secret name ``secrets.<setting>`` sets, or its default."""
    secrets_config = raw_config.get("secrets")
    configured_name = secrets_config.get(setting) if isinstance(secrets_config, dict) else None
    return configured_name or DEFAULT_SECRET_NAMES[setting]


def resolve_secret_alias(raw_config: dict[str, Any], alias: str) -> str:
    """Return the secret name for a backend's secret alias."""
    setting = SECRET_SETTING_BY_ALIAS.get(alias)
    return resolve_secret_name(raw_config, setting) if setting else alias
