"""Tests for V-S08: Platform invocation compatibility.

V-S08 verifies that the platform supports every InvocationKind produced by
the configured backend renderers.
"""
from __future__ import annotations

from neutral_core_tests.static_validator_tests.helpers import (
    make_fresh_registry,
    make_normalized_stage,
    StubBackendRendererCiComponent,
    StubBackendRendererPrComment,
)


def test_v_s08_passes_when_invocation_kind_supported() -> None:
    """V-S08 raises no error when the backend's invocation kind is in the supported set."""
    from stagr.core.enums import InvocationKind
    from stagr.core.static_validator import validate_platform_invocation_compatibility

    registry = make_fresh_registry()
    renderer = StubBackendRendererPrComment()
    registry.register(renderer)
    stage = make_normalized_stage("review-stage")

    supported_kinds = frozenset({InvocationKind.PR_COMMENT})
    validate_platform_invocation_compatibility((stage,), registry, supported_kinds)


def test_v_s08_raises_for_unsupported_invocation_kind() -> None:
    """V-S08 raises StaticValidationError when CI_COMPONENT is used on a platform that lacks support."""
    from stagr.core.enums import InvocationKind
    from stagr.core.models import StaticValidationError
    from stagr.core.static_validator import validate_platform_invocation_compatibility

    registry = make_fresh_registry()
    renderer = StubBackendRendererCiComponent()
    registry.register(renderer)
    stage = make_normalized_stage(
        "ci-component-stage",
        provider="stub-provider",
        backend="ci-component-backend",
    )

    # Platform supports only PR_COMMENT — CI_COMPONENT is not in the set.
    supported_kinds = frozenset({InvocationKind.PR_COMMENT})

    try:
        validate_platform_invocation_compatibility((stage,), registry, supported_kinds)
        assert False, "Expected StaticValidationError but no exception was raised"  # noqa: B011
    except StaticValidationError as error:
        error_message = str(error)
        assert "V-S08" in error_message, (
            f"Error message must contain 'V-S08'; got: {error_message!r}"
        )
        assert "ci_component" in error_message, (
            f"Error message must name the unsupported kind; got: {error_message!r}"
        )


def test_v_s08_names_stage_in_error() -> None:
    """V-S08 error message includes the stage id."""
    from stagr.core.enums import InvocationKind
    from stagr.core.models import StaticValidationError
    from stagr.core.static_validator import validate_platform_invocation_compatibility

    registry = make_fresh_registry()
    renderer = StubBackendRendererCiComponent()
    registry.register(renderer)
    stage = make_normalized_stage(
        "named-stage",
        provider="stub-provider",
        backend="ci-component-backend",
    )

    try:
        validate_platform_invocation_compatibility(
            (stage,), registry, frozenset({InvocationKind.PR_COMMENT})
        )
        assert False, "Expected StaticValidationError"  # noqa: B011
    except StaticValidationError as error:
        assert "named-stage" in str(error), (
            f"Error must name the stage id; got: {str(error)!r}"
        )


def test_v_s08_github_renderer_declares_only_what_it_really_renders() -> None:
    """GitHubPlatformRenderer declares exactly the kinds it performs: PR_COMMENT and CI_COMPONENT."""
    from stagr.core.enums import InvocationKind
    from stagr.platforms.github.renderer import GitHubPlatformRenderer

    assert GitHubPlatformRenderer.SUPPORTED_INVOCATION_KINDS == frozenset(
        {InvocationKind.PR_COMMENT, InvocationKind.CI_COMPONENT}
    ), "GitHubPlatformRenderer must declare exactly {PR_COMMENT, CI_COMPONENT}; it wires no other kind"
