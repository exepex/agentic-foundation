"""Policy derivation for the neutral-core normalization pipeline.

Implements Group C derivation functions (issues #184–#187). Each function
accepts the raw config dict and returns a frozen, validated policy dataclass.

Issue #184: derive_trust_policy — who and what Stagr-generated automation
may act on behalf of.
Issue #185: derive_routing_policy — fast-path routing policy.
Issue #186: derive_merge_policy — merge eligibility requirements.
derive_remediation_policy — automated fixing of review findings.
"""
from __future__ import annotations

from typing import Any

from .enums import AuthorRole, ForkPolicy, StageGate
from .models import (
    DiscussionPolicy,
    FastPathPolicy,
    MergePolicy,
    NormalizedStage,
    PathMatchSpec,
    RemediationPolicy,
    RouteStageMap,
    RoutingPolicy,
    StaticValidationError,
    TrustPolicy,
)

_DEFAULT_HUMAN_MERGE_LABEL = "human-merge"
_DEFAULT_TRUSTED_ROLES: tuple[AuthorRole, ...] = (
    AuthorRole.OWNER,
    AuthorRole.MEMBER,
    AuthorRole.COLLABORATOR,
)


def derive_trust_policy(config: dict[str, Any]) -> TrustPolicy:
    """Derive a TrustPolicy from the raw M1 config dict.

    Reads ``platform.trusted_roles``, ``platform.same_repo_only``, and
    ``platform.labels.human_merge``.

    Derivation rules
    ----------------
    - ``platform.trusted_roles`` → tuple of :class:`AuthorRole` values.
      When the key is absent the schema default applies: OWNER, MEMBER, and
      COLLABORATOR.  An explicit empty list produces an empty tuple.
      Each string must match a recognised :class:`AuthorRole` member; an
      unrecognised string raises :class:`~stagr.core.models.StaticValidationError`
      with code ``V-S14``.  ``CONTRIBUTOR`` is never added implicitly — only
      roles the operator explicitly lists appear in ``trusted_roles``.
    - ``platform.same_repo_only`` → :class:`ForkPolicy` member.  ``true``
      (the default when the key is absent) maps to :attr:`ForkPolicy.DENY`;
      ``false`` maps to :attr:`ForkPolicy.ALLOW_UNPRIVILEGED`.
    - ``platform.labels.human_merge`` → ``human_merge_label`` string.
      Defaults to ``"human-merge"`` when absent.

    Parameters
    ----------
    config:
        The raw YAML config dict (top-level, as loaded by ``yaml.safe_load``).

    Returns
    -------
    TrustPolicy
        An immutable, validated trust-policy dataclass.

    Raises
    ------
    StaticValidationError
        When any element of ``platform.trusted_roles`` is not a recognised
        :class:`AuthorRole` value (check code ``V-S14``).
    """
    platform_config: dict[str, Any] = config.get("platform", {}) or {}

    trusted_roles = _derive_trusted_roles(platform_config)
    fork_policy = _derive_fork_policy(platform_config)
    human_merge_label = _derive_human_merge_label(platform_config)

    return TrustPolicy(
        trusted_roles=trusted_roles,
        fork_policy=fork_policy,
        human_merge_label=human_merge_label,
    )


def _derive_trusted_roles(platform_config: dict[str, Any]) -> tuple[AuthorRole, ...]:
    if "trusted_roles" not in platform_config:
        return _DEFAULT_TRUSTED_ROLES
    raw_role_strings: list[str] = platform_config["trusted_roles"] or []
    valid_role_values = {member.value for member in AuthorRole}

    validated_roles: list[AuthorRole] = []
    for raw_role in raw_role_strings:
        role_string = str(raw_role).lower()
        if role_string not in valid_role_values:
            raise StaticValidationError(
                f"V-S14: unrecognised trusted_role '{raw_role}'. "
                f"Valid values: {sorted(valid_role_values)}"
            )
        validated_roles.append(AuthorRole(role_string))

    return tuple(validated_roles)


def _derive_fork_policy(platform_config: dict[str, Any]) -> ForkPolicy:
    same_repo_only: bool = platform_config.get("same_repo_only", True)
    if same_repo_only:
        return ForkPolicy.DENY
    return ForkPolicy.ALLOW_UNPRIVILEGED


def _derive_human_merge_label(platform_config: dict[str, Any]) -> str:
    labels_config: dict[str, Any] = platform_config.get("labels", {}) or {}
    return str(labels_config.get("human_merge", _DEFAULT_HUMAN_MERGE_LABEL))


