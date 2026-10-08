"""Render-time validation: plans the GitHub runtime cannot evaluate exactly are rejected."""
from __future__ import annotations

from stagr.core.renderers.openai_codex_backend_renderer import CODEX_REVIEW_GUIDELINES

import dataclasses

from neutral_core_tests.github_platform_renderer_tests.helpers import (
    build_execution_plan,
    build_render_context,
    build_stage,
)
from neutral_core_tests.stage_signal_tests.render_helpers import build_codex_plan
from stagr.core.enums import (
    EvidenceKind,
    EvidenceSuccessCondition,
    ForkPolicy,
    GateDispositionKind,
    InvocationKind,
    StageKind,
)
from stagr.core.models import (
    GateDispositionSpec,
    Invocation,
)
from stagr.platforms.github.stage_signal_config import build_stage_signal_config

CHECK_RUN_NAME = "stagr/stage/review"


def _build(plan, stage, **context_overrides):
    context = build_render_context(stage)
    if context_overrides:
        context = dataclasses.replace(context, **context_overrides)
    return build_stage_signal_config(plan, stage, context, "99001", CHECK_RUN_NAME)


def _expect_rejection(plan, stage, expected_fragment: str, **context_overrides) -> None:
    try:
        _build(plan, stage, **context_overrides)
    except ValueError as error:
        message = str(error)
        assert stage.id in message, f"rejection must name the stage: {message}"
        assert expected_fragment in message, f"expected {expected_fragment!r} in: {message}"
        return
    raise AssertionError(f"expected ValueError containing {expected_fragment!r}")


def _replace_evidence(plan, **evidence_changes):
    (spec,) = plan.evidence
    return dataclasses.replace(plan, evidence=(dataclasses.replace(spec, **evidence_changes),))


def _replace_correlation(plan, **correlation_changes):
    (spec,) = plan.evidence
    correlation = dataclasses.replace(spec.correlation, **correlation_changes)
    return dataclasses.replace(plan, evidence=(dataclasses.replace(spec, correlation=correlation),))


def test_codex_review_plan_produces_the_expected_runtime_document() -> None:
    plan, stage = build_codex_plan(StageKind.REVIEW)
    config = _build(plan, stage)
    assert config.document == {
        "schemaVersion": 1, "stageId": "review", "checkRunName": CHECK_RUN_NAME,
        "publisherAppId": "99001", "trustedRoles": ["OWNER"], "denyForks": True,
        "privilegedStage": True,
        "evidence": [{"kind": "review_result", "selector": "codex-pull-request-review-summary",
                      "shaField": "review_summary_sha", "successCondition": "completed",
                      "producedBy": "chatgpt-codex-connector[bot]"}],
        "gate": {"kind": "no_open_threads", "selector": "",
                 "createdBy": "chatgpt-codex-connector[bot]", "headShaBound": True},
        "invocation": {"kind": "pr_comment", "body": f"@codex review\n\n{CODEX_REVIEW_GUIDELINES}",
                       "leaseMinutes": 30},
        "dependencies": [], "routing": None, "handOffLabel": None,
    }
    assert config.evidence_producers == ("chatgpt-codex-connector[bot]",)


def test_fork_policy_allow_unprivileged_disables_deny_forks() -> None:
    plan, stage = build_codex_plan()
    context = build_render_context(stage)
    allowed = dataclasses.replace(
        context.trust_policy, fork_policy=ForkPolicy.ALLOW_UNPRIVILEGED)
    assert _build(plan, stage, trust_policy=allowed).document["denyForks"] is False


def test_evidence_kinds_beyond_comment_based_are_rejected() -> None:
    plan, stage = build_codex_plan()
    for kind, condition in ((EvidenceKind.CHECK_RESULT, EvidenceSuccessCondition.SUCCESS),
                            (EvidenceKind.WORKFLOW_RESULT, EvidenceSuccessCondition.SUCCESS)):
        _expect_rejection(_replace_evidence(plan, kind=kind, success_condition=condition), stage,
                          "supports only REVIEW_RESULT")


