"""Tests for the PlatformRenderer Protocol interface (issue #192).

Covers: Protocol conformance, neutral-type-only interface, and the returned-artifact
contract (every render method returns RenderedArtifact values; none writes files).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stagr.core.models import (
        ExecutionPlan,
        NormalizedStage,
        RenderContext,
        RenderedArtifact,
        StageRender,
        StageResultSpec,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_minimal_normalized_stage():
    """Return a valid NormalizedStage for use in stub calls."""
    from stagr.core.models import NormalizedStage
    from stagr.core.enums import StageKind, StageGate, StageTrigger

    return NormalizedStage(
        id="test-stage",
        kind=StageKind.REVIEW,
        provider="anthropic",
        backend="claude",
        skill=None,
        gate=StageGate.BLOCKING,
        triggers=(StageTrigger.PR_OPENED,),
        dependencies=(),
    )


def _build_minimal_execution_plan():
    """Return a valid ExecutionPlan with alias-only secrets and gate_disposition set."""
    from stagr.core.models import (
        ExecutionPlan,
        GateDispositionSpec,
        Invocation,
        SecretRef,
    )
    from stagr.core.enums import GateDispositionKind, InvocationKind

    return ExecutionPlan(
        stage_id="test-stage",
        invocation=Invocation(kind=InvocationKind.CI_COMPONENT),
        gate_disposition=GateDispositionSpec(
            kind=GateDispositionKind.ALWAYS_PASS,
            selector="always",
        ),
        required_secrets=(SecretRef(alias="PROVIDER_API_KEY"),),
    )


def _build_minimal_render_context():
    """Return a minimal RenderContext for use in stub calls."""
    from stagr.core.models import (
        RenderContext,
        RoutingPolicy,
        MergePolicy,
        TrustPolicy,
        DiscussionPolicy,
    )
    from stagr.core.enums import AuthorRole, ForkPolicy

    stage = _build_minimal_normalized_stage()
    routing_policy = RoutingPolicy(fast_path=None)
    merge_policy = MergePolicy(
        blocking_stage_ids=(stage.id,),
        require_head_bound=True,
        discussion_policy=DiscussionPolicy(require_resolved=False),
    )
    trust_policy = TrustPolicy(
        trusted_roles=(AuthorRole.OWNER,),
        fork_policy=ForkPolicy.DENY,
        human_merge_label="human-merge",
    )
    return RenderContext(
        stages=(stage,),
        routing_policy=routing_policy,
        merge_policy=merge_policy,
        trust_policy=trust_policy,
        platform="github",
    )


def _build_minimal_stage_result_spec():
    """Return a minimal StageResultSpec."""
    from stagr.core.models import StageResultSpec, StageResultProvenance
    from stagr.core.enums import StageResultSignalKind

    return StageResultSpec(
        stage_id="test-stage",
        signal_kind=StageResultSignalKind.CHECK_RUN,
        signal_selector="test/check",
        provenance=StageResultProvenance(publisher_identity="github-app[bot]"),
    )


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubPlatformRenderer:
    """Minimal PlatformRenderer implementation for conformance testing.

    Returns artifacts and never touches the file system.
    """

    def render_stage(
        self,
        plan: "ExecutionPlan",
        stage: "NormalizedStage",
        render_context: "RenderContext",
    ) -> "StageRender":
        from stagr.core.models import RenderedArtifact, StageRender

        return StageRender(
            result_spec=_build_minimal_stage_result_spec(),
            artifact=RenderedArtifact(path=f"stages/{stage.id}.yml", content="# stub artifact\n"),
        )

    def render_routing(self, render_context: "RenderContext") -> "RenderedArtifact":
        from stagr.core.models import RenderedArtifact

        return RenderedArtifact(path="routing.yml", content="# stub routing\n")

    def render_governance(
        self,
        result_specs: "tuple[StageResultSpec, ...]",
        render_context: "RenderContext",
    ) -> "RenderedArtifact":
        from stagr.core.models import RenderedArtifact

        return RenderedArtifact(path="governance.yml", content="# stub governance\n")

    def render_thread_resolution(
        self,
        result_specs: "tuple[StageResultSpec, ...]",
        render_context: "RenderContext",
        token_secret: str,
    ) -> "RenderedArtifact | None":
        return None

    def render_remediation(
        self,
        result_specs: "tuple[StageResultSpec, ...]",
        render_context: "RenderContext",
    ) -> "RenderedArtifact | None":
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_platform_renderer_protocol_conformance() -> None:
    """A minimal stub satisfies the PlatformRenderer Protocol (runtime isinstance check)."""
    from stagr.core.platform_renderer import PlatformRenderer

    stub = _StubPlatformRenderer()

    assert isinstance(stub, PlatformRenderer), (
        f"_StubPlatformRenderer must be recognised as a PlatformRenderer by isinstance(); "
        f"got type {type(stub)}"
    )


def test_platform_renderer_interface_uses_only_neutral_types() -> None:
    """Every PlatformRenderer method annotation is a neutral-core type or composition thereof.

    Inspects the actual public method signatures of PlatformRenderer using
    typing.get_type_hints so that introducing a platform-specific annotation
    (e.g. a GitHub workflow type) would fail this test.

    Approved annotations: ExecutionPlan, NormalizedStage, RenderContext,
    StageResultSpec, StageRender, RenderedArtifact, str (a secret name), None,
    tuple[<neutral>, ...] and unions of these.
    """
    import types
    import typing

    from stagr.core.platform_renderer import PlatformRenderer
    from stagr.core.models import (
        ExecutionPlan,
        NormalizedStage,
        RenderContext,
        RenderedArtifact,
        StageRender,
        StageResultSpec,
    )

    neutral_types = frozenset(
        {ExecutionPlan, NormalizedStage, RenderContext, StageResultSpec, StageRender, RenderedArtifact, str, type(None)}
    )

    def _is_neutral(annotation: object) -> bool:
        """Return True if annotation is composed only of neutral types or tuple."""
        if annotation in neutral_types:
            return True
        origin = typing.get_origin(annotation)
        if origin in (typing.Union, types.UnionType):
            return all(_is_neutral(arg) for arg in typing.get_args(annotation))
        if origin is tuple:
            return all(
                arg is Ellipsis or _is_neutral(arg)
                for arg in typing.get_args(annotation)
            )
        return False

    public_methods = ("render_stage", "render_routing", "render_governance", "render_thread_resolution", "render_remediation")
    for method_name in public_methods:
        method = getattr(PlatformRenderer, method_name)
        hints = typing.get_type_hints(method)
        for param_name, annotation in hints.items():
            if param_name == "self":
                continue
            assert _is_neutral(annotation), (
                f"PlatformRenderer.{method_name} parameter/return '{param_name}' has "
                f"non-neutral annotation {annotation!r}; only neutral-core types are "
                f"permitted in the PlatformRenderer interface"
            )


def test_platform_renderer_methods_return_artifacts() -> None:
    """render_stage, render_routing and render_governance each return their artifact."""
    from stagr.core.models import RenderedArtifact, StageRender

    stub = _StubPlatformRenderer()
    render_context = _build_minimal_render_context()

    stage_render = stub.render_stage(
        _build_minimal_execution_plan(), _build_minimal_normalized_stage(), render_context
    )
    routing_artifact = stub.render_routing(render_context)
    governance_artifact = stub.render_governance((_build_minimal_stage_result_spec(),), render_context)

    assert isinstance(stage_render, StageRender), f"got {type(stage_render)!r}"
    assert isinstance(stage_render.artifact, RenderedArtifact)
    assert isinstance(routing_artifact, RenderedArtifact), f"got {type(routing_artifact)!r}"
    assert isinstance(governance_artifact, RenderedArtifact), f"got {type(governance_artifact)!r}"


def test_rendered_artifact_accepts_repository_relative_posix_path() -> None:
    """A normal workflow path is accepted unchanged."""
    from stagr.core.models import RenderedArtifact

    artifact = RenderedArtifact(path=".github/workflows/stage-review.yml", content="name: x\n")

    assert artifact.path == ".github/workflows/stage-review.yml"
    assert artifact.content == "name: x\n"


def test_rendered_artifact_rejects_unsafe_paths() -> None:
    """Empty, absolute, backslash, NUL, '.', '..' and empty-segment paths are rejected."""
    from stagr.core.models import RenderedArtifact

    unsafe_paths = ("", "/etc/passwd", "../outside.yml", "a/../b.yml", "./a.yml", "a/./b.yml",
                    "a//b.yml", "a\\b.yml", "a/b\0.yml", "dir/")
    for unsafe_path in unsafe_paths:
        try:
            RenderedArtifact(path=unsafe_path, content="")
        except ValueError:
            continue
        assert False, f"RenderedArtifact must reject path {unsafe_path!r}"  # noqa: B011
