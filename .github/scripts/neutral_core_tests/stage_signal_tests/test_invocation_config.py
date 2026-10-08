"""Render-time and run-time validation of the invocation and its lease (issue #205)."""
from __future__ import annotations

from stagr.core.renderers.openai_codex_backend_renderer import CODEX_REVIEW_GUIDELINES

import dataclasses
import json

from neutral_core_tests.github_platform_renderer_tests.helpers import build_render_context
from neutral_core_tests.stage_signal_tests.fixtures import build_config_document
from neutral_core_tests.stage_signal_tests.render_helpers import build_codex_plan
from stagr.core.enums import InvocationKind
from stagr.core.models import Invocation, SecretRef
from stagr.platforms.github.runtime import stage_signal_runtime as runtime
from stagr.platforms.github.stage_signal_config import build_stage_signal_config


def _build_document(plan, stage) -> dict:
    config = build_stage_signal_config(plan, stage, build_render_context(stage), "99001", "stagr/stage/review")
    return config.document


def _plan_with_params(plan, **param_changes):
    params = {**plan.invocation.params, **param_changes}
    return dataclasses.replace(plan, invocation=Invocation(kind=InvocationKind.PR_COMMENT, params=params))


def _expect_render_rejection(plan, stage, expected_fragment: str) -> None:
    try:
        _build_document(plan, stage)
    except ValueError as error:
        assert stage.id in str(error) and expected_fragment in str(error), str(error)
        return
    raise AssertionError(f"expected ValueError containing {expected_fragment!r}")


def _expect_runtime_rejection(invocation_document, expected_fragment: str) -> None:
    text = json.dumps(build_config_document(invocation=invocation_document))
    try:
        runtime.StageRuntimeConfig.from_json_text(text)
    except runtime.RuntimeConfigError as error:
        assert expected_fragment in str(error), str(error)
        return
    raise AssertionError(f"expected RuntimeConfigError containing {expected_fragment!r}")


# ---- render time: lease_minutes ----


def test_lease_defaults_to_thirty_minutes_when_the_backend_does_not_set_one() -> None:
    plan, stage = build_codex_plan()
    assert "lease_minutes" not in plan.invocation.params
    assert _build_document(plan, stage)["invocation"] == {
        "kind": "pr_comment", "body": f"@codex review\n\n{CODEX_REVIEW_GUIDELINES}",
        "leaseMinutes": runtime.DEFAULT_LEASE_MINUTES}
    assert runtime.DEFAULT_LEASE_MINUTES == 30


def test_backend_defined_lease_minutes_is_rendered_including_the_bounds() -> None:
    plan, stage = build_codex_plan()
    for minutes in (1, 45, runtime.MAX_LEASE_MINUTES):
        document = _build_document(_plan_with_params(plan, lease_minutes=minutes), stage)
        assert document["invocation"]["leaseMinutes"] == minutes


def test_lease_minutes_that_is_not_a_sane_positive_integer_is_rejected() -> None:
    plan, stage = build_codex_plan()
    for invalid in (0, -5, runtime.MAX_LEASE_MINUTES + 1, 10**9, True, False, "30", 30.0, 0.5, None, [30]):
        _expect_render_rejection(_plan_with_params(plan, lease_minutes=invalid), stage, "lease_minutes")


# ---- render time: body and credential ----


def test_pr_comment_invocation_needs_a_non_empty_text_body() -> None:
    plan, stage = build_codex_plan()
    for invalid in ("", "   \n", None, 7, ["@codex review"]):
        _expect_render_rejection(_plan_with_params(plan, body=invalid), stage, "params['body']")
    params_without_body = {key: value for key, value in plan.invocation.params.items() if key != "body"}
    no_body_plan = dataclasses.replace(
        plan, invocation=Invocation(kind=InvocationKind.PR_COMMENT, params=params_without_body))
    _expect_render_rejection(no_body_plan, stage, "params['body']")


def test_invocation_body_containing_the_expression_opener_is_rejected() -> None:
    plan, stage = build_codex_plan()
    _expect_render_rejection(_plan_with_params(plan, body="@codex ${{ secrets.X }}"), stage, "expression")


def test_pr_comment_invocation_needs_the_resolved_trusted_commenter_secret() -> None:
    plan, stage = build_codex_plan()
    for secrets in (
        (),
        (SecretRef(alias="OTHER_TOKEN", env_name="OTHER"),),
        (SecretRef(alias="TRUSTED_COMMENTER_TOKEN"),),
        (SecretRef(alias="TRUSTED_COMMENTER_TOKEN", env_name=""),),
    ):
        _expect_render_rejection(dataclasses.replace(plan, required_secrets=secrets), stage, "TRUSTED_COMMENTER_TOKEN")


# ---- run time: the embedded configuration is re-validated ----


def test_runtime_rejects_an_invocation_it_cannot_perform_exactly() -> None:
    valid = {"kind": "pr_comment", "body": "@codex review", "leaseMinutes": 30}
    _expect_runtime_rejection({**valid, "kind": "webhook"}, "Unsupported invocation kind")
    _expect_runtime_rejection({**valid, "body": " "}, "non-empty body")
    _expect_runtime_rejection({**valid, "body": 3}, "non-empty body")
    for invalid in (0, -1, runtime.MAX_LEASE_MINUTES + 1, True, "30", 1.5, None):
        _expect_runtime_rejection({**valid, "leaseMinutes": invalid}, "leaseMinutes")
    _expect_runtime_rejection({"kind": "pr_comment", "body": "x"}, "Invalid STAGR_STAGE_CONFIG")
    _expect_runtime_rejection("pr_comment", "Invalid STAGR_STAGE_CONFIG")


def test_runtime_rejects_a_document_without_an_invocation_rule() -> None:
    _expect_runtime_rejection(None, "Invalid STAGR_STAGE_CONFIG")
    document = build_config_document()
    del document["invocation"]
    try:
        runtime.StageRuntimeConfig.from_json_text(json.dumps(document))
    except runtime.RuntimeConfigError as error:
        assert "Invalid STAGR_STAGE_CONFIG" in str(error), str(error)
        return
    raise AssertionError("expected RuntimeConfigError")
