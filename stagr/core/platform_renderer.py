"""PlatformRenderer Protocol interface.

Defines the contract every platform renderer must satisfy. A PlatformRenderer
maps an ExecutionPlan and NormalizedStage to platform-specific artifacts in
two phases:

  Phase 1  — render_stage: return the stage execution artifact and its
              StageResultSpec (used in Phase 2).
  Phase 2a — render_routing: return the routing artifact (path classification
              workflow) from the RenderContext.
  Phase 2b — render_governance: return the governance artifact from collected
              StageResultSpecs.

A renderer is a pure function of its inputs: it returns ``RenderedArtifact``
values (repository-relative path plus content) and never touches the file
system. ``stagr plan`` lists the artifacts; ``stagr apply`` writes the same
artifacts (and removes generated files the config no longer produces), so the
two commands cannot differ.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
    ExecutionPlan,
    NormalizedStage,
    RenderContext,
    RenderedArtifact,
    StageRender,
    StageResultSpec,
)


@runtime_checkable
class PlatformRenderer(Protocol):
    """Builds platform-specific artifacts from neutral-core render inputs.

    Implementations produce CI platform artifacts (workflows, gate checks) from
    neutral-core data types. No platform-specific types appear in the
    interface; they are internal to each concrete implementation.
    """

    def render_stage(
        self,
        plan: ExecutionPlan,
        stage: NormalizedStage,
        render_context: RenderContext,
    ) -> StageRender:
        """Phase 1: return the stage execution artifact and its StageResultSpec."""
        ...

    def render_routing(
        self,
        render_context: RenderContext,
    ) -> RenderedArtifact:
        """Phase 2a: return the routing artifact (path classification workflow)."""
        ...

    def render_governance(
        self,
        result_specs: tuple[StageResultSpec, ...],
        render_context: RenderContext,
    ) -> RenderedArtifact:
        """Phase 2b: return the governance artifact built from collected StageResultSpecs."""
        ...

    def render_thread_resolution(
        self,
        result_specs: tuple[StageResultSpec, ...],
        render_context: RenderContext,
        token_secret: str,
    ) -> RenderedArtifact | None:
        """Phase 2c: return the artifact resolving outdated finding threads, or None when no stage has any."""
        ...

    def render_remediation(
        self,
        result_specs: tuple[StageResultSpec, ...],
        render_context: RenderContext,
    ) -> RenderedArtifact | None:
        """Phase 2d: return the artifact that fixes or declines review findings, or None when off."""
        ...