def test_mismatched_success_condition_is_rejected() -> None:
    plan, stage = build_codex_plan()
    _expect_rejection(_replace_evidence(plan, success_condition=EvidenceSuccessCondition.MATCH_FOUND),
                      stage, "supports only REVIEW_RESULT")


def test_evidence_that_is_not_head_bound_is_rejected() -> None:
    plan, stage = build_codex_plan()
    _expect_rejection(_replace_correlation(plan, head_sha=False), stage, "head-bound")


def test_comment_evidence_without_a_producer_is_rejected() -> None:
    plan, stage = build_codex_plan()
    _expect_rejection(_replace_evidence(plan, produced_by=None), stage, "produced_by")


def test_producer_that_is_not_a_plain_github_login_is_rejected() -> None:
    plan, stage = build_codex_plan()
    for hostile in ("bot' || true || '", "a b", "x]", "${{ secrets.X }}", "-lead", ""):
        _expect_rejection(_replace_evidence(plan, produced_by=hostile), stage, "produced_by")


def test_review_result_needs_the_known_sha_field_and_a_single_token_selector() -> None:
    plan, stage = build_codex_plan()
    _expect_rejection(_replace_correlation(plan, sha_field="headSha"), stage, "review_summary_sha")
    _expect_rejection(_replace_evidence(plan, selector="two tokens"), stage, "single-token")


def test_comment_match_selector_predicates_must_be_key_value_pairs() -> None:
    plan, stage = build_codex_plan(StageKind.SECURITY)
    _expect_rejection(_replace_evidence(plan, selector="codex-security-review:v1 completed"),
                      stage, "COMMENT_MATCH needs")
    _expect_rejection(_replace_correlation(plan, sha_field=" "), stage, "COMMENT_MATCH needs")


def test_selector_containing_the_expression_opener_is_rejected() -> None:
    plan, stage = build_codex_plan(StageKind.SECURITY)
    _expect_rejection(_replace_evidence(plan, selector="marker key=${{x}}"), stage, "expression")


def test_invocation_correlation_is_rejected_rather_than_ignored() -> None:
    plan, stage = build_codex_plan()
    scope = dataclasses.replace(plan.gate_disposition.scope, invocation_correlation="run-1")
    gate = dataclasses.replace(plan.gate_disposition, scope=scope)
    _expect_rejection(dataclasses.replace(plan, gate_disposition=gate), stage, "invocation_correlation")


def test_finding_author_must_be_a_github_login() -> None:
    plan, stage = build_codex_plan()
    scope = dataclasses.replace(plan.gate_disposition.scope, created_by="bad login")
    gate = dataclasses.replace(plan.gate_disposition, scope=scope)
    _expect_rejection(dataclasses.replace(plan, gate_disposition=gate), stage, "created_by")


def test_plan_without_evidence_is_rejected() -> None:
    stage = build_stage()
    plan = dataclasses.replace(build_execution_plan(), evidence=())
    _expect_rejection(plan, stage, "no EvidenceSpec")


def test_invocation_kinds_other_than_pr_comment_are_rejected() -> None:
    stage = build_stage()
    for kind in (InvocationKind.CI_COMPONENT,):
        plan = dataclasses.replace(build_execution_plan(), invocation=Invocation(kind=kind))
        _expect_rejection(plan, stage, "cannot run CI component None")


def test_explicit_pass_marker_requires_a_selector() -> None:
    stage = build_stage()
    gate = GateDispositionSpec(kind=GateDispositionKind.EXPLICIT_PASS_MARKER, selector=" ")
    plan = dataclasses.replace(build_execution_plan(), gate_disposition=gate)
    _expect_rejection(plan, stage, "EXPLICIT_PASS_MARKER")


def test_two_evidence_specs_from_one_producer_yield_a_single_producer() -> None:
    plan, stage = build_codex_plan(StageKind.SECURITY)
    review_plan, _ = build_codex_plan(StageKind.REVIEW)
    combined = dataclasses.replace(plan, evidence=plan.evidence + review_plan.evidence)
    assert _build(combined, stage).evidence_producers == ("chatgpt-codex-connector[bot]",)
