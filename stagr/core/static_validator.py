"""Static validation checks V-S07, V-S08, and V-S09 for the Stagr neutral core.

These checks run after normalization and before rendering begins.

**V-S07** — BackendRenderer availability: every ``(provider, backend)`` pair
in the normalized stage list has a registered ``BackendRenderer``. Uses
``registry.has(provider, backend)``.

**V-S08** — Platform invocation compatibility: the target platform supports
every ``InvocationKind`` that the backend renderers would produce. Catches
``CI_COMPONENT`` on a platform that does not support it. Prerequisite: V-S07
must pass so all backend renderers are available. Calls each stage's renderer
to obtain the ``ExecutionPlan.invocation.kind`` and checks it against the
caller-supplied ``supported_invocation_kinds`` set.

**V-S09** — Route dependency-closure: when ``fast_path.enabled: true`` the
stage set for each route (``fast`` and ``normal``) must be
dependency-closed. If stage S is in the route set and S declares dependency D,
then D must also be in the set. The error names the stage and the missing
dependency. This check is skipped entirely when
``routing_policy.fast_path`` is ``None``.

Design source: design-docs/07-validation.md (V-S07 through V-S09).
"""
from __future__ import annotations

from .backend_renderer_registry import BackendRendererRegistry
from .enums import InvocationKind
from .models import NormalizedStage, RoutingPolicy, StaticValidationError


def validate_backend_renderer_availability(
    normalized_stages: tuple[NormalizedStage, ...],
    registry: BackendRendererRegistry,
) -> None:
    """Raise ``StaticValidationError`` (V-S07) for any unregistered backend.

    Checks every ``(provider, backend)`` pair that appears in
    ``normalized_stages`` against ``registry.has(provider, backend)``.  The
    first missing pair raises immediately, naming the pair and the stage.

    Args:
        normalized_stages: Stages produced by the normalization pipeline.
        registry: The ``BackendRendererRegistry`` populated before validation.

    Raises:
        StaticValidationError: V-S07 when a ``(provider, backend)`` pair has
            no registered renderer.
    """
    for stage in normalized_stages:
        if not registry.has(stage.provider, stage.backend):
            raise StaticValidationError(
                f"V-S07: no BackendRenderer registered for "
                f"provider={stage.provider!r}, backend={stage.backend!r} "
                f"(stage '{stage.id}')"
            )


def validate_platform_invocation_compatibility(
    normalized_stages: tuple[NormalizedStage, ...],
    registry: BackendRendererRegistry,
    supported_invocation_kinds: frozenset[InvocationKind],
) -> None:
    """Raise ``StaticValidationError`` (V-S08) when a stage uses an unsupported kind.

    Calls each stage's registered ``BackendRenderer.render(stage)`` to obtain
    the ``ExecutionPlan``, then checks that
    ``plan.invocation.kind in supported_invocation_kinds``.

    Prerequisite: V-S07 must pass before this function is called; all backend
    renderers in ``registry`` must be available.

    Args:
        normalized_stages: Stages produced by the normalization pipeline.
        registry: The ``BackendRendererRegistry`` populated before validation.
        supported_invocation_kinds: The set of ``InvocationKind`` values the
            target platform renderer supports.  Typically the caller reads this
            from the concrete ``PlatformRenderer`` class.

    Raises:
        StaticValidationError: V-S08 when a stage's backend renderer produces
            an ``InvocationKind`` not in ``supported_invocation_kinds``.
    """
    for stage in normalized_stages:
        backend_renderer = registry.get(stage.provider, stage.backend)
        execution_plan = backend_renderer.render(stage)
        if execution_plan.invocation.kind not in supported_invocation_kinds:
            raise StaticValidationError(
                f"V-S08: platform does not support invocation kind "
                f"{execution_plan.invocation.kind.value!r} required by "
                f"stage '{stage.id}' "
                f"(provider={stage.provider!r}, backend={stage.backend!r})"
            )


def validate_route_dependency_closure(
    routing_policy: RoutingPolicy,
    normalized_stages: tuple[NormalizedStage, ...],
) -> None:
    """Raise ``StaticValidationError`` (V-S09) for any non-closed route stage set.

    When ``routing_policy.fast_path`` is ``None`` (fast-path disabled), this
    function returns immediately without performing any check.

    When fast-path is enabled, both the ``fast`` and ``normal`` route stage
    sets are checked for dependency-closure: for every stage S in the set, all
    of S's declared ``dependencies`` must also be in the set.  The first
    violation raises immediately, naming the route, the stage, and the missing
    dependency.

    Args:
        routing_policy: The ``RoutingPolicy`` derived from the config.  When
            ``fast_path`` is ``None`` the check is skipped.
        normalized_stages: Stages produced by the normalization pipeline.
            Stages listed in the route sets but absent from this tuple are
            ignored (their reference validity is covered by V-S05).

    Raises:
        StaticValidationError: V-S09 when a route stage set is not
            dependency-closed, naming the route, the stage, and the missing
            dependency.
    """
    if routing_policy.fast_path is None:
        return

    stage_by_id: dict[str, NormalizedStage] = {stage.id: stage for stage in normalized_stages}

    route_stage_sets: dict[str, tuple[str, ...]] = {
        "fast": routing_policy.fast_path.stages.fast,
        "normal": routing_policy.fast_path.stages.normal,
    }

    for route_name, stage_ids in route_stage_sets.items():
        stage_id_set: set[str] = set(stage_ids)
        for stage_id in stage_ids:
            if stage_id not in stage_by_id:
                # Unknown ids are a V-S05 concern; skip here to avoid double-reporting.
                continue
            stage = stage_by_id[stage_id]
            for dependency_id in stage.dependencies:
                if dependency_id not in stage_id_set:
                    raise StaticValidationError(
                        f"V-S09: route '{route_name}' is not dependency-closed: "
                        f"stage '{stage_id}' depends on '{dependency_id}' "
                        f"which is not in the route set"
                    )


def validate_every_stage_is_routed(
    routing_policy: RoutingPolicy,
    normalized_stages: tuple[NormalizedStage, ...],
) -> None:
    """Raise ``StaticValidationError`` (V-S17) when an enabled stage runs on no route.

    With the fast path on, a stage listed in neither ``stages.fast`` nor ``stages.normal`` never
    runs, and the merge gate never evaluates it: a blocking stage would silently stop guarding the
    merge. Skipped when the fast path is off, because every stage then runs on every pull request.
    """
    if routing_policy.fast_path is None:
        return
    routed_stage_ids = set(routing_policy.fast_path.stages.fast) | set(routing_policy.fast_path.stages.normal)
    unrouted_stage_ids = [stage.id for stage in normalized_stages if stage.id not in routed_stage_ids]
    if unrouted_stage_ids:
        raise StaticValidationError(
            f"V-S17: stage(s) {', '.join(unrouted_stage_ids)} run on no route: add each to "
            "routing.fast_path.stages.normal (or .fast), or disable the fast path."
        )
