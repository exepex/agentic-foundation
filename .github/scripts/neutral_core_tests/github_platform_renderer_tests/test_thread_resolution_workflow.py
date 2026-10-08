"""Tests for the workflow that resolves outdated review threads written by review backends."""
from __future__ import annotations

import dataclasses

import yaml

from stagr.core.enums import AuthorRole, StageResultSignalKind
from stagr.core.models import StageResultProvenance, StageResultSpec

from neutral_core_tests.github_platform_renderer_tests.helpers import (
    TEST_PUBLISHER_APP_ID,
    TRUSTED_COMMENTER_ENV_NAME,
    build_execution_plan,
    build_render_context,
    build_renderer,
    build_stage,
)

CODEX_BOT_LOGIN = "chatgpt-codex-connector"


def _build_result_spec(finding_author: str | None) -> StageResultSpec:
    return StageResultSpec(
        stage_id="review",
        signal_kind=StageResultSignalKind.CHECK_RUN,
        signal_selector="stagr/stage/review",
        provenance=StageResultProvenance(publisher_identity=TEST_PUBLISHER_APP_ID),
        finding_author=finding_author,
    )


def _load_resolution_job(*finding_authors: str) -> dict:
    artifact = build_renderer().render_thread_resolution(
        tuple(_build_result_spec(author) for author in finding_authors),
        build_render_context(build_stage()),
        TRUSTED_COMMENTER_ENV_NAME,
    )
    return yaml.safe_load(artifact.content)["jobs"]["resolve-outdated-threads"]


def test_codex_stage_declares_its_bot_as_finding_author() -> None:
    """A Codex review stage hands its bot login to the thread-resolution workflow."""
    stage = build_stage()
    stage_render = build_renderer().render_stage(build_execution_plan(), stage, build_render_context(stage))
    assert stage_render.result_spec.finding_author == f"{CODEX_BOT_LOGIN}[bot]"


def test_thread_resolution_artifact_path_and_absence_without_finding_authors() -> None:
    """The workflow is written only when a stage reads review threads."""
    renderer = build_renderer()
    render_context = build_render_context(build_stage())
    artifact = renderer.render_thread_resolution(
        (_build_result_spec(f"{CODEX_BOT_LOGIN}[bot]"),), render_context, TRUSTED_COMMENTER_ENV_NAME
    )
    assert artifact.path == ".github/workflows/resolve-outdated-threads.yml"
    assert renderer.render_thread_resolution(
        (_build_result_spec(None),), render_context, TRUSTED_COMMENTER_ENV_NAME
    ) is None


def test_thread_resolution_resolves_only_review_backend_threads() -> None:
    """Thread authors are the GraphQL logins of the review backends, without the [bot] suffix, once each."""
    step = _load_resolution_job(f"{CODEX_BOT_LOGIN}[bot]", f"{CODEX_BOT_LOGIN}[bot]")["steps"][0]
    assert step["env"]["THREAD_AUTHOR_LOGINS"] == f'["{CODEX_BOT_LOGIN}"]'
    assert "select(.isResolved == false and .isOutdated == true)" in step["run"]
    assert "all(.comments.nodes[];" in step["run"], "a thread with any other author must be left alone"


def test_thread_resolution_uses_the_platform_token_by_name_only() -> None:
    """The resolving token is the platform token secret; the workflow token gets no permissions."""
    artifact = build_renderer().render_thread_resolution(
        (_build_result_spec(f"{CODEX_BOT_LOGIN}[bot]"),), build_render_context(build_stage()), "MY_PAT"
    )
    workflow = yaml.safe_load(artifact.content)
    assert workflow["permissions"] == {}
    assert workflow["jobs"]["resolve-outdated-threads"]["steps"][0]["env"]["GH_TOKEN"] == "${{ secrets.MY_PAT }}"


def test_thread_resolution_only_for_trusted_same_repository_pull_requests() -> None:
    """Forks and untrusted authors never reach the token; a stale event resolves nothing."""
    job = _load_resolution_job(f"{CODEX_BOT_LOGIN}[bot]")
    assert "github.event.pull_request.head.repo.full_name == github.repository" in job["if"]
    assert "contains(fromJSON('[\"OWNER\"]'), github.event.pull_request.author_association)" in job["if"]
    assert 'if [[ "${current_head_sha}" != "${EVENT_HEAD_SHA}" ]]' in job["steps"][0]["run"]


def test_thread_resolution_follows_the_trust_policy_roles() -> None:
    """The trusted associations come from the trust policy, not a fixed list."""
    stage = build_stage()
    render_context = build_render_context(stage)
    wider_trust = dataclasses.replace(
        render_context.trust_policy,
        trusted_roles=(AuthorRole.OWNER, AuthorRole.MEMBER),
    )
    artifact = build_renderer().render_thread_resolution(
        (_build_result_spec(f"{CODEX_BOT_LOGIN}[bot]"),),
        dataclasses.replace(render_context, trust_policy=wider_trust),
        TRUSTED_COMMENTER_ENV_NAME,
    )
    assert "fromJSON('[\"MEMBER\", \"OWNER\"]')" in artifact.content


THREAD_RESOLUTION_WORKFLOW_TESTS = (
    test_codex_stage_declares_its_bot_as_finding_author,
    test_thread_resolution_artifact_path_and_absence_without_finding_authors,
    test_thread_resolution_resolves_only_review_backend_threads,
    test_thread_resolution_uses_the_platform_token_by_name_only,
    test_thread_resolution_only_for_trusted_same_repository_pull_requests,
    test_thread_resolution_follows_the_trust_policy_roles,
)