def derive_routing_policy(config: dict[str, Any]) -> RoutingPolicy:
    """Derive a ``RoutingPolicy`` from the parsed config dict.

    Reads ``routing.fast_path`` to determine whether fast-path routing is
    active and, when it is, extracts the glob match patterns and route-stage
    map.

    Derivation rules
    ----------------
    - When ``routing`` is absent -> ``RoutingPolicy(fast_path=None)``
    - When ``routing.fast_path.enabled`` is ``false`` -> ``RoutingPolicy(fast_path=None)``
    - When ``routing.fast_path.enabled`` is ``true`` -> ``RoutingPolicy`` with a
      fully-populated ``FastPathPolicy`` (``match`` glob patterns and ``stages``
      route-stage map).

    V-S09 (route dependency-closure) is NOT validated here; it belongs in a
    separate validation pass.

    Parameters
    ----------
    config:
        The raw YAML config dict (top-level, as loaded by ``yaml.safe_load``).

    Returns
    -------
    RoutingPolicy
        An immutable routing-policy dataclass. ``fast_path`` is ``None`` when
        fast-path is disabled or the ``routing`` key is absent.
    """
    routing_cfg: dict[str, Any] = config.get("routing") or {}
    if not routing_cfg:
        return RoutingPolicy(fast_path=None)

    fast_path_cfg: dict[str, Any] = routing_cfg.get("fast_path") or {}
    if not fast_path_cfg.get("enabled", True):
        return RoutingPolicy(fast_path=None)

    match_paths: tuple[str, ...] = tuple(fast_path_cfg.get("globs", []))
    stages_cfg: dict[str, Any] = fast_path_cfg.get("stages") or {}
    fast_stage_ids: tuple[str, ...] = tuple(stages_cfg.get("fast", []))
    normal_stage_ids: tuple[str, ...] = tuple(stages_cfg.get("normal", []))

    fast_path = FastPathPolicy(
        match=PathMatchSpec(paths=match_paths),
        stages=RouteStageMap(fast=fast_stage_ids, normal=normal_stage_ids),
    )
    return RoutingPolicy(fast_path=fast_path)


def derive_merge_policy(
    config: dict[str, Any],
    normalized_stages: tuple[NormalizedStage, ...],
    trust_policy: TrustPolicy,  # reserved for V2 human-gate integration; not consumed in V1
) -> MergePolicy:
    """Derive a ``MergePolicy`` from the config and the normalized stage list.

    Derivation rules
    ----------------
    - ``blocking_stage_ids``: ids of every stage whose ``gate`` is
      :attr:`~stagr.core.enums.StageGate.BLOCKING`.  Stages with
      ``gate == NON_BLOCKING`` and any disabled stage (removed before
      normalization) are absent.
    - ``require_head_bound``: always ``True`` in V1.
    - ``discussion_policy``: derived from ``merge.discussions.require_resolved``
      when the key is present; ``None`` otherwise.

    Parameters
    ----------
    config:
        The raw YAML config dict (top-level, as loaded by ``yaml.safe_load``).
    normalized_stages:
        Tuple of :class:`~stagr.core.models.NormalizedStage` objects produced
        by the normalization pipeline.  Disabled stages have already been
        removed before this function is called.
    trust_policy:
        The derived :class:`~stagr.core.models.TrustPolicy` for this config.
        Accepted for future use; not consumed in V1.

    Returns
    -------
    MergePolicy
        An immutable merge-policy dataclass.
    """
    blocking_stage_ids = _derive_blocking_stage_ids(normalized_stages)
    discussion_policy = _derive_discussion_policy(config)

    return MergePolicy(
        blocking_stage_ids=blocking_stage_ids,
        require_head_bound=True,
        discussion_policy=discussion_policy,
    )


def _derive_blocking_stage_ids(
    normalized_stages: tuple[NormalizedStage, ...],
) -> tuple[str, ...]:
    return tuple(
        stage.id for stage in normalized_stages if stage.gate == StageGate.BLOCKING
    )


def _derive_discussion_policy(config: dict[str, Any]) -> DiscussionPolicy | None:
    merge_cfg: dict[str, Any] = config.get("merge", {}) or {}
    discussions_cfg: dict[str, Any] = merge_cfg.get("discussions", {}) or {}
    if "require_resolved" not in discussions_cfg:
        return None
    return DiscussionPolicy(require_resolved=bool(discussions_cfg["require_resolved"]))


_DEFAULT_REMEDIATION_MAX_ROUNDS = 5
_DEFAULT_REMEDIATION_API_KEY_SECRET = "ANTHROPIC_API_KEY"


def derive_remediation_policy(config: dict[str, Any]) -> RemediationPolicy | None:
    """Return the ``remediation`` policy, or None when the config has no ``remediation`` section.

    The schema has already checked the shape (provider, round range, secret name).
    """
    remediation_config: dict[str, Any] | None = config.get("remediation")
    if not remediation_config:
        return None
    return RemediationPolicy(
        provider=str(remediation_config["provider"]),
        max_rounds=int(remediation_config.get("max_rounds", _DEFAULT_REMEDIATION_MAX_ROUNDS)),
        api_key_secret=str(remediation_config.get("api_key_secret", _DEFAULT_REMEDIATION_API_KEY_SECRET)),
    )
