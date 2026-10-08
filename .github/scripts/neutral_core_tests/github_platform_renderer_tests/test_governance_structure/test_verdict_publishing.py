"""Tests for the governance verdict published as a Check Run on the pull request head."""
from __future__ import annotations

import yaml

from stagr.core.enums import ForkPolicy

from stagr.platforms.github._governance import GOVERNANCE_CHECK_RUN_NAME

from neutral_core_tests.github_platform_renderer_tests.test_governance_structure.helpers import (
    _render_governance_to_string,
)


def _load_merge_verdict_job(fork_policy: ForkPolicy = ForkPolicy.DENY) -> dict:
    workflow = yaml.safe_load(_render_governance_to_string(fork_policy=fork_policy))
    return workflow["jobs"]["publish-merge-verdict"]


def _find_step(job: dict, step_name_start: str) -> dict:
    return next(step for step in job["steps"] if step["name"].startswith(step_name_start))


def test_governance_publishes_verdict_check_run_on_pull_request_head() -> None:
    """The verdict is a Check Run created on the PR head, so it reaches the PR whatever the trigger."""
    publish_step = _find_step(_load_merge_verdict_job(), "Publish the merge-gate verdict")
    assert f"name={GOVERNANCE_CHECK_RUN_NAME}" in publish_step["run"], "verdict Check Run must be created by name"
    assert "head_sha=${PR_HEAD_SHA}" in publish_step["run"], "verdict must be bound to the PR head commit"
    assert "github.event.check_suite.head_sha" in publish_step["env"]["PR_HEAD_SHA"], (
        "a check_suite run must publish on the suite's head commit, not the default branch"
    )
    assert publish_step["env"]["GH_TOKEN"] == "${{ steps.app-token.outputs.token }}", (
        "the verdict must be published with the Stagr App token"
    )


def test_governance_verdict_fails_closed() -> None:
    """Only a successful evaluation publishes success; any other outcome publishes failure."""
    job = _load_merge_verdict_job()
    evaluate_step = _find_step(job, "Evaluate stage result signals")
    publish_step = _find_step(job, "Publish the merge-gate verdict")
    assert evaluate_step["id"] == "evaluate", "the publishing step reads the evaluation outcome by this id"
    assert evaluate_step["continue-on-error"] is True, "a blocked evaluation must still reach the publishing step"
    assert publish_step["env"]["EVALUATION_OUTCOME"] == "${{ steps.evaluate.outcome }}"
    assert 'if [[ "${EVALUATION_OUTCOME}" == "success" ]]; then' in publish_step["run"]
    assert "else\n  verdict_conclusion=failure" in publish_step["run"], "any other outcome must publish failure"


def test_governance_verdict_unchanged_is_not_republished() -> None:
    """An unchanged verdict publishes nothing, so publishing cannot re-trigger governance forever."""
    publish_run = _find_step(_load_merge_verdict_job(), "Publish the merge-gate verdict")["run"]
    assert "--method PATCH" in publish_run, "an existing verdict Check Run must be updated in place"
    assert "nothing to publish" in publish_run, "an unchanged verdict must not be published again"
    assert 'tostring == \\"${STAGR_APP_ID}\\"' in publish_run, "only the Stagr App's Check Run may be updated"


def test_governance_job_skips_check_suites_without_pull_request() -> None:
    """A check suite on a commit that heads no PR (for example on the default branch) publishes nothing."""
    job_condition = _load_merge_verdict_job()["if"]
    assert "github.event_name == 'pull_request_target'" in job_condition
    assert "github.event.check_suite.pull_requests[0].number" in job_condition



def test_governance_job_never_publishes_for_fork_pull_requests() -> None:
    """A fork PR never reaches the App token or the publisher, under every fork policy."""
    for fork_policy in ForkPolicy:
        job_condition = _load_merge_verdict_job(fork_policy)["if"]
        assert "github.event.pull_request.head.repo.full_name == github.repository" in job_condition, (
            f"fork pull requests must not reach the publisher under {fork_policy.value}"
        )
