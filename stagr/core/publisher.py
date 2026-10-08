"""Platform publisher configuration for the neutral-core pipeline.

The publisher is the Stagr GitHub App that publishes Stagr-owned platform signals
(Check Runs). Two config keys describe it:

- ``platform.publisher.app_id``: the App's numeric ID. It is not a secret and is
  rendered as a literal into generated workflows. It has no default.
- ``platform.publisher.private_key_secret``: the NAME of the repository secret
  holding the App private key (never the key itself). Defaults to
  ``STAGR_APP_PRIVATE_KEY``.
- ``platform.publisher.app_slug``: the App's slug (the name in its URL). Optional; a backend that
  posts its results as the publisher (``PUBLISHER_IDENTITY``) needs it, because the platform names
  the App's account after the slug.

The whole block is optional in the config schema. ``derive_publisher_config`` is called only by
code that needs the publisher, and raises ``ConfigError`` when it is missing or invalid.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import ConfigError

DEFAULT_PRIVATE_KEY_SECRET = "STAGR_APP_PRIVATE_KEY"

# Placeholder a backend renderer uses for "the publisher posts this" (its evidence producer or
# finding author). Not a valid account name, so a platform renderer that does not replace it with
# the publisher's real account fails closed instead of matching nothing.
PUBLISHER_IDENTITY = "<stagr-publisher>"

# Same shape as the JSON schema patterns; fullmatch means a trailing newline is rejected.
_APP_ID_PATTERN = re.compile(r"[1-9][0-9]*", re.ASCII)
_SECRET_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*", re.ASCII)
_RESERVED_SECRET_PREFIX = "GITHUB_"
# GitHub App slugs: lowercase letters, digits and single hyphens.
_APP_SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*", re.ASCII)


@dataclass(frozen=True)
class PublisherConfig:
    """The Stagr GitHub App identity used to publish platform signals.

    ``app_id`` is a numeric string (e.g. ``"99001"``) ready to render as a literal.
    ``private_key_secret`` is the repository secret NAME, never a secret value.
    """

    app_id: str
    private_key_secret: str
    app_slug: str | None = None


def derive_publisher_config(config: dict[str, Any]) -> PublisherConfig:
    """Derive a :class:`PublisherConfig` from the raw config dict.

    Reads ``platform.publisher.app_id`` (required) and
    ``platform.publisher.private_key_secret`` (default ``STAGR_APP_PRIVATE_KEY``).
    An integer ``app_id`` is normalized to its decimal string.

    Raises:
        ConfigError: When the publisher block or ``app_id`` is absent, when
            ``app_id`` is not a positive integer (or digit string), or when
            ``private_key_secret`` is not a valid GitHub secret name.
    """
    platform_config = config.get("platform") or {}
    publisher_config = platform_config.get("publisher")
    if publisher_config is None:
        raise ConfigError(
            "platform.publisher is not configured; set platform.publisher.app_id to the "
            "numeric ID of the Stagr GitHub App"
        )
    if not isinstance(publisher_config, dict):
        raise ConfigError("platform.publisher must be a mapping with app_id and private_key_secret")
    known_keys = {"app_id", "private_key_secret", "app_slug"}
    unknown_keys = sorted(str(key) for key in set(publisher_config) - known_keys)
    if unknown_keys:
        raise ConfigError(f"platform.publisher has unknown key(s): {', '.join(unknown_keys)}")
    if "app_id" not in publisher_config:
        raise ConfigError(
            "platform.publisher.app_id is missing; set it to the numeric ID of the Stagr GitHub App"
        )
    return PublisherConfig(
        app_id=_normalize_app_id(publisher_config["app_id"]),
        private_key_secret=_validate_private_key_secret(
            publisher_config.get("private_key_secret", DEFAULT_PRIVATE_KEY_SECRET)
        ),
        app_slug=_validate_app_slug(publisher_config.get("app_slug")),
    )


def _normalize_app_id(raw_app_id: object) -> str:
    is_positive_integer = (
        isinstance(raw_app_id, int) and not isinstance(raw_app_id, bool) and raw_app_id >= 1
    )
    if is_positive_integer:
        return str(raw_app_id)
    if isinstance(raw_app_id, str) and _APP_ID_PATTERN.fullmatch(raw_app_id):
        return raw_app_id
    raise ConfigError(
        f"platform.publisher.app_id {raw_app_id!r} is invalid; it must be a positive integer "
        "(or a string of digits without a leading zero), the numeric ID of the Stagr GitHub App"
    )


def _validate_private_key_secret(raw_secret_name: object) -> str:
    if not (isinstance(raw_secret_name, str) and _SECRET_NAME_PATTERN.fullmatch(raw_secret_name)):
        raise ConfigError(
            "platform.publisher.private_key_secret is not a valid GitHub secret name "
            "(letters, digits, underscore; not starting with a digit). It must be the NAME of the "
            "repository secret holding the App private key, never the key itself"
        )
    if raw_secret_name.upper().startswith(_RESERVED_SECRET_PREFIX):
        raise ConfigError(
            f"platform.publisher.private_key_secret '{raw_secret_name}' uses the reserved "
            f"{_RESERVED_SECRET_PREFIX} prefix, which GitHub forbids for repository secrets"
        )
    return raw_secret_name


def _validate_app_slug(raw_app_slug: object) -> str | None:
    if raw_app_slug is None:
        return None
    if not (isinstance(raw_app_slug, str) and _APP_SLUG_PATTERN.fullmatch(raw_app_slug)):
        raise ConfigError(
            f"platform.publisher.app_slug {raw_app_slug!r} is invalid; it must be the App's slug, "
            "the lowercase name in its URL github.com/apps/<slug>"
        )
    return raw_app_slug
