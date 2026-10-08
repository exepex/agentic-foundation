"""Rendered workflow structure for the backend invocation and its credential isolation (issue #205)."""
from __future__ import annotations

from stagr.core.renderers.openai_codex_backend_renderer import CODEX_REVIEW_GUIDELINES

import dataclasses
import json

from neutral_core_tests.github_platform_renderer_tests.helpers import (
    build_execution_plan,
    build_stage,
)
from neutral_core_tests.stage_signal_tests.render_helpers import (
    TRUSTED_COMMENTER_ENV_NAME,
    build_codex_plan,
    parse_workflow,
    render_codex_workflow,
    render_workflow_text,
)
from stagr.core.enums import InvocationKind, StageKind
from stagr.core.models import Invocation, SecretRef

BACKEND_SECRET_EXPRESSION = "${{ secrets.%s }}" % TRUSTED_COMMENTER_ENV_NAME
APP_TOKEN_EXPRESSION = "steps.app-token.outputs.token"
RUN_COMMAND = 'python3 -c "$STAGR_RUNTIME_SCRIPT"'


def _steps(document: dict, job_name: str = "execute") -> dict[str, dict]:
    return {step["name"]: step for step in document["jobs"][job_name]["steps"]}


def test_pr_comment_stage_replaces_both_placeholders_with_one_invoke_step() -> None:
    _, document = render_codex_workflow()
    names = list(_steps(document))
    assert names == [
        "Acquire Stagr App installation token",
        "Check eligibility",
        "Invoke backend (idempotent)",
        "Publish result signal",
    ]


def test_invoke_step_runs_the_shared_runtime_in_invoke_mode_with_only_event_data_in_env() -> None:
    _, document = render_codex_workflow()
    invoke_step = _steps(document)["Invoke backend (idempotent)"]
    assert invoke_step["run"] == RUN_COMMAND
    assert invoke_step["env"] == {
        "STAGR_MODE": "invoke",
        "STAGR_PULL_NUMBER": "${{ github.event.pull_request.number }}",
        "STAGR_EVENT_HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
        "TRUSTED_COMMENTER_TOKEN": BACKEND_SECRET_EXPRESSION,
    }
    assert invoke_step["if"] == "${{ steps.eligibility.outputs.proceed == 'true' }}", (
        "the backend is invoked only when the eligibility step said so (implicit success() also "
        "stops it after any failed earlier step)")


def test_only_the_invoke_step_holds_the_backend_secret() -> None:
    workflow_text, document = render_codex_workflow()
    assert workflow_text.count(TRUSTED_COMMENTER_ENV_NAME) == 1
    assert TRUSTED_COMMENTER_ENV_NAME not in json.dumps(document["env"])
    for job_name, job in document["jobs"].items():
        for step in job["steps"]:
            holds_secret = TRUSTED_COMMENTER_ENV_NAME in json.dumps(step)
            assert holds_secret == (step["name"] == "Invoke backend (idempotent)"), (job_name, step["name"])


def test_app_installation_token_never_reaches_the_invoke_step() -> None:
    _, document = render_codex_workflow()
    invoke_step = _steps(document)["Invoke backend (idempotent)"]
    assert APP_TOKEN_EXPRESSION not in json.dumps(invoke_step)
    assert "GH_TOKEN" not in invoke_step["env"], "the runtime derives gh's token from the backend secret"


def test_wakeup_and_sweep_jobs_hold_neither_the_backend_secret_nor_any_token_permission() -> None:
    _, document = render_codex_workflow()
    for job_name in ("reconcile", "sweep"):
        job = document["jobs"][job_name]
        assert job["permissions"] == {}
        serialized = json.dumps(job)
        assert TRUSTED_COMMENTER_ENV_NAME not in serialized and "TRUSTED_COMMENTER_TOKEN" not in serialized
        assert "invoke" not in serialized
        for step in job["steps"]:
            if step["name"] != "Acquire Stagr App installation token":
                assert step["env"]["GH_TOKEN"] == "${{ steps.app-token.outputs.token }}"
                assert step["env"]["STAGR_MODE"] in ("reconcile", "sweep")


def test_execute_job_permissions_and_publish_semantics_are_unchanged() -> None:
    _, document = render_codex_workflow()
    assert document["jobs"]["execute"]["permissions"] == {"pull-requests": "read", "contents": "read"}
    names = list(_steps(document))
    publish_step = _steps(document)["Publish result signal"]
    assert names.index("Publish result signal") == len(names) - 1
    assert publish_step["if"] == "${{ !cancelled() }}"
    assert publish_step["env"]["STAGR_MODE"] == "publish"


def test_concurrency_group_is_untouched_by_the_invocation_guard() -> None:
    _, document = render_codex_workflow()
    assert document["concurrency"]["group"] == (
        "stagr-review-${{ github.event_name == 'schedule' && 'sweep'"
        " || github.event.pull_request.number || github.event.issue.number }}"
    )
    assert document["concurrency"]["cancel-in-progress"] is False


def test_invocation_and_lease_reach_the_runtime_only_through_the_configuration_document() -> None:
    plan, stage = build_codex_plan(StageKind.SECURITY)
    params = {**plan.invocation.params, "lease_minutes": 45}
    plan = dataclasses.replace(plan, invocation=Invocation(kind=InvocationKind.PR_COMMENT, params=params))
    document = parse_workflow(render_workflow_text(plan, stage))
    configuration = json.loads(document["env"]["STAGR_STAGE_CONFIG"])
    assert configuration["invocation"] == {
        "kind": "pr_comment", "body": f"@codex security review\n\n{CODEX_REVIEW_GUIDELINES}", "leaseMinutes": 45}
    assert "@codex" not in document["env"]["STAGR_RUNTIME_SCRIPT"]
    assert "@codex" not in json.dumps(_steps(document)["Invoke backend (idempotent)"])


def test_invoke_step_carries_every_declared_secret_and_only_those() -> None:
    plan, stage = build_codex_plan()
    plan = dataclasses.replace(plan, required_secrets=plan.required_secrets + (
        SecretRef(alias="PROVIDER_API_KEY", env_name="OPENAI_API_KEY"),))
    document = parse_workflow(render_workflow_text(plan, stage))
    environment = _steps(document)["Invoke backend (idempotent)"]["env"]
    assert environment["PROVIDER_API_KEY"] == "${{ secrets.OPENAI_API_KEY }}"
    assert environment["TRUSTED_COMMENTER_TOKEN"] == BACKEND_SECRET_EXPRESSION
    assert set(environment) == {
        "STAGR_MODE", "STAGR_PULL_NUMBER", "STAGR_EVENT_HEAD_SHA",
        "PROVIDER_API_KEY", "TRUSTED_COMMENTER_TOKEN"}


def test_other_invocation_kinds_are_rejected_at_render_time() -> None:
    stage = build_stage()
    for kind in (InvocationKind.CI_COMPONENT,):
        plan = dataclasses.replace(build_execution_plan(), invocation=Invocation(kind=kind))
        try:
            render_workflow_text(plan, stage)
        except ValueError as error:
            assert "cannot run CI component None" in str(error), str(error)
            continue
        raise AssertionError(f"expected ValueError for {kind.name}")
