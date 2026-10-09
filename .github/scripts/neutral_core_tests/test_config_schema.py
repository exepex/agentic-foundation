"""Tests for the narrowed config schema and the config front door (``validate_config``).

The schema lists exactly the keys the neutral pipeline reads. Keys that only the retired legacy
renderer read were deleted, not deprecated: inside a Stagr namespace they are now unknown keys and
are rejected; at the top level, unknown keys are ignored (design-docs/01-neutral-config-contract.md).
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from neutral_core_tests.harness import REPO_ROOT

_VALID_CONFIG: dict[str, Any] = {
    "version": 2,
    "profile": "custom",
    "platform": {"type": "github"},
    "stages": [{"id": "review", "type": "review", "provider": "openai", "skill": "code-review"}],
}


def _config_with(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    """Return a copy of the valid config with ``value`` set at the dict ``path``."""
    config = copy.deepcopy(_VALID_CONFIG)
    node = config
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return config


def _expect_schema_error(config: dict[str, Any], expected_fragment: str) -> None:
    from stagr.core.config_validation import validate_config_schema
    from stagr.core.errors import ConfigSchemaError

    try:
        validate_config_schema(config)
    except ConfigSchemaError as error:
        assert expected_fragment in str(error), f"expected {expected_fragment!r} in: {error}"
        return
    raise AssertionError(f"expected a schema error mentioning {expected_fragment!r} for {config!r}")


def test_schema_accepts_the_valid_baseline_config() -> None:
    from stagr.core.config_validation import validate_config_schema

    validate_config_schema(copy.deepcopy(_VALID_CONFIG))


def test_schema_rejects_keys_deleted_from_stagr_namespaces() -> None:
    """Each key only the legacy renderer read is an unknown key inside its namespace."""
    deleted_keys: list[tuple[tuple[str, ...], Any, str]] = [
        (("platform", "host"), "github.example.com", "'host' was unexpected"),
        (("platform", "labels"), {"dispatch": "agentic-task"}, "'dispatch' was unexpected"),
        (("merge",), {"required_status_checks": []}, "'required_status_checks' was unexpected"),
        (("merge",), {"protected_paths": [".github/workflows/**"]}, "'protected_paths' was unexpected"),
        (("merge",), {"method": "squash"}, "'method' was unexpected"),
        (("routing",), {"fast_path": {"max_files": 3}}, "'max_files' was unexpected"),
        (("routing",), {"fast_path": {"max_lines": 10}}, "'max_lines' was unexpected"),
        (("routing",), {"fast_path": {"exclude": ["AGENTS.md"]}}, "'exclude' was unexpected"),
        (("defaults",), {"models": {"openai": {"tiers": {"complex": "gpt-x"}}}}, "'tiers' was unexpected"),
        (("secrets",), {"base_url": "https://example.test"}, "'base_url' was unexpected"),
        (("secrets",), {"extra_headers_secret": "H"}, "'extra_headers_secret' was unexpected"),
    ]
    for path, value, expected_fragment in deleted_keys:
        _expect_schema_error(_config_with(path, value), expected_fragment)


def test_schema_rejects_keys_deleted_from_stages() -> None:
    for extra_stage_key, value in [
        ("from", "code-review"),
        ("name", "Code review"),
        ("instructions", "be careful"),
        ("tiering", False),
        ("budgets", {"enabled": True}),
    ]:
        config = copy.deepcopy(_VALID_CONFIG)
        config["stages"][0][extra_stage_key] = value
        _expect_schema_error(config, extra_stage_key)


def test_schema_rejects_the_full_profile() -> None:
    _expect_schema_error(_config_with(("profile",), "full"), "'full'")


def test_schema_ignores_unknown_top_level_keys() -> None:
    """Retired contract keys and operator tooling keys at the top level do nothing and never fail."""
    from stagr.core.config_validation import validate_config_schema

    config = copy.deepcopy(_VALID_CONFIG)
    config.update({
        "modules": {"auto_merge": True, "sonar": True},
        "extends": "org-base.yml",
        "skills": {"code-review": {"source": "builtin"}},
        "models": {"aliases": {"fast": {"openai": "gpt-x"}}},
        "budgets": {"enabled": True},
        "tiering": {"enabled": True},
        "guardrails": {"redact_secrets_in_context": True},
        "observability": {"enabled": True},
        "build": {"preset": "maven"},
        "deploy": {"target": "staging"},
    })
    validate_config_schema(config)


def test_schema_stage_enums_mirror_the_neutral_core() -> None:
    """The schema accepts exactly the stage types, triggers, profiles and roles the core normalizes."""
    from stagr.core.config_validation import load_schema
    from stagr.core.enums import AuthorRole, StageKind, StageTrigger
    from stagr.core.normalize import _PROFILE_STAGE_DEFAULTS

    schema = load_schema()
    stage_properties = schema["$defs"]["stage"]["properties"]
    platform_properties = schema["properties"]["platform"]["properties"]
    assert set(stage_properties["type"]["enum"]) == {kind.value for kind in StageKind}
    assert set(stage_properties["triggers"]["items"]["enum"]) == {trigger.value for trigger in StageTrigger}
    assert set(schema["properties"]["profile"]["enum"]) == set(_PROFILE_STAGE_DEFAULTS)
    assert set(platform_properties["trusted_roles"]["items"]["enum"]) == {role.value for role in AuthorRole}


def test_schema_backend_is_a_plain_name() -> None:
    from stagr.core.config_validation import validate_config_schema

    config = copy.deepcopy(_VALID_CONFIG)
    config["stages"][0]["backend"] = "codex"
    validate_config_schema(config)
    config["stages"][0]["backend"] = {"name": "codex"}
    _expect_schema_error(config, "stages/0/backend")


def test_schema_rejects_a_type_the_core_cannot_normalize() -> None:
    for retired_type in ("plan", "integration-test", "docs", "release"):
        config = copy.deepcopy(_VALID_CONFIG)
        config["stages"][0]["type"] = retired_type
        _expect_schema_error(config, retired_type)


def test_schema_requires_a_stage_type() -> None:
    config = copy.deepcopy(_VALID_CONFIG)
    del config["stages"][0]["type"]
    _expect_schema_error(config, "'type' is a required property")


_NOT_SECRET_NAMES = ["${{ secrets.OTHER }}", "has space", "1STARTS_WITH_DIGIT", "GITHUB_TOKEN", "a-b", ""]


def test_secret_name_fields_reject_anything_that_is_not_a_name() -> None:
    """Secret names are placed into workflows as ``secrets.<name>``, so every such field is pattern-checked."""
    for bad_name in _NOT_SECRET_NAMES:
        for setting in ("app_private_key", "platform_token", "openai_api_key", "anthropic_api_key"):
            _expect_schema_error(_config_with(("secrets",), {setting: bad_name}), setting)


def test_pasted_key_in_a_secrets_map_is_not_echoed() -> None:
    from stagr.core.config_validation import validate_config_schema
    from stagr.core.errors import ConfigSchemaError

    pasted_key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEsupersecretkeymaterial\n-----END RSA PRIVATE KEY-----\n"
    config = _config_with(("secrets",), {"openai_api_key": pasted_key})
    try:
        validate_config_schema(config)
    except ConfigSchemaError as error:
        assert "supersecretkeymaterial" not in str(error) and "BEGIN RSA" not in str(error), str(error)
        assert "value withheld" in str(error), str(error)
        return
    raise AssertionError("expected the pasted key to be rejected")


def test_provider_without_default_backend_needs_an_explicit_backend() -> None:
    from stagr.core.models import ConfigError
    from stagr.core.pipeline import normalize_config

    config = {"version": 2, "profile": "custom", "stages": [{"id": "review", "type": "review", "provider": "gemini"}]}
    try:
        normalize_config(config)
    except ConfigError as error:
        assert "review" in str(error) and "gemini" in str(error) and "backend" in str(error), str(error)
    else:
        raise AssertionError("expected ConfigError for a provider with no default backend")

    config["stages"][0]["backend"] = "gemini-cli"
    assert normalize_config(config)[0].backend == "gemini-cli"


def test_front_door_reports_a_missing_skill_file() -> None:
    from stagr.core.config_validation import validate_config
    from stagr.core.models import StaticValidationError

    config = copy.deepcopy(_VALID_CONFIG)
    config["stages"][0]["skill"] = "no-such-skill"
    validate_config(config)  # no project root: the file check is skipped
    try:
        validate_config(config, project_root=Path(REPO_ROOT))
    except StaticValidationError as error:
        assert "V-S06" in str(error) and "no-such-skill" in str(error), str(error)
        return
    raise AssertionError("expected V-S06 for a missing skill file")


def test_front_door_reports_an_unknown_dependency() -> None:
    from stagr.core.config_validation import validate_config
    from stagr.core.models import StaticValidationError

    config = copy.deepcopy(_VALID_CONFIG)
    config["stages"][0]["depends_on"] = ["missing"]
    try:
        validate_config(config)
    except StaticValidationError as error:
        assert "V-S05" in str(error), str(error)
        return
    raise AssertionError("expected V-S05 for an unknown dependency")


def test_omitted_profile_means_the_standard_profile() -> None:
    """A config without ``profile`` gets the schema default, ``standard``: review and security stages."""
    from stagr.core.config_validation import load_schema
    from stagr.core.pipeline import expand_active_stages, normalize_config

    assert load_schema()["properties"]["profile"]["default"] == "standard"
    minimal_config = {"version": 2}
    assert [stage["id"] for stage in expand_active_stages(minimal_config)] == ["review", "security"]
    assert [stage.id for stage in normalize_config({"version": 2, "defaults": {"provider": "openai"}})] == [
        "review",
        "security",
    ]


CONFIG_SCHEMA_TESTS = [
    test_schema_accepts_the_valid_baseline_config,
    test_schema_rejects_keys_deleted_from_stagr_namespaces,
    test_schema_rejects_keys_deleted_from_stages,
    test_schema_rejects_the_full_profile,
    test_schema_ignores_unknown_top_level_keys,
    test_schema_stage_enums_mirror_the_neutral_core,
    test_schema_backend_is_a_plain_name,
    test_schema_rejects_a_type_the_core_cannot_normalize,
    test_schema_requires_a_stage_type,
    test_secret_name_fields_reject_anything_that_is_not_a_name,
    test_pasted_key_in_a_secrets_map_is_not_echoed,
    test_provider_without_default_backend_needs_an_explicit_backend,
    test_omitted_profile_means_the_standard_profile,
    test_front_door_reports_a_missing_skill_file,
    test_front_door_reports_an_unknown_dependency,
]
