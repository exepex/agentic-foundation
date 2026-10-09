"""Tests for stagr.core.config_parser — parse_config and ConfigVersionError (issue #197).

Covers:
  - Happy path: the actual dogfood config parses successfully (V-S02 green).
  - Version absent: raises ConfigVersionError.
  - Version is a string "2": raises ConfigVersionError (must be integer).
  - Version is an unsupported integer: raises ConfigVersionError.
  - Non-mapping YAML document: raises ConfigVersionError.
  - Returned dict carries the correct version value.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from neutral_core_tests.harness import REPO_ROOT


def test_parse_config_dogfood_config_succeeds() -> None:
    """The actual .agentic/config.yml parses successfully and returns version 2.

    This test uses the real dogfood config, not a hand-crafted fixture,
    as required by the issue acceptance criteria.
    """
    from stagr.core.config_parser import parse_config

    dogfood_config_path = Path(REPO_ROOT) / ".agentic" / "config.yml"
    config = parse_config(dogfood_config_path)
    assert config["version"] == 2, (
        f"Dogfood config version must be 2 (integer), got {config['version']!r}"
    )


def test_parse_config_returns_full_config_dict() -> None:
    """parse_config returns the complete config dict, not just the version field."""
    from stagr.core.config_parser import parse_config

    dogfood_config_path = Path(REPO_ROOT) / ".agentic" / "config.yml"
    config = parse_config(dogfood_config_path)
    assert isinstance(config, dict), f"Expected dict, got {type(config)}"
    assert "platform" in config, "Dogfood config must have a 'platform' key"
    assert "stages" in config, "Dogfood config must have a 'stages' key"


def _write_temp_config(content: dict) -> Path:
    """Write a temporary YAML config file and return its Path."""
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yml",
        delete=False,
        encoding="utf-8",
    )
    yaml.dump(content, temp_file)
    temp_file.flush()
    return Path(temp_file.name)


def test_parse_config_raises_config_version_error_when_version_absent() -> None:
    """parse_config raises ConfigVersionError when the version key is missing."""
    from stagr.core.config_parser import parse_config
    from stagr.core.errors import ConfigVersionError

    config_path = _write_temp_config({"profile": "standard"})
    try:
        raised = False
        try:
            parse_config(config_path)
        except ConfigVersionError as exc:
            raised = True
            assert exc.found_version is None, (
                f"Missing version must report found_version=None, got {exc.found_version!r}"
            )
        assert raised, "Expected ConfigVersionError for a config without a version key"
    finally:
        config_path.unlink(missing_ok=True)


def test_parse_config_raises_config_version_error_for_string_version() -> None:
    """parse_config raises ConfigVersionError when version is the string '2' (not integer)."""
    from stagr.core.config_parser import parse_config
    from stagr.core.errors import ConfigVersionError

    config_path = _write_temp_config({"version": "2", "profile": "standard"})
    try:
        raised = False
        try:
            parse_config(config_path)
        except ConfigVersionError as exc:
            raised = True
            assert exc.found_version == "2", (
                f"String '2' must be reported as found_version, got {exc.found_version!r}"
            )
        assert raised, "Expected ConfigVersionError when version is the string '2'"
    finally:
        config_path.unlink(missing_ok=True)


def test_parse_config_raises_config_version_error_for_unsupported_integer_version() -> None:
    """parse_config raises ConfigVersionError when version is an unsupported integer."""
    from stagr.core.config_parser import parse_config
    from stagr.core.errors import ConfigVersionError

    config_path = _write_temp_config({"version": 1, "profile": "standard"})
    try:
        raised = False
        try:
            parse_config(config_path)
        except ConfigVersionError as exc:
            raised = True
            assert exc.found_version == 1, (
                f"Version 1 must be reported as found_version, got {exc.found_version!r}"
            )
        assert raised, "Expected ConfigVersionError for version: 1"
    finally:
        config_path.unlink(missing_ok=True)


def test_parse_config_raises_config_version_error_for_non_mapping_document() -> None:
    """parse_config raises ConfigVersionError when the YAML document is not a mapping."""
    from stagr.core.config_parser import parse_config
    from stagr.core.errors import ConfigVersionError

    config_path = Path(tempfile.mktemp(suffix=".yml"))
    config_path.write_text("- item1\n- item2\n", encoding="utf-8")
    try:
        raised = False
        try:
            parse_config(config_path)
        except ConfigVersionError as exc:
            raised = True
            assert exc.found_version is None, (
                f"Non-mapping document must report found_version=None, got {exc.found_version!r}"
            )
        assert raised, "Expected ConfigVersionError for a YAML list document"
    finally:
        config_path.unlink(missing_ok=True)


def test_parse_config_raises_config_version_error_for_float_version() -> None:
    """parse_config raises ConfigVersionError when version is the float 2.0.

    YAML parses ``version: 2.0`` as a Python float.  Python equality (2.0 == 2)
    would previously have accepted it, but the contract requires strictly the
    integer 2.  Regression coverage for the type-check fix.
    """
    from stagr.core.config_parser import parse_config
    from stagr.core.errors import ConfigVersionError

    config_path = Path(tempfile.mktemp(suffix=".yml"))
    config_path.write_text("version: 2.0\nprofile: standard\n", encoding="utf-8")
    try:
        raised = False
        try:
            parse_config(config_path)
        except ConfigVersionError as exc:
            raised = True
            assert exc.found_version == 2.0, (
                f"Float 2.0 must be reported as found_version, got {exc.found_version!r}"
            )
        assert raised, "Expected ConfigVersionError when version is the float 2.0"
    finally:
        config_path.unlink(missing_ok=True)


def test_parse_config_raises_config_version_error_for_bool_version() -> None:
    """parse_config raises ConfigVersionError when version is the bool True.

    ``bool`` is a subclass of ``int`` in Python, so ``True == 1`` and
    ``isinstance(True, int)`` is True.  The strict ``type(version) is int``
    check correctly rejects booleans.  Regression coverage for the type-check
    fix.
    """
    from stagr.core.config_parser import parse_config
    from stagr.core.errors import ConfigVersionError

    config_path = Path(tempfile.mktemp(suffix=".yml"))
    config_path.write_text("version: true\nprofile: standard\n", encoding="utf-8")
    try:
        raised = False
        try:
            parse_config(config_path)
        except ConfigVersionError as exc:
            raised = True
            assert exc.found_version is True, (
                f"Bool True must be reported as found_version, got {exc.found_version!r}"
            )
        assert raised, "Expected ConfigVersionError when version is the bool True"
    finally:
        config_path.unlink(missing_ok=True)


def test_parse_config_error_message_names_found_version() -> None:
    """ConfigVersionError message includes the actual version value."""
    from stagr.core.config_parser import parse_config
    from stagr.core.errors import ConfigVersionError

    config_path = _write_temp_config({"version": 99, "profile": "standard"})
    try:
        raised = False
        try:
            parse_config(config_path)
        except ConfigVersionError as exc:
            raised = True
            assert "99" in str(exc), (
                f"Error message must name the found version (99): {exc}"
            )
        assert raised, "Expected ConfigVersionError for version: 99"
    finally:
        config_path.unlink(missing_ok=True)


PASTED_SECRET_YAML_CASES = (
    # The first is the reviewer's example; the second makes PyYAML quote the text in its message.
    ("mapping values are not allowed", "secrets:\n  platform_token: MY_TOKEN\n  app_private_key: sk-secret-abc123: oops\n"),
    ("constructor for the tag", "secrets:\n  platform_token: MY_TOKEN\n  app_private_key: !sk-secret-abc123 value\n"),
)


def test_parse_config_syntax_error_reports_position_but_never_config_text() -> None:
    """A YAML syntax error names line and column only; a pasted secret is never echoed."""
    from stagr.core.config_parser import parse_config
    from stagr.core.errors import ConfigSyntaxError

    for label, yaml_text in PASTED_SECRET_YAML_CASES:
        config_path = Path(tempfile.mkdtemp()) / "config.yml"
        config_path.write_text("version: 2\n" + yaml_text, encoding="utf-8")
        try:
            parse_config(config_path)
        except ConfigSyntaxError as error:
            message = str(error)
            assert "sk-secret-abc123" not in message, f"{label}: the pasted secret leaked: {message}"
            assert "line 4" in message, f"{label}: no line number in: {message}"
            assert "column" in message, f"{label}: no column number in: {message}"
            assert error.__cause__ is None, f"{label}: the parser error is chained as the cause"
            assert error.__suppress_context__, f"{label}: the parser error is shown as context"
        else:
            raise AssertionError(f"{label}: expected ConfigSyntaxError")


CONFIG_PARSER_TESTS: list = [
    test_parse_config_dogfood_config_succeeds,
    test_parse_config_returns_full_config_dict,
    test_parse_config_raises_config_version_error_when_version_absent,
    test_parse_config_raises_config_version_error_for_string_version,
    test_parse_config_raises_config_version_error_for_unsupported_integer_version,
    test_parse_config_raises_config_version_error_for_non_mapping_document,
    test_parse_config_raises_config_version_error_for_float_version,
    test_parse_config_raises_config_version_error_for_bool_version,
    test_parse_config_error_message_names_found_version,
    test_parse_config_syntax_error_reports_position_but_never_config_text,
]
