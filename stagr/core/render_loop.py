"""Phase 1 rendering loop.

Implements ``run_phase1``, which drives the per-stage
BackendRenderer → ExecutionPlan → PlatformRenderer pipeline described in
design-docs/04-render-time-architecture.md.

Phase 1 invariant: routing and merge policy are never read here. The loop
structure enforces this — neither RenderContext.routing_policy nor
RenderContext.merge_policy is accessed. PlatformRenderer Phase 2 methods
(render_routing, render_governance) are never called from this module.
"""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from .backend_renderer_registry import BackendRendererRegistry
from .models import ExecutionPlan, NormalizedStage, RenderContext, StageRender
from .secret_names import resolve_secret_alias, resolve_secret_name

if TYPE_CHECKING:
    from .platform_renderer import PlatformRenderer


def resolve_platform_token_secret(provider_config: dict) -> str:
    """Return the secret name of the platform token: ``secrets.platform_token`` or its default."""
    return resolve_secret_name(provider_config, "platform_token")


def _resolve_secret_aliases(plan: ExecutionPlan, provider_config: dict) -> ExecutionPlan:
    """Return a new ExecutionPlan with every SecretRef.env_name filled in.

    Each alias resolves through the config's ``secrets`` block (``secret_names``): the setting
    that names it, else that setting's default; an alias with no setting is its own secret name.
    The original plan is not mutated; a new frozen ExecutionPlan is returned.
    """
    return dataclasses.replace(
        plan,
        required_secrets=tuple(
            dataclasses.replace(secret_ref, env_name=resolve_secret_alias(provider_config, secret_ref.alias))
            for secret_ref in plan.required_secrets
        ),
    )


def run_phase1(
    context: RenderContext,
    registry: BackendRendererRegistry,
    platform_renderer: "PlatformRenderer",
    provider_config: dict,
) -> list[StageRender]:
    """Execute Phase 1 of the rendering pipeline for all stages in context.

    For each NormalizedStage in ``context.stages``:

    1. Look up the BackendRenderer in ``registry`` for
       ``(stage.provider, stage.backend)``; raises
       ``BackendRendererNotFoundError`` when none is found.
    2. Call ``backend_renderer.render(stage)`` to get an ``ExecutionPlan``
       with alias-only ``SecretRef`` values (``env_name`` not yet set).
    3. Resolve each ``SecretRef.alias`` through the config's ``secrets`` block
       (``_resolve_secret_aliases``) before the PlatformRenderer is called.
    4. Call ``platform_renderer.render_stage(resolved_plan, stage, context)``
       to get a ``StageRender`` (the stage artifact and its ``StageResultSpec``).
    5. Collect and return all ``StageRender`` objects.

    Phase 1 invariant: ``render_routing`` and ``render_governance`` are never
    called here. ``context.routing_policy`` and ``context.merge_policy`` are
    never read.

    Returns a list of length ``len(context.stages)`` in stage order.
    """
    # Preparation pass: validate every plan and resolve every alias before any render_stage call,
    # so a later stage's unresolvable alias is reported before any stage is rendered.
    prepared: list[tuple[ExecutionPlan, NormalizedStage]] = []
    for stage in context.stages:
        backend_renderer = registry.get(stage.provider, stage.backend)

        unresolved_plan: ExecutionPlan = backend_renderer.render(stage)
        if unresolved_plan.stage_id != stage.id:
            raise ValueError(
                f"BackendRenderer returned an ExecutionPlan with stage_id "
                f"{unresolved_plan.stage_id!r} but was called for stage "
                f"{stage.id!r}; the renderer must return a plan for the "
                f"stage it received."
            )

        resolved_plan: ExecutionPlan = _resolve_secret_aliases(unresolved_plan, provider_config)
        prepared.append((resolved_plan, stage))

    # Rendering pass: only reached when all stages have a validated, fully-resolved plan.
    stage_renders: list[StageRender] = []
    for resolved_plan, stage in prepared:
        stage_render: StageRender = platform_renderer.render_stage(
            resolved_plan, stage, context
        )
        if stage_render.result_spec.stage_id != stage.id:
            raise ValueError(
                f"PlatformRenderer returned a StageResultSpec with stage_id "
                f"{stage_render.result_spec.stage_id!r} but was called for stage "
                f"{stage.id!r}; the renderer must return a result for the "
                f"stage it received."
            )
        stage_renders.append(stage_render)

    return stage_renders
