"""Tests for Phase 1 rendering invariants (issue #193).

Covers: resolved ExecutionPlan has env_name populated on every SecretRef;
Phase 2 PlatformRenderer methods are never called during Phase 1.
"""
from __future__ import annotations

from neutral_core_tests.phase1_render_loop_tests.helpers import (
    build_execution_plan,
    build_minimal_render_context,
    build_stage,
    build_stage_render,
    TrackingPlatformRenderer,
)


def test_phase1_resolved_plan_has_env_name_set() -> None:
    """The ExecutionPlan passed to PlatformRenderer has env_name filled on every SecretRef."""
    from stagr.core.render_loop import run_phase1
    from stagr.core.backend_renderer_registry import BackendRendererRegistry

    stage = build_stage("stage-resolved")
    render_context = build_minimal_render_context([stage])

    plan_with_alias = build_execution_plan(
        "stage-resolved", secret_aliases=("OPENAI_API_KEY",)
    )

    class _BackendRendererWithAlias:
        provider = "testprovider"
        backend = "testbackend"

        def render(self, stage_arg):
            return plan_with_alias

    registry = BackendRendererRegistry()
    registry.register(_BackendRendererWithAlias())

    received_plans: list = []

    class _CapturingPlatformRenderer(TrackingPlatformRenderer):
        def render_stage(self, plan, stage_arg, render_context_arg):
            received_plans.append(plan)
            return build_stage_render(stage_arg.id)

    provider_config = {"secrets": {"openai_api_key": "MY_REAL_API_KEY_ENV_VAR"}}

    run_phase1(render_context, registry, _CapturingPlatformRenderer(), provider_config)

    assert received_plans, "PlatformRenderer.render_stage was not called"
    resolved_plan = received_plans[0]
    assert len(resolved_plan.required_secrets) == 1, (
        f"Expected 1 required secret; got {len(resolved_plan.required_secrets)}"
    )
    resolved_secret = resolved_plan.required_secrets[0]
    assert resolved_secret.alias == "OPENAI_API_KEY", (
        f"Expected alias 'OPENAI_API_KEY'; got {resolved_secret.alias!r}"
    )
    assert resolved_secret.env_name == "MY_REAL_API_KEY_ENV_VAR", (
        f"Expected env_name 'MY_REAL_API_KEY_ENV_VAR'; got {resolved_secret.env_name!r}"
    )


def test_phase1_phase2_methods_not_called() -> None:
    """render_routing and render_governance are never called during Phase 1."""
    from stagr.core.render_loop import run_phase1
    from stagr.core.backend_renderer_registry import BackendRendererRegistry

    stage = build_stage("stage-only")
    render_context = build_minimal_render_context([stage])

    class _PlainBackendRenderer:
        provider = "testprovider"
        backend = "testbackend"

        def render(self, stage_arg):
            return build_execution_plan(stage_arg.id)

    registry = BackendRendererRegistry()
    registry.register(_PlainBackendRenderer())
    # TrackingPlatformRenderer raises AssertionError if Phase 2 methods are called.
    platform_renderer = TrackingPlatformRenderer()
    provider_config: dict = {}

    # No exception means render_routing and render_governance were not called.
    run_phase1(render_context, registry, platform_renderer, provider_config)
