"""The ``codex-api`` backend: its plan, and the stage workflow the GitHub renderer builds from it."""
from __future__ import annotations

import dataclasses
import json

from neutral_core_tests.github_platform_renderer_tests.helpers import (
    TEST_PUBLISHER_APP_ID,
    TEST_PUBLISHER_PRIVATE_KEY_SECRET,
    build_render_context,
    build_stage,
)
from neutral_core_tests.stage_signal_tests.render_helpers import build_codex_plan, parse_workflow
from stagr.core.enums import EvidenceKind, InvocationKind, StageGate, StageKind
from stagr.core.models import RemediationPolicy, SecretRef
from stagr.core.publisher import PUBLISHER_IDENTITY
from stagr.core.renderers.openai_codex_api_backend_renderer import (
    CODEX_REVIEW_COMPONENT,
    OpenAICodexApiBackendRenderer,
)
from stagr.core.renderers.openai_codex_backend_renderer import CODEX_REVIEW_GUIDELINES
from stagr.platforms.github.action_pins import CHECKOUT_ACTION_REF, CODEX_ACTION_REF, CODEX_CLI_VERSION
from stagr.platforms.github.renderer import GitHubPlatformRenderer
from stagr.platforms.github.runtime import stage_signal_runtime as runtime

APP_SLUG = "stagr-demo"
OPENAI_SECRET_NAME = "TEAM_OPENAI_KEY"


def _api_plan(stage_kind: StageKind = StageKind.REVIEW, model: str | None = None):
    stage = dataclasses.replace(
        build_stage(stage_id=stage_kind.value), kind=stage_kind, backend="codex-api", model=model
    )
    plan = OpenAICodexApiBackendRenderer().render(stage)
    resolved = tuple(SecretRef(alias=secret.alias, env_name=OPENAI_SECRET_NAME) for secret in plan.required_secrets)
    return dataclasses.replace(plan, required_secrets=resolved), stage


def _render(stage_kind: StageKind = StageKind.REVIEW, app_slug: str | None = APP_SLUG,
            remediation: bool = False, model: str | None = None):
    plan, stage = _api_plan(stage_kind, model)
    context = build_render_context(stage)
    if remediation:
        context = dataclasses.replace(
            context, remediation_policy=RemediationPolicy("anthropic", 5, "ANTHROPIC_API_KEY")
        )
    renderer = GitHubPlatformRenderer(TEST_PUBLISHER_APP_ID, TEST_PUBLISHER_PRIVATE_KEY_SECRET, app_slug)
    stage_render = renderer.render_stage(plan, stage, context)
    return stage_render, parse_workflow(stage_render.artifact.content)


def _step(job: dict, name_start: str) -> dict:
    return next(step for step in job["steps"] if step["name"].startswith(name_start))


def test_codex_api_plan_runs_codex_as_a_ci_component_and_proves_completion_per_stage() -> None:
    for stage_kind in (StageKind.REVIEW, StageKind.SECURITY):
        plan, stage = _api_plan(stage_kind)
        params = plan.invocation.params
        assert plan.invocation.kind is InvocationKind.CI_COMPONENT
        assert params["component"] == CODEX_REVIEW_COMPONENT
        assert params["credential_alias"] == "OPENAI_API_KEY"
        assert CODEX_REVIEW_GUIDELINES in params["prompt"]
        assert json.loads(params["output_schema"])["required"] == ["findings"]
        (evidence,) = plan.evidence
        assert evidence.kind is EvidenceKind.COMMENT_MATCH and evidence.correlation.head_sha
        assert evidence.selector == f"stagr-review:{stage.id}:v1 status=completed"
        assert evidence.produced_by == PUBLISHER_IDENTITY == plan.gate_disposition.scope.created_by
    assert "security vulnerabilities" in _api_plan(StageKind.SECURITY)[0].invocation.params["prompt"]
    assert _api_plan(model="gpt-5-codex")[0].invocation.params["model"] == "gpt-5-codex"


def test_codex_api_backend_refuses_other_stage_kinds_and_advisory_gates() -> None:
    renderer = OpenAICodexApiBackendRenderer()
    for stage in (
        dataclasses.replace(build_stage(), kind=StageKind.TEST, backend="codex-api"),
        dataclasses.replace(build_stage(gate=StageGate.NON_BLOCKING), backend="codex-api"),
    ):
        try:
            renderer.render(stage)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {stage}")


def test_the_app_posts_the_findings_so_its_account_is_the_finding_author() -> None:
    stage_render, document = _render()
    assert stage_render.result_spec.finding_author == f"{APP_SLUG}[bot]"
    config = runtime.StageRuntimeConfig.from_json_text(document["env"]["STAGR_STAGE_CONFIG"])
    assert config.invocation_rule.kind == runtime.INVOCATION_KIND_CI_COMPONENT
    assert [rule.produced_by for rule in config.evidence_rules] == [f"{APP_SLUG}[bot]"]
    assert f"'{APP_SLUG}[bot]'" in document["jobs"]["reconcile"]["if"]


def test_without_the_app_slug_the_stage_is_refused_at_render_time() -> None:
    try:
        _render(app_slug=None)
    except ValueError as error:
        assert "platform.publisher.app_slug" in str(error), str(error)
        return
    raise AssertionError("expected ValueError naming platform.publisher.app_slug")


