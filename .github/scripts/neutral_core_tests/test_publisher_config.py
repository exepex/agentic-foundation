"""Tests for platform.publisher config: schema, derive_publisher_config, front-door validation.

``platform.publisher`` is optional. It carries ``app_id`` (the Stagr GitHub App's numeric ID, no
default) and ``private_key_secret`` (the NAME of the secret holding the App key, default
``STAGR_APP_PRIVATE_KEY``). Prerequisite for the neutral-pipeline CLI commands (#201/#202/#203).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from neutral_core_tests.harness import REPO_ROOT

_SCHEMA_PATH = Path(REPO_ROOT) / "stagr" / "config.schema.json"

_INVALID_APP_IDS = [0, -5, 1.5, True, False, None, "", "0", "007", "abc", "12a", " 1", "-1", "1.5", [1], {"id": 1}]
_INVALID_SECRET_NAMES = [
    "",
    "stagr-app-key",
    "1STARTS_WITH_DIGIT",
    "has space",
    "GITHUB_APP_KEY",
    "github_app_key",
    "-----BEGIN RSA PRIVATE KEY-----",
    "${{ secrets.OTHER }}",
    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC==",
    123,
    None,
]


def _config_with_publisher(publisher: Any) -> dict[str, Any]:
    return {
        "version": 2,
        "profile": "standard",
        "platform": {"type": "github", "publisher": publisher},
        "defaults": {"provider": "anthropic", "models": {"anthropic": {"default": "c"}}},
    }


def _config_without_publisher() -> dict[str, Any]:
    config = _config_with_publisher({})
    del config["platform"]["publisher"]
    return config


def _schema_errors(config: dict[str, Any]) -> list[Any]:
    from jsonschema import Draft202012Validator

    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return list(Draft202012Validator(schema).iter_errors(config))


def _assert_derive_raises(publisher: Any, expected_fragment: str) -> None:
    from stagr.core.models import ConfigError
    from stagr.core.publisher import derive_publisher_config

    try:
        derive_publisher_config(_config_with_publisher(publisher))
    except ConfigError as error:
        assert expected_fragment in str(error), f"{expected_fragment!r} not in {error}"
        return
    raise AssertionError(f"expected ConfigError for publisher={publisher!r}")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_publisher_schema_accepts_valid_blocks() -> None:
    """Integer and digit-string app_id, with and without an explicit secret name, all validate."""
    valid_publishers = [
        {"app_id": 99001},
        {"app_id": "99001"},
        {"app_id": 1},
        {"app_id": 99001, "private_key_secret": "STAGR_APP_PRIVATE_KEY"},
        {"app_id": 99001, "private_key_secret": "my_app_key_2"},
        {"app_id": 99001, "private_key_secret": "_LEADING_UNDERSCORE"},
        {"private_key_secret": "STAGR_APP_PRIVATE_KEY"},
        {},
    ]
    for publisher in valid_publishers:
        errors = _schema_errors(_config_with_publisher(publisher))
        assert not errors, f"{publisher!r} should validate, got {[e.message for e in errors]}"


def test_publisher_schema_rejects_invalid_app_id() -> None:
    """Zero, negative, float, bool, null, empty, and non-numeric app_id values are rejected."""
    for invalid_app_id in _INVALID_APP_IDS:
        errors = _schema_errors(_config_with_publisher({"app_id": invalid_app_id}))
        assert errors, f"app_id {invalid_app_id!r} must be rejected by the schema"


def test_publisher_schema_rejects_invalid_private_key_secret() -> None:
    """Names with hyphens/spaces, key-looking values, reserved prefix, empty, and non-strings are rejected."""
    for invalid_name in _INVALID_SECRET_NAMES:
        errors = _schema_errors(_config_with_publisher({"app_id": 1, "private_key_secret": invalid_name}))
        assert errors, f"private_key_secret {invalid_name!r} must be rejected by the schema"


def test_publisher_schema_rejects_unknown_keys() -> None:
    """additionalProperties is false: unknown keys in platform.publisher are rejected."""
    errors = _schema_errors(_config_with_publisher({"app_id": 1, "private_key": "abc"}))
    assert errors, "unknown key 'private_key' must be rejected"
    errors = _schema_errors(_config_with_publisher({"app_id": 1, "installation_id": 5}))
    assert errors, "unknown key 'installation_id' must be rejected"


def test_publisher_schema_absent_block_stays_valid() -> None:
    """A config without platform.publisher still validates against the schema (the block is optional)."""
    assert not _schema_errors(_config_without_publisher())


def test_publisher_schema_documents_default_secret_name() -> None:
    """The schema advertises STAGR_APP_PRIVATE_KEY as the private_key_secret default."""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    publisher_schema = schema["properties"]["platform"]["properties"]["publisher"]
    assert publisher_schema["properties"]["private_key_secret"]["default"] == "STAGR_APP_PRIVATE_KEY"
    assert "default" not in publisher_schema["properties"]["app_id"], "app_id has no default"
    assert "required" not in schema["properties"]["platform"], "publisher must stay optional"


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------

def test_derive_publisher_config_normalizes_app_id_to_string() -> None:
    """An integer app_id and a digit-string app_id both derive the same string."""
    from stagr.core.publisher import derive_publisher_config

    from_integer = derive_publisher_config(_config_with_publisher({"app_id": 99001}))
    from_string = derive_publisher_config(_config_with_publisher({"app_id": "99001"}))
    assert from_integer.app_id == "99001"
    assert isinstance(from_integer.app_id, str)
    assert from_integer == from_string


def test_derive_publisher_config_applies_default_secret_name() -> None:
    """An omitted private_key_secret derives to STAGR_APP_PRIVATE_KEY."""
    from stagr.core.publisher import derive_publisher_config

    derived = derive_publisher_config(_config_with_publisher({"app_id": 99001}))
    assert derived.private_key_secret == "STAGR_APP_PRIVATE_KEY"


def test_derive_publisher_config_keeps_explicit_secret_name() -> None:
    """An explicit private_key_secret is preserved."""
    from stagr.core.publisher import derive_publisher_config

    derived = derive_publisher_config(
        _config_with_publisher({"app_id": "42", "private_key_secret": "ORG_STAGR_KEY"})
    )
    assert derived.app_id == "42"
    assert derived.private_key_secret == "ORG_STAGR_KEY"


def test_derive_publisher_config_result_is_frozen() -> None:
    """PublisherConfig is an immutable dataclass."""
    import dataclasses

    from stagr.core.publisher import PublisherConfig, derive_publisher_config

    derived = derive_publisher_config(_config_with_publisher({"app_id": 1}))
    assert isinstance(derived, PublisherConfig)
    try:
        derived.app_id = "2"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("PublisherConfig must be frozen")


def test_derive_publisher_config_raises_when_block_absent() -> None:
    """A missing publisher block (or platform section) raises ConfigError naming platform.publisher."""
    from stagr.core.models import ConfigError
    from stagr.core.publisher import derive_publisher_config

    for config in (_config_without_publisher(), {"version": 2}, {"platform": None}):
        try:
            derive_publisher_config(config)
        except ConfigError as error:
            assert "platform.publisher is not configured" in str(error), str(error)
            assert "app_id" in str(error), str(error)
        else:
            raise AssertionError(f"expected ConfigError for {config!r}")


def test_derive_publisher_config_raises_when_app_id_missing() -> None:
    """A publisher block without app_id raises ConfigError naming platform.publisher.app_id."""
    _assert_derive_raises({}, "platform.publisher.app_id is missing")
    _assert_derive_raises({"private_key_secret": "STAGR_APP_PRIVATE_KEY"}, "platform.publisher.app_id is missing")


def test_derive_publisher_config_raises_for_invalid_app_id() -> None:
    """Non-positive, float (incl. 1.0), bool, non-numeric, and newline-suffixed app_ids are rejected."""
    for invalid_app_id in [*_INVALID_APP_IDS, 1.0, "1\n", "١٢٣"]:
        if invalid_app_id is None:
            continue  # None is covered separately: it reads as an explicit empty value
        _assert_derive_raises({"app_id": invalid_app_id}, "platform.publisher.app_id")
    _assert_derive_raises({"app_id": None}, "platform.publisher.app_id")


def test_derive_publisher_config_raises_for_invalid_secret_name() -> None:
    """Values that are not a GitHub secret NAME are rejected, and the message never echoes the value."""
    for invalid_name in [*_INVALID_SECRET_NAMES, "STAGR_KEY\n"]:
        _assert_derive_raises({"app_id": 1, "private_key_secret": invalid_name}, "private_key_secret")
    key_like_value = "-----BEGIN RSA PRIVATE KEY-----"
    from stagr.core.models import ConfigError
    from stagr.core.publisher import derive_publisher_config

    try:
        derive_publisher_config(_config_with_publisher({"app_id": 1, "private_key_secret": key_like_value}))
    except ConfigError as error:
        assert key_like_value not in str(error), "error must not echo a credential-looking value"


def test_derive_publisher_config_raises_for_unknown_key_and_non_mapping() -> None:
    """Unknown publisher keys and a non-mapping publisher raise ConfigError."""
    _assert_derive_raises({"app_id": 1, "private_key": "x"}, "unknown key")
    _assert_derive_raises("99001", "must be a mapping")


def test_publisher_app_slug_is_optional_and_must_be_a_slug() -> None:
    """``app_slug`` names the App's account; schema and derivation accept slugs only."""
    from stagr.core.publisher import derive_publisher_config

    assert derive_publisher_config(_config_with_publisher({"app_id": 1})).app_slug is None
    for valid_slug in ("stagr", "stagr-demo-2"):
        publisher = {"app_id": 1, "app_slug": valid_slug}
        assert derive_publisher_config(_config_with_publisher(publisher)).app_slug == valid_slug
        assert not _schema_errors(_config_with_publisher(publisher))
    for invalid_slug in ("Stagr", "stagr[bot]", "-stagr", "stagr--demo", "stagr demo", "", 5, "stagr\n"):
        _assert_derive_raises({"app_id": 1, "app_slug": invalid_slug}, "platform.publisher.app_slug")
        if invalid_slug != "stagr\n":
            assert _schema_errors(_config_with_publisher({"app_id": 1, "app_slug": invalid_slug})), invalid_slug


