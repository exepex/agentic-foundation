"""Render-time configuration of the stage signal runtime (issue #206).

``build_stage_signal_config`` turns an ``ExecutionPlan`` into the JSON document that the generated
workflow hands to ``runtime/stage_signal_runtime.py``. All per-stage data reaches the runtime as this
one document, never as text spliced into script source, so a backend-defined selector or identity
cannot break out of (or inject into) the generated code.

The builder fails closed: any plan the GitHub runtime cannot evaluate EXACTLY raises ``ValueError``
here, at ``stagr apply`` time, instead of degrading into a weaker check at run time. Unsupported:
- evidence kinds other than the comment-based ``REVIEW_RESULT`` and ``COMMENT_MATCH``;
- evidence that is not head-bound, has an unknown ``sha_field``, or lacks ``produced_by``;
- ``FindingScopeSpec.invocation_correlation`` (GitHub V1 has no reliable binding for it);
- any invocation kind other than ``PR_COMMENT``: the GitHub renderer cannot wire it;
- a plan without evidence: a ``PR_COMMENT`` invocation completes asynchronously, so nothing could
  ever prove it finished, and reporting PASS after merely posting the request would be a false
  signal;
- a ``PR_COMMENT`` invocation (issue #205) without a non-empty ``params["body"]``, without the
  ``TRUSTED_COMMENTER_TOKEN`` secret it must be posted with, or with a ``params["lease_minutes"]``
  that is not an integer from 1 to ``MAX_LEASE_MINUTES``. The lease defaults to 30 minutes.
- (issue #207) a dependency on a stage that is not in the render context, on itself, or whose id or
  the publisher App id is not plain text that is safe to place inside a workflow ``if:`` expression.

Eligibility data (issue #207) travels in the same document: ``dependencies`` (each upstream stage id
with the name of the Check Run that carries its signal) and ``routing`` (the Check Run that carries
the ``RouteClassification`` and the stage ids that apply to the FAST and to the NORMAL route, or
``null`` when no fast path is configured).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from stagr.core.enums import (
    EvidenceKind,
    EvidenceSuccessCondition,
    ForkPolicy,
    GateDispositionKind,
    InvocationKind,
)
from stagr.core.models import EvidenceSpec, ExecutionPlan, NormalizedStage, RenderContext
from stagr.platforms.github.routing_workflow import ROUTE_CLASSIFICATION_CHECK_RUN_NAME
from stagr.platforms.github.runtime.stage_signal_runtime import (
    DEFAULT_LEASE_MINUTES,
    INVOCATION_KIND_PR_COMMENT,
    MAX_LEASE_MINUTES,
    TRUSTED_COMMENTER_TOKEN_VARIABLE,
)

# Check Run name of a stage signal: design-doc 08, ``stagr/stage/<stageId>``.
STAGE_CHECK_RUN_NAME_PREFIX = "stagr/stage"

# The stage id pattern of config.schema.json. Dependency ids are placed inside a quoted literal of
# the wake-up ``if:`` expression, so anything outside it is refused here rather than escaped.
_STAGE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_APP_ID_PATTERN = re.compile(r"^[0-9]{1,20}$")

# The "Code Review" row of the Codex review-summary table; see the runtime's REVIEW_RESULT handling.
_REVIEW_SUMMARY_SHA_FIELD = "review_summary_sha"

# GitHub login: alphanumerics and single hyphens, optionally suffixed "[bot]". Also the only
# character set allowed into the job-level ``if:`` expression, which embeds the login as a literal.
_GITHUB_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\[bot\])?$")

_GITHUB_EXPRESSION_OPENER = "${{"


@dataclass(frozen=True)
class StageSignalConfig:
    """The validated runtime configuration plus the facts the workflow assembly needs."""

    document: dict[str, Any]
    evidence_producers: tuple[str, ...]

    @property
    def upstream_check_run_names(self) -> tuple[str, ...]:
        """Check Run names of the stages this stage depends on, in declaration order."""
        return tuple(item["checkRunName"] for item in self.document["dependencies"])

    @property
    def has_dependencies(self) -> bool:
        """True when upstream signals can wake this stage (Check Run and Check Suite events)."""
        return bool(self.document["dependencies"])

    def to_json_text(self) -> str:
        return json.dumps(self.document, sort_keys=True, separators=(",", ":"))


def build_stage_check_run_name(stage_id: str) -> str:
    return f"{STAGE_CHECK_RUN_NAME_PREFIX}/{stage_id}"


def build_stage_signal_config(
    plan: ExecutionPlan,
    stage: NormalizedStage,
    render_context: RenderContext,
    publisher_app_id: str,
    check_run_name: str,
) -> StageSignalConfig:
    """Return the validated runtime configuration for ``stage``; raise ValueError if unsupported."""
    invocation_document = _build_invocation_document(stage, plan)
    _require_completion_evidence(stage, plan)
    evidence_documents = [_build_evidence_document(stage, spec) for spec in plan.evidence]
    gate_document = _build_gate_document(stage, plan)
    trust_policy = render_context.trust_policy
    document = {
        "schemaVersion": 1,
        "stageId": stage.id,
        "checkRunName": check_run_name,
        "publisherAppId": str(publisher_app_id),
        "trustedRoles": sorted(role.value.upper() for role in trust_policy.trusted_roles),
        "denyForks": trust_policy.fork_policy is ForkPolicy.DENY,
        "privilegedStage": bool(plan.required_secrets),
        "evidence": evidence_documents,
        "gate": gate_document,
        "invocation": invocation_document,
        "dependencies": _build_dependency_documents(stage, render_context, publisher_app_id),
        "routing": _build_routing_document(render_context),
        "handOffLabel": trust_policy.human_merge_label if render_context.remediation_policy else None,
    }
    if _GITHUB_EXPRESSION_OPENER in json.dumps(document):
        raise ValueError(
            f"Stage '{stage.id}': the stage signal configuration contains the GitHub expression "
            f"opener, which would be evaluated by Actions when stored in the workflow."
        )
    producers = tuple(dict.fromkeys(item["producedBy"] for item in evidence_documents))
    return StageSignalConfig(document=document, evidence_producers=producers)


def _build_evidence_document(stage: NormalizedStage, spec: EvidenceSpec) -> dict[str, Any]:
    supported_conditions = {
        EvidenceKind.REVIEW_RESULT: EvidenceSuccessCondition.COMPLETED,
        EvidenceKind.COMMENT_MATCH: EvidenceSuccessCondition.MATCH_FOUND,
    }
    if supported_conditions.get(spec.kind) is not spec.success_condition:
        raise ValueError(
            f"Stage '{stage.id}': GitHubPlatformRenderer V1 supports only REVIEW_RESULT with "
            f"COMPLETED and COMMENT_MATCH with MATCH_FOUND; got {spec.kind.name} with "
            f"{spec.success_condition.name}."
        )
    if not spec.correlation.head_sha:
        raise ValueError(
            f"Stage '{stage.id}': evidence must be head-bound (correlation.head_sha=True); an "
            f"unbound item could satisfy the check for a newer commit."
        )
    _require_valid_login(stage, spec.produced_by, "EvidenceSpec.produced_by")
    _require_selector_and_sha_field(stage, spec)
    return {
        "kind": spec.kind.value,
        "selector": spec.selector,
        "shaField": spec.correlation.sha_field,
        "successCondition": spec.success_condition.value,
        "producedBy": spec.produced_by,
    }


def _require_selector_and_sha_field(stage: NormalizedStage, spec: EvidenceSpec) -> None:
    tokens = spec.selector.split()
    if not tokens:
        raise ValueError(f"Stage '{stage.id}': evidence selector must not be empty.")
    if spec.kind is EvidenceKind.REVIEW_RESULT:
        if len(tokens) != 1 or spec.correlation.sha_field != _REVIEW_SUMMARY_SHA_FIELD:
            raise ValueError(
                f"Stage '{stage.id}': REVIEW_RESULT needs a single-token selector and "
                f"sha_field '{_REVIEW_SUMMARY_SHA_FIELD}'; got selector {spec.selector!r} and "
                f"sha_field {spec.correlation.sha_field!r}."
            )
        return
    if not spec.correlation.sha_field.strip() or any("=" not in token for token in tokens[1:]):
        raise ValueError(
            f"Stage '{stage.id}': COMMENT_MATCH needs a sha_field and a selector of the form "
            f"'<marker-prefix> [key=value ...]'; got {spec.selector!r}."
        )


def _build_gate_document(stage: NormalizedStage, plan: ExecutionPlan) -> dict[str, Any]:
    gate = plan.gate_disposition
    created_by, head_sha_bound = "", False
    if gate.kind is GateDispositionKind.NO_OPEN_THREADS:
        scope = gate.scope
        if scope.invocation_correlation is not None:
            raise ValueError(
                f"Stage '{stage.id}': FindingScopeSpec.invocation_correlation is not supported "
                f"by GitHubPlatformRenderer V1 (no reliable binding to review threads); ignoring "
                f"it would silently widen the scope."
            )
        _require_valid_login(stage, scope.created_by, "FindingScopeSpec.created_by")
        created_by, head_sha_bound = scope.created_by, scope.head_sha
    elif gate.kind is GateDispositionKind.EXPLICIT_PASS_MARKER:
        if not gate.selector.strip():
            raise ValueError(f"Stage '{stage.id}': EXPLICIT_PASS_MARKER needs a selector.")
    return {
        "kind": gate.kind.value,
        "selector": gate.selector,
        "createdBy": created_by,
        "headShaBound": head_sha_bound,
    }


def _build_invocation_document(stage: NormalizedStage, plan: ExecutionPlan) -> dict[str, Any]:
    invocation = plan.invocation
    if invocation.kind is not InvocationKind.PR_COMMENT:
        raise ValueError(
            f"Stage '{stage.id}': the GitHub renderer can only wire a PR_COMMENT invocation; got "
            f"{invocation.kind.name}."
        )
    body = invocation.params.get("body")
    if not isinstance(body, str) or not body.strip():
        raise ValueError(
            f"Stage '{stage.id}': a PR_COMMENT invocation needs a non-empty params['body'], the "
            f"comment that asks the backend to run; got {body!r}."
        )
    if not any(secret.alias == TRUSTED_COMMENTER_TOKEN_VARIABLE and secret.env_name
               for secret in plan.required_secrets):
        raise ValueError(
            f"Stage '{stage.id}': a PR_COMMENT invocation is posted with the "
            f"{TRUSTED_COMMENTER_TOKEN_VARIABLE} secret, but the plan declares no resolved secret "
            f"with that alias."
        )
    lease_minutes = invocation.params.get("lease_minutes", DEFAULT_LEASE_MINUTES)
    is_integer = isinstance(lease_minutes, int) and not isinstance(lease_minutes, bool)
    if not is_integer or not 1 <= lease_minutes <= MAX_LEASE_MINUTES:
        raise ValueError(
            f"Stage '{stage.id}': invocation params['lease_minutes'] must be an integer from 1 to "
            f"{MAX_LEASE_MINUTES}; got {lease_minutes!r}."
        )
    return {"kind": INVOCATION_KIND_PR_COMMENT, "body": body, "leaseMinutes": lease_minutes}


def _build_dependency_documents(
    stage: NormalizedStage, render_context: RenderContext, publisher_app_id: str
) -> list[dict[str, str]]:
    dependency_ids = tuple(dict.fromkeys(stage.dependencies))
    if not dependency_ids:
        return []
    if not _APP_ID_PATTERN.match(str(publisher_app_id)):
        raise ValueError(
            f"Stage '{stage.id}': the publisher App id must be numeric to guard the dependency "
            f"wake-up events; got {publisher_app_id!r}."
        )
    known_stage_ids = {known_stage.id for known_stage in render_context.stages}
    for dependency_id in dependency_ids:
        if not _STAGE_ID_PATTERN.match(dependency_id):
            raise ValueError(
                f"Stage '{stage.id}': dependency {dependency_id!r} is not a valid stage id."
            )
        if dependency_id == stage.id or dependency_id not in known_stage_ids:
            raise ValueError(
                f"Stage '{stage.id}': dependency '{dependency_id}' is not another active stage, so "
                f"its signal could never be evaluated."
            )
    return [
        {"stageId": dependency_id, "checkRunName": build_stage_check_run_name(dependency_id)}
        for dependency_id in dependency_ids
    ]


def _build_routing_document(render_context: RenderContext) -> dict[str, Any] | None:
    fast_path = render_context.routing_policy.fast_path
    if fast_path is None:
        return None
    return {
        "checkRunName": ROUTE_CLASSIFICATION_CHECK_RUN_NAME,
        "fastStageIds": sorted(set(fast_path.stages.fast)),
        "normalStageIds": sorted(set(fast_path.stages.normal)),
    }


def _require_completion_evidence(stage: NormalizedStage, plan: ExecutionPlan) -> None:
    if plan.evidence:
        return
    raise ValueError(
        f"Stage '{stage.id}': a PR_COMMENT invocation completes asynchronously but the plan "
        f"declares no EvidenceSpec, so nothing could prove it finished. Declare evidence."
    )


def _require_valid_login(stage: NormalizedStage, identity: str | None, field_name: str) -> None:
    if not identity or not _GITHUB_LOGIN_PATTERN.match(identity):
        raise ValueError(
            f"Stage '{stage.id}': {field_name} must be a GitHub login (optionally ending in "
            f"'[bot]') so evidence and findings can be authenticated; got {identity!r}."
        )
