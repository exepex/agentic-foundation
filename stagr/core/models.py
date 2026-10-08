"""Neutral core data models.

All models are immutable dataclasses. No platform-specific fields appear here
(no GitHub event names, no permissions: strings, no CI YAML keys). The policy
models (TrustPolicy, RoutingPolicy, MergePolicy) are defined here as data
structures; their derivation logic lives in stagr/core/policy.py (Group C).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .enums import (
    AuthorRole,
    EvidenceKind,
    EvidenceSuccessCondition,
    ForkPolicy,
    GateDispositionKind,
    InvocationKind,
    StageGate,
    StageKind,
    StageResultConclusion,
    StageResultSignalKind,
    StageResultState,
    StageTrigger,
)


class ConfigError(Exception):
    """A normalization-time error caused by an unresolvable or invalid config field.

    Raised by neutral-core normalization functions when a required field cannot be
    resolved after all defaults are applied (e.g. a stage with no provider and no
    defaults.provider). Callers must surface this as a user-visible error that names
    the stage and the unresolvable field.
    """


class StaticValidationError(ValueError):
    """Raised when static validation of a stage config fails.

    Covers all V-S0x checks: schema validity, dependency reference validity
    (V-S05), and dependency graph acyclicity (V-S04). The exception message
    always names the relevant stage id(s) and the check code that failed.
    """


def _deep_freeze(value: Any) -> Any:
    """Recursively convert mutable containers to immutable equivalents.

    dict → MappingProxyType, list/tuple → tuple, set/frozenset → frozenset.
    Recurses into tuple and frozenset elements so values nested inside already-
    immutable containers are also frozen. Other values pass through unchanged.
    """
    if isinstance(value, dict):
        return MappingProxyType({k: _deep_freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_deep_freeze(v) for v in value)
    return value


# ---------------------------------------------------------------------------
# NormalizedStage (#174)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedStage:
    """Fully-resolved, platform-agnostic stage produced by the normalization pipeline.

    No `enabled` field — stages with enabled:false are removed before this object
    is constructed. All fields are fully typed; no raw dicts.
    """

    id: str
    kind: StageKind
    provider: str
    backend: str
    skill: str | None              # None = stage does not require a methodology skill
    gate: StageGate
    triggers: tuple[StageTrigger, ...]
    dependencies: tuple[str, ...]  # stage ids; validity enforced by V-S05
    model: str | None = None       # None = backend decides


# ---------------------------------------------------------------------------
# ExecutionPlan and Invocation (#175)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Invocation:
    """How the backend is asked to execute the stage.

    `params` is backend-defined and opaque to the neutral contract. The
    PlatformRenderer interprets the params for each supported InvocationKind.
    No platform-specific field names appear here.
    """

    kind: InvocationKind
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Deep-freeze so nested dicts/lists/sets are also immutable.
        # frozen=True prevents re-assignment; _deep_freeze prevents mutation of nested values.
        object.__setattr__(self, "params", _deep_freeze(dict(self.params)))


@dataclass(frozen=True)
class GateDispositionSpec:
    """How to determine PASS vs BLOCKED at run time.

    Separates "is the stage done?" (EvidenceSpec) from "given it is done, is
    the result PASS or BLOCKED?". `kind` identifies the disposition strategy;
    `selector` is backend-defined and opaque to the neutral contract.
    `scope` is required when kind is NO_OPEN_THREADS; None otherwise.
    """

    kind: GateDispositionKind
    selector: str                                # backend-defined; opaque to neutral contract
    scope: "FindingScopeSpec | None" = None

    def __post_init__(self) -> None:
        if self.kind is GateDispositionKind.NO_OPEN_THREADS and self.scope is None:
            raise ValueError(
                "GateDispositionSpec with kind NO_OPEN_THREADS requires scope to be set"
            )


@dataclass(frozen=True)
class CorrelationSpec:
    """How a piece of evidence is correlated to the correct head commit.

    Prevents a stale evidence item from satisfying a check on a newer commit.
    `head_sha`: True = evidence must be correlated to the current head SHA.
    `sha_field`: which field within the evidence item carries the SHA value
                 (backend-defined; opaque to the neutral contract).
    """

    head_sha: bool
    sha_field: str


@dataclass(frozen=True)
class FindingScopeSpec:
    """Constrains which review threads count as open findings for a NO_OPEN_THREADS gate.

    Without scope, the governance artifact would count all unresolved threads on the PR,
    including threads from other stages or pre-existing discussions.

    `created_by`: only count threads from comments posted by this identity.
    `head_sha`: True = only count threads linked to the current head SHA.
    `invocation_correlation`: per-invocation discriminator when two stages share the same
                              bot identity and head SHA (backend-defined; opaque).
                              None when created_by + head_sha are sufficient.
    """

    created_by: str
    head_sha: bool
    invocation_correlation: str | None = None


@dataclass(frozen=True)
class EvidenceSpec:
    """How stage completion is detected at run time.

    Backend-supplied; consumed by the PlatformRenderer writing the stage
    execution artifact. Uses semantic vocabulary — not platform-object names.
    `selector` is backend-defined and opaque to the neutral contract.
    `success_condition` is an EvidenceSuccessCondition enum value.
    `produced_by` is the identity that authors the evidence item (mirrors
    FindingScopeSpec.created_by). Evidence authored by any other identity must be
    ignored: PR content is untrusted, so an unauthenticated evidence item could be
    forged by anyone able to comment. Required for comment-based evidence kinds
    (REVIEW_RESULT, COMMENT_MATCH); the PlatformRenderer rejects the plan at render
    time when it is missing. The identity string format is platform-defined
    (e.g. a GitHub login).
    """

    kind: EvidenceKind
    selector: str                              # backend-defined, opaque
    correlation: CorrelationSpec
    success_condition: EvidenceSuccessCondition  # enum: COMPLETED, SUCCESS, MATCH_FOUND
    produced_by: str | None = None


@dataclass(frozen=True)
class SecretRef:
    """A backend-declared secret requirement.

    `alias` is the semantic, backend-defined name describing the credential's
    capability (e.g., "PROVIDER_API_KEY", "TRUSTED_COMMENTER_TOKEN"). BackendRenderers
    declare only `alias` — they must NOT set `env_name`.

    `env_name` is the resolved platform secret name (e.g., "OPENAI_API_KEY"). It is
    None when produced by a BackendRenderer and is filled in by the Phase 1 alias
    resolution step (see issue #193) before being passed to the PlatformRenderer.
    """

    alias: str
    env_name: str | None = None

    def __post_init__(self) -> None:
        if not self.alias:
            raise ValueError(
                "SecretRef.alias must not be empty; a backend declared a secret "
                "requirement with no alias name, which would produce a malformed "
                "environment-variable reference when resolved by the Phase 1 loop."
            )


@dataclass(frozen=True)
class ExecutionPlan:
    """Intermediate representation between BackendRenderer and PlatformRenderer.

    Opaque to the neutral contract beyond this structure. No platform-specific
    fields (no permissions:, no GitHub event names). `gate_disposition` is
    required — an ExecutionPlan without it is invalid.
    """

    stage_id: str
    invocation: Invocation
    gate_disposition: GateDispositionSpec
    required_secrets: tuple[SecretRef, ...] = ()
    evidence: tuple[EvidenceSpec, ...] = ()

    def __post_init__(self) -> None:
        # Enforce required field at construction time.
        if self.gate_disposition is None:
            raise ValueError(
                f"ExecutionPlan for stage '{self.stage_id}' must have gate_disposition set"
            )


# ---------------------------------------------------------------------------
# StageResultSpec and StageResultSignal (#177)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageResultProvenance:
    """Expected publisher identity for governance verification.

    A platform-neutral string identifying the trusted publisher of a stage's
    result signal. The PlatformRenderer fills this with the platform-specific
    identity (e.g., a GitHub App ID on GitHub). The governance artifact must
    reject any signal whose publisher identity does not match.
    """

    publisher_identity: str


@dataclass(frozen=True)
class StageResultSpec:
    """Render-time declaration of how a stage will signal its result at run time.

    Produced by the PlatformRenderer during Phase 1. Phase 2 uses this to
    generate the governance artifact without reading raw EvidenceSpec details.
    Signal locations are platform primitives; they are determined by the
    PlatformRenderer, not the BackendRenderer.
    """

    stage_id: str
    signal_kind: StageResultSignalKind
    signal_selector: str              # platform-specific locator
    provenance: StageResultProvenance
    # Login of the review backend whose threads are the stage's findings; None when the stage's
    # gate does not read review threads. Its outdated threads are resolved automatically.
    finding_author: str | None = None


@dataclass(frozen=True)
class RenderedArtifact:
    """One generated file: where it belongs in the repository and what it contains.

    A PlatformRenderer returns artifacts and never writes them; the caller decides
    whether to list them (``stagr plan``) or write them (``stagr apply``).
    ``path`` is a POSIX path relative to the repository root, for example
    ``.github/workflows/stage-review.yml``.
    """

    path: str
    content: str

    def __post_init__(self) -> None:
        path_segments = self.path.split("/")
        is_unsafe_path = (
            not self.path
            or "\\" in self.path
            or "\0" in self.path
            or self.path.startswith("/")
            or any(segment in ("", ".", "..") for segment in path_segments)
        )
        if is_unsafe_path:
            raise ValueError(
                f"artifact path {self.path!r} must be a relative POSIX path "
                f"without empty, '.' or '..' segments"
            )


@dataclass(frozen=True)
class StageRender:
    """Phase 1 output for one stage: its result declaration and its execution artifact."""

    result_spec: StageResultSpec
    artifact: RenderedArtifact


@dataclass(frozen=True)
class StageResultSignal:
    """Run-time value emitted by a stage execution artifact.

    headSha binding is mandatory — a StageResultSignal without headSha cannot
    be safely consumed (a stale signal for a prior commit could satisfy the gate
    for a new commit).
    """

    stage_id: str
    head_sha: str
    state: StageResultState
    conclusion: StageResultConclusion

    def __post_init__(self) -> None:
        if not self.head_sha:
            raise ValueError(
                f"StageResultSignal for stage '{self.stage_id}' must have head_sha set"
            )


# ---------------------------------------------------------------------------
# Policy models (#178 stubs — derivation logic in policy.py, Group C)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrustPolicy:
    """Who and what Stagr-generated automation may act on behalf of."""

    trusted_roles: tuple[AuthorRole, ...]
    fork_policy: ForkPolicy
    human_merge_label: str


@dataclass(frozen=True)
class PathMatchSpec:
    """Glob patterns for route classification."""

    paths: tuple[str, ...]


@dataclass(frozen=True)
class RouteStageMap:
    """Stage id sets for each route."""

    fast: tuple[str, ...]
    normal: tuple[str, ...]


@dataclass(frozen=True)
class FastPathPolicy:
    """Route classification rules when fast_path is enabled."""

    match: PathMatchSpec
    stages: RouteStageMap


@dataclass(frozen=True)
class RoutingPolicy:
    """How changed file paths are classified into route classes.

    `fast_path` is None when fast_path.enabled: false (or routing is absent).
    """

    fast_path: FastPathPolicy | None


@dataclass(frozen=True)
class DiscussionPolicy:
    """Neutral representation of the unresolved-discussions requirement."""

    require_resolved: bool


@dataclass(frozen=True)
class MergePolicy:
    """Merge eligibility requirements. Derived at normalization time; never re-derived at run time."""

    blocking_stage_ids: tuple[str, ...]
    require_head_bound: bool              # always True in V1
    discussion_policy: DiscussionPolicy | None = None


# ---------------------------------------------------------------------------
# RenderContext (#178)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderContext:
    """Complete input to every renderer. Assembled by the Stagr CLI; never modified by renderers.

    StageResultSpec[] is NOT here — it is produced during Phase 1 and collected
    by the CLI before Phase 2 begins.
    """

    stages: tuple[NormalizedStage, ...]
    routing_policy: RoutingPolicy
    merge_policy: MergePolicy
    trust_policy: TrustPolicy
    platform: str
