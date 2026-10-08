"""Shared helpers for governance workflow structure tests."""
from __future__ import annotations

from neutral_core_tests.github_platform_renderer_tests.helpers import (
    TEST_PUBLISHER_APP_ID,
    build_renderer,
    build_stage,
)
from stagr.core.enums import (
    AuthorRole,
    ForkPolicy,
    StageGate,
    StageResultSignalKind,
)
from stagr.core.models import (
    DiscussionPolicy,
    FastPathPolicy,
    MergePolicy,
    RenderContext,
    RoutingPolicy,
    StageResultProvenance,
    StageResultSpec,
    TrustPolicy,
)


def _render_governance_to_string(
    stage_id: str = "review",
    gate: StageGate = StageGate.BLOCKING,
    extra_stage_id: str | None = None,
    extra_gate: StageGate = StageGate.NON_BLOCKING,
    fast_path_policy: FastPathPolicy | None = None,
    fork_policy: ForkPolicy = ForkPolicy.DENY,
) -> str:
    """Render a governance workflow and return the YAML text."""
    renderer = build_renderer()
    primary_stage = build_stage(stage_id=stage_id, gate=gate)
    primary_spec = StageResultSpec(
        stage_id=stage_id,
        signal_kind=StageResultSignalKind.CHECK_RUN,
        signal_selector=f"stagr/stage/{stage_id}",
        provenance=StageResultProvenance(publisher_identity=TEST_PUBLISHER_APP_ID),
    )

    stages = (primary_stage,)
    specs: tuple[StageResultSpec, ...] = (primary_spec,)

    if extra_stage_id is not None:
        extra_stage = build_stage(stage_id=extra_stage_id, gate=extra_gate)
        extra_spec = StageResultSpec(
            stage_id=extra_stage_id,
            signal_kind=StageResultSignalKind.CHECK_RUN,
            signal_selector=f"stagr/stage/{extra_stage_id}",
            provenance=StageResultProvenance(
                publisher_identity=TEST_PUBLISHER_APP_ID
            ),
        )
        stages = (primary_stage, extra_stage)
        specs = (primary_spec, extra_spec)

    blocking_ids = tuple(
        stage.id for stage in stages if stage.gate is StageGate.BLOCKING
    )
    context = RenderContext(
        stages=stages,
        routing_policy=RoutingPolicy(fast_path=fast_path_policy),
        merge_policy=MergePolicy(
            blocking_stage_ids=blocking_ids,
            require_head_bound=True,
            discussion_policy=DiscussionPolicy(require_resolved=False),
        ),
        trust_policy=TrustPolicy(
            trusted_roles=(AuthorRole.OWNER,),
            fork_policy=fork_policy,
            human_merge_label="human-merge",
        ),
        platform="github",
    )
    return renderer.render_governance(specs, context).content
