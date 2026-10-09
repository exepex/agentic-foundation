"""Schema errors must never echo a value pasted into the ``secrets`` block (review finding on #237).

jsonschema quotes the rejected instance in its messages. An operator who pastes a private key where a
secret NAME belongs must not see the key in CLI or CI logs.
"""
from __future__ import annotations

import copy
from typing import Any

from neutral_core_tests.test_publisher_config import _config_with_publisher
from stagr.core.config_validation import describe_schema_error, validate_config
from stagr.core.errors import ConfigSchemaError

PEM_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAsupersecretkeymaterial0123456789\n"
    "-----END RSA PRIVATE KEY-----\n"
)
KEY_FRAGMENTS = ("BEGIN RSA", "supersecretkeymaterial", "END RSA")


def _validation_error_text(config: dict[str, Any]) -> str:
    try:
        validate_config(config)
    except ConfigSchemaError as error:
        return str(error)
    raise AssertionError("expected the config to be rejected")


def _assert_no_key_material(error_text: str, field_path: str) -> None:
    for fragment in KEY_FRAGMENTS:
        assert fragment not in error_text, f"error leaked key material {fragment!r}: {error_text}"
    assert field_path in error_text and "value withheld" in error_text, error_text


def test_pasted_private_key_in_app_private_key_is_not_echoed() -> None:
    config = _config_with_publisher({"app_id": 1}, PEM_KEY)
    _assert_no_key_material(_validation_error_text(config), "app_private_key")


def test_pasted_key_in_any_secrets_entry_is_not_echoed() -> None:
    for setting in ("platform_token", "openai_api_key", "anthropic_api_key"):
        config = copy.deepcopy(_config_with_publisher({"app_id": 1}))
        config["secrets"] = {setting: PEM_KEY}
        _assert_no_key_material(_validation_error_text(config), setting)


def test_non_string_secret_value_is_reported_without_its_content() -> None:
    config = _config_with_publisher({"app_id": 1}, ["a-secret-in-a-list"])
    text = _validation_error_text(config)
    assert "a-secret-in-a-list" not in text and "app_private_key" in text


def test_a_value_pasted_in_place_of_the_secrets_block_is_not_echoed() -> None:
    for pasted_value in (PEM_KEY, [PEM_KEY]):
        config = copy.deepcopy(_config_with_publisher({"app_id": 1}))
        config["secrets"] = pasted_value
        _assert_no_key_material(_validation_error_text(config), "secrets")


def test_errors_for_non_secret_fields_keep_their_full_message() -> None:
    text = _validation_error_text(_config_with_publisher({"app_id": "not-a-number"}))
    assert "app_id" in text and "not-a-number" in text


def test_describe_schema_error_only_redacts_secret_named_fields() -> None:
    class FakeError:
        def __init__(self, path: list[Any], message: str, validator: str = "pattern") -> None:
            self.path, self.message, self.validator = path, message, validator

    assert "withheld" in describe_schema_error(FakeError(["secrets", "platform_token"], "'v' bad"))
    assert describe_schema_error(FakeError(["x", "branch"], "'v' bad")) == "'v' bad"
    assert describe_schema_error(FakeError([], "root bad")) == "root bad"
    assert "withheld" in describe_schema_error(FakeError(["secrets"], "'v' is not of type 'object'", "type"))
    unknown_key = FakeError(["secrets"], "'x' was unexpected", "additionalProperties")
    assert describe_schema_error(unknown_key) == "'x' was unexpected"


SECRET_VALUE_REDACTION_TESTS = [
    test_pasted_private_key_in_app_private_key_is_not_echoed,
    test_pasted_key_in_any_secrets_entry_is_not_echoed,
    test_non_string_secret_value_is_reported_without_its_content,
    test_a_value_pasted_in_place_of_the_secrets_block_is_not_echoed,
    test_errors_for_non_secret_fields_keep_their_full_message,
    test_describe_schema_error_only_redacts_secret_named_fields,
]
