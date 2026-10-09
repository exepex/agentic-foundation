"""Tests for Phase 1 secret alias built-in default resolution (issue #193).

Covers: TRUSTED_COMMENTER_TOKEN falling back to REMEDIATION_TOKEN; secrets.platform_token
overriding that default.
"""
from __future__ import annotations

from neutral_core_tests.phase1_render_loop_tests.helpers import (
    build_execution_plan,
    build_minimal_render_context,
    build_stage,
    build_stage_render,
    TrackingPlatformRenderer,
)


def test_phase1_trusted_commenter_token_resolves_to_remediation_token_when_platform_absent() -> None:
    """TRUSTED_COMMENTER_TOKEN resolves to REMEDIATION_TOKEN when no secrets.platform_token."""
    from stagr.core.render_loop import run_phase1
    from stagr.core.backend_renderer_registry import BackendRendererRegistry

    stage = build_stage("stage-commenter-default")
    render_context = build_minimal_render_context([stage])

    plan_with_commenter_token = build_execution_plan(
        "stage-commenter-default", secret_aliases=("TRUSTED_COMMENTER_TOKEN",)
    )

    class _BackendRendererWithCommenterToken:
        provider = "testprovider"
        backend = "testbackend"

        def render(self, stage_arg):
            return plan_with_commenter_token

    registry = BackendRendererRegistry()
    registry.register(_BackendRendererWithCommenterToken())

    received_plans: list = []

    class _CapturingPlatformRenderer(TrackingPlatformRenderer):
        def render_stage(self, plan, stage_arg, render_context_arg):
            received_plans.append(plan)
            return build_stage_render(stage_arg.id)

    provider_config: dict = {}  # no secrets — default must apply

    run_phase1(render_context, registry, _CapturingPlatformRenderer(), provider_config)

    assert received_plans, "PlatformRenderer.render_stage was not called"
    resolved_plan = received_plans[0]
    env_names = {ref.alias: ref.env_name for ref in resolved_plan.required_secrets}
    assert env_names == {"TRUSTED_COMMENTER_TOKEN": "REMEDIATION_TOKEN"}, (
        f"TRUSTED_COMMENTER_TOKEN must resolve to REMEDIATION_TOKEN when platform absent; got {env_names!r}"
    )


def test_phase1_platform_token_setting_overrides_trusted_commenter_default() -> None:
    """secrets.platform_token overrides the REMEDIATION_TOKEN default for TRUSTED_COMMENTER_TOKEN."""
    from stagr.core.render_loop import run_phase1
    from stagr.core.backend_renderer_registry import BackendRendererRegistry

    stage = build_stage("stage-commenter-override")
    render_context = build_minimal_render_context([stage])

    plan_with_commenter_token = build_execution_plan(
        "stage-commenter-override", secret_aliases=("TRUSTED_COMMENTER_TOKEN",)
    )

    class _BackendRendererOverride:
        provider = "testprovider"
        backend = "testbackend"

        def render(self, stage_arg):
            return plan_with_commenter_token

    registry = BackendRendererRegistry()
    registry.register(_BackendRendererOverride())

    received_plans: list = []

    class _CapturingPlatformRenderer(TrackingPlatformRenderer):
        def render_stage(self, plan, stage_arg, render_context_arg):
            received_plans.append(plan)
            return build_stage_render(stage_arg.id)

    provider_config = {"secrets": {"platform_token": "MY_CUSTOM_PAT"}}

    run_phase1(render_context, registry, _CapturingPlatformRenderer(), provider_config)

    assert received_plans, "PlatformRenderer.render_stage was not called"
    resolved_plan = received_plans[0]
    env_names = {ref.alias: ref.env_name for ref in resolved_plan.required_secrets}
    assert env_names == {"TRUSTED_COMMENTER_TOKEN": "MY_CUSTOM_PAT"}, (
        f"secrets.platform_token must override REMEDIATION_TOKEN default; got {env_names!r}"
    )