def test_no_job_holds_both_the_app_token_and_the_openai_key() -> None:
    _, document = _render()
    jobs = document["jobs"]
    assert list(jobs) == ["execute", "review", "publish", "reconcile", "sweep"]
    key_expression = f"${{{{ secrets.{OPENAI_SECRET_NAME} }}}}"
    for name, job in jobs.items():
        job_text = json.dumps(job)
        holds_key = key_expression in job_text
        holds_app_token = "create-github-app-token" in job_text
        assert not (holds_key and holds_app_token), name
        assert holds_key == (name == "review"), name
    review = jobs["review"]
    assert review["permissions"] == {"contents": "read"}
    checkout = _step(review, "Check out")
    assert checkout["uses"] == CHECKOUT_ACTION_REF.split()[0]
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["with"]["ref"] == "${{ needs.execute.outputs.head_sha }}"
    codex = _step(review, "Review the change")["with"]
    assert _step(review, "Review the change")["uses"] == CODEX_ACTION_REF.split()[0]
    assert (codex["sandbox"], codex["safety-strategy"]) == ("read-only", "drop-sudo")
    assert codex["codex-version"] == CODEX_CLI_VERSION, "the CLI is pinned like the action"
    assert json.loads(codex["codex-args"]) == ["--config", "project_doc_max_bytes=0"], (
        "the change under review must not instruct its reviewer through AGENTS.md"
    )
    assert codex["prompt"].endswith(
        "Base commit: ${{ needs.execute.outputs.base_sha }}\nHead commit: ${{ needs.execute.outputs.head_sha }}"
    )
    assert json.loads(codex["output-schema"])["required"] == ["findings"]
    for job_name in ("execute", "publish"):
        assert jobs[job_name]["permissions"] == {}


def test_the_component_runs_only_when_the_gate_says_so_and_publish_reports_every_outcome() -> None:
    _, document = _render()
    jobs = document["jobs"]
    assert jobs["review"]["if"] == "${{ needs.execute.outputs.run_backend == 'true' }}"
    assert _step(jobs["execute"], "Decide")["env"]["STAGR_MODE"] == "ci_gate"
    assert _step(jobs["execute"], "Decide")["run"] == 'python3 -c "$STAGR_CI_RUNTIME_SCRIPT"'
    publish = jobs["publish"]
    assert publish["needs"] == ["execute", "review"]
    assert "needs.execute.result != 'skipped'" in publish["if"]
    post = _step(publish, "Post the review findings")
    assert post["if"] == "${{ needs.review.result == 'success' }}"
    assert post["env"]["STAGR_REVIEW_FINDINGS"] == "${{ needs.review.outputs.findings }}"
    status = _step(publish, "Publish result signal")["env"]["STAGR_JOB_STATUS"]
    assert "needs.review.result == 'failure'" in status and "needs.execute.result == 'failure'" in status


def test_the_action_admits_the_app_and_with_remediation_the_fixing_agent() -> None:
    assert _step(_render()[1]["jobs"]["review"], "Review")["with"]["allow-bot-users"] == APP_SLUG
    with_remediation = _step(_render(remediation=True)[1]["jobs"]["review"], "Review")["with"]
    assert with_remediation["allow-bot-users"] == f"{APP_SLUG},claude"
    assert _step(_render(model="gpt-5-codex")[1]["jobs"]["review"], "Review")["with"]["model"] == "gpt-5-codex"


def test_only_ci_component_stages_embed_the_ci_runtime_and_both_scripts_stay_small() -> None:
    _, document = _render()
    for script_name in ("STAGR_RUNTIME_SCRIPT", "STAGR_CI_RUNTIME_SCRIPT"):
        script = document["env"][script_name]
        assert "${{" not in script and len(script.encode("utf-8")) < 128 * 1024 // 2, script_name
    plan, stage = build_codex_plan()
    comment_stage = GitHubPlatformRenderer(TEST_PUBLISHER_APP_ID, TEST_PUBLISHER_PRIVATE_KEY_SECRET, APP_SLUG)
    comment_document = parse_workflow(comment_stage.render_stage(plan, stage, build_render_context(stage)).artifact.content)
    assert "STAGR_CI_RUNTIME_SCRIPT" not in comment_document["env"]


def test_a_ci_component_the_renderer_cannot_run_or_validate_is_refused() -> None:
    plan, stage = _api_plan()
    renderer = GitHubPlatformRenderer(TEST_PUBLISHER_APP_ID, TEST_PUBLISHER_PRIVATE_KEY_SECRET, APP_SLUG)
    params = dict(plan.invocation.params)
    for broken_params, expected in (
        ({**params, "component": "other"}, "cannot run CI component"),
        ({**params, "prompt": " "}, "non-empty prompt"),
        ({**params, "prompt": "Review ${{ secrets.X }}"}, "expression opener"),
        ({**params, "output_schema": "[]"}, "output_schema"),
        ({**params, "credential_alias": "OTHER_KEY"}, "OTHER_KEY"),
        ({**params, "model": "gpt 5; rm"}, "plain model name"),
    ):
        broken = dataclasses.replace(plan, invocation=dataclasses.replace(plan.invocation, params=broken_params))
        try:
            renderer.render_stage(broken, stage, build_render_context(stage))
        except ValueError as error:
            assert expected in str(error), (expected, str(error))
            continue
        raise AssertionError(f"expected ValueError containing {expected!r}")