# ---------------------------------------------------------------------------
# Front door (validate_config) and shipped configs
# ---------------------------------------------------------------------------

def test_publisher_front_door_accepts_valid_and_absent_block() -> None:
    """validate_config accepts a valid publisher block and a config with none."""
    from stagr.core.config_validation import validate_config

    validate_config(_config_with_publisher({"app_id": 99001}))
    validate_config(_config_without_publisher())


def test_publisher_front_door_rejects_what_schema_misses() -> None:
    """A trailing-newline secret name passes the schema's `$` anchor but is rejected at the front door."""
    from stagr.core.config_validation import validate_config
    from stagr.core.models import ConfigError

    for publisher in ({"app_id": 1, "private_key_secret": "STAGR_KEY\n"}, {"app_id": "1\n"}):
        assert not _schema_errors(_config_with_publisher(publisher)), "documents the schema gap"
        try:
            validate_config(_config_with_publisher(publisher))
        except ConfigError as error:
            assert "platform.publisher" in str(error), str(error)
        else:
            raise AssertionError(f"expected ConfigError for {publisher!r}")


def test_publisher_front_door_reports_schema_violation() -> None:
    """A key-looking secret value is rejected with a ConfigSchemaError pointing at the offending field."""
    from stagr.core.config_validation import validate_config
    from stagr.core.errors import ConfigSchemaError

    try:
        validate_config(_config_with_publisher({"app_id": 1, "private_key_secret": "-----BEGIN KEY-----"}))
    except ConfigSchemaError as error:
        assert "platform/publisher/private_key_secret" in str(error), str(error)
        return
    raise AssertionError("expected ConfigSchemaError")


def test_publisher_shipped_config_still_validates() -> None:
    """The dogfood config validates through the front door and names the Stagr App `plan`/`apply` need."""
    from stagr.core.config_validation import validate_config

    dogfood_path = Path(REPO_ROOT) / ".agentic" / "config.yml"
    config = yaml.safe_load(dogfood_path.read_text(encoding="utf-8"))
    from stagr.core.publisher import derive_publisher_config

    assert derive_publisher_config(config).app_id, "stagr plan and apply need platform.publisher.app_id"
    assert not _schema_errors(config), "the dogfood config must validate against the schema"
    validate_config(config, project_root=Path(REPO_ROOT))


PUBLISHER_CONFIG_TESTS = [
    test_publisher_schema_accepts_valid_blocks,
    test_publisher_schema_rejects_invalid_app_id,
    test_publisher_schema_rejects_invalid_private_key_secret,
    test_publisher_schema_rejects_unknown_keys,
    test_publisher_schema_absent_block_stays_valid,
    test_publisher_schema_documents_default_secret_name,
    test_derive_publisher_config_normalizes_app_id_to_string,
    test_derive_publisher_config_applies_default_secret_name,
    test_derive_publisher_config_keeps_explicit_secret_name,
    test_derive_publisher_config_result_is_frozen,
    test_derive_publisher_config_raises_when_block_absent,
    test_derive_publisher_config_raises_when_app_id_missing,
    test_derive_publisher_config_raises_for_invalid_app_id,
    test_derive_publisher_config_raises_for_invalid_secret_name,
    test_derive_publisher_config_raises_for_unknown_key_and_non_mapping,
    test_publisher_app_slug_is_optional_and_must_be_a_slug,
    test_publisher_front_door_accepts_valid_and_absent_block,
    test_publisher_front_door_rejects_what_schema_misses,
    test_publisher_front_door_reports_schema_violation,
    test_publisher_shipped_config_still_validates,
]
