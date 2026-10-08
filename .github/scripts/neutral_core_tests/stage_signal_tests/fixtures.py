"""Builders for stage signal runtime tests, in the shapes the real GitHub APIs return."""
from __future__ import annotations

import json
from typing import Any

from neutral_core_tests.stage_signal_tests.fake_github import (
    PUBLISHER_APP_ID,
    REPOSITORY,
    FakeGitHubApi,
)
from stagr.platforms.github.runtime import stage_signal_runtime as runtime

STAGE_ID = "review"
CHECK_RUN_NAME = "stagr/stage/review"
CODEX_BOT = "chatgpt-codex-connector[bot]"
HEAD_SHA = "3f2c9d1e8b7a4c6d5e0f1a2b3c4d5e6f7a8b9c0d"
OLD_HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
BASE_SHA = "89abcdef0123456789abcdef0123456789abcdef"
PULL_NUMBER = 7
BASE_REPOSITORY_ID = 500


def review_evidence_rule() -> dict[str, Any]:
    return {
        "kind": "review_result",
        "selector": "codex-pull-request-review-summary",
        "shaField": "review_summary_sha",
        "successCondition": "completed",
        "producedBy": CODEX_BOT,
    }


def security_evidence_rule() -> dict[str, Any]:
    return {
        "kind": "comment_match",
        "selector": "codex-security-review:v1 status=completed",
        "shaField": "headSha",
        "successCondition": "match_found",
        "producedBy": CODEX_BOT,
    }


def no_open_threads_gate(head_sha_bound: bool = True) -> dict[str, Any]:
    return {"kind": "no_open_threads", "selector": "", "createdBy": CODEX_BOT,
            "headShaBound": head_sha_bound}


def always_pass_gate() -> dict[str, Any]:
    return {"kind": "always_pass", "selector": "always", "createdBy": "", "headShaBound": False}


def build_config_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schemaVersion": 1,
        "stageId": STAGE_ID,
        "checkRunName": CHECK_RUN_NAME,
        "publisherAppId": PUBLISHER_APP_ID,
        "trustedRoles": ["OWNER"],
        "denyForks": True,
        "privilegedStage": True,
        "evidence": [review_evidence_rule()],
        "gate": no_open_threads_gate(),
        "invocation": {"kind": "pr_comment", "body": "@codex review", "leaseMinutes": 30},
        "dependencies": [],
        "routing": None,
    }
    document.update(overrides)
    return document


def build_config(**overrides: Any) -> runtime.StageRuntimeConfig:
    return runtime.StageRuntimeConfig.from_json_text(json.dumps(build_config_document(**overrides)))


def build_pull_request(
    number: int = PULL_NUMBER,
    head_sha: str = HEAD_SHA,
    author_association: str = "OWNER",
    state: str = "open",
    is_fork: bool = False,
    is_draft: bool = False,
) -> dict[str, Any]:
    return {
        "number": number,
        "state": state,
        "draft": is_draft,
        "author_association": author_association,
        "head": {"sha": head_sha, "repo": {"id": 900 + number if is_fork else BASE_REPOSITORY_ID}},
        "base": {"sha": BASE_SHA, "repo": {"id": BASE_REPOSITORY_ID}},
    }


def build_summary_body(
    code_review_status: str = "Completed",
    code_review_sha: str = HEAD_SHA[:7],
    security_status: str = "completed",
    security_sha: str = HEAD_SHA,
) -> str:
    """The Codex review-summary comment, mirroring the real one posted on PR #235."""
    marker = {
        "blockingSeverityThreshold": "P0",
        "headSha": security_sha,
        "mergeGateEnabled": False,
        "pullRequestNumber": PULL_NUMBER,
        "repository": REPOSITORY,
        "status": security_status,
    }
    return (
        "<!-- codex-pull-request-review-summary -->\n"
        f"<!-- codex-security-review:v1 {json.dumps(marker, separators=(',', ':'))} -->\n"
        "## Codex Review Summary\n\n"
        "This comment shows the latest Codex review activity on this pull request.\n\n"
        "| Review | Status | Commit | Review trigger |\n"
        "| --- | --- | --- | --- |\n"
        f"| \U0001F4DD **Code Review** | ✅ **{code_review_status}** "
        '<relative-time datetime="2026-09-29T15:27:40.735426Z">2026-09-29T15:27:40Z</relative-time> '
        f"| `{code_review_sha}` | Manual request |\n"
        "| \U0001F512 **Security Review** | ✅ **Completed** "
        f"| `{security_sha[:7]}` | PR opened |\n"
    )


def build_issue_comment(
    body: str, comment_id: int = 1, login: str = CODEX_BOT, user_type: str = "Bot"
) -> dict[str, Any]:
    return {"id": comment_id, "body": body, "user": {"login": login, "type": user_type}}


def build_review_thread(
    is_resolved: bool = False,
    author_login: str = "chatgpt-codex-connector",
    author_type: str = "Bot",
    review_commit: str | None = HEAD_SHA,
) -> dict[str, Any]:
    """A GraphQL review thread. GraphQL reports Bot logins WITHOUT the ``[bot]`` suffix."""
    review = {"commit": {"oid": review_commit}} if review_commit else None
    return {
        "isResolved": is_resolved,
        "comments": {"nodes": [{
            "author": {"__typename": author_type, "login": author_login},
            "pullRequestReview": review,
        }]},
    }


def build_existing_check_run(
    state: str,
    conclusion: str,
    head_sha: str = HEAD_SHA,
    check_run_id: int = 42,
    app_id: str = PUBLISHER_APP_ID,
    name: str = CHECK_RUN_NAME,
) -> dict[str, Any]:
    signal = runtime.StageSignal(state, conclusion)
    check_run: dict[str, Any] = {
        "id": check_run_id,
        "app": {"id": int(app_id)},
        "name": name,
        "head_sha": head_sha,
        "status": signal.native_status,
        "output": {
            "title": "existing",
            "summary": runtime.serialize_signal_payload(STAGE_ID, head_sha, signal),
        },
    }
    if signal.native_conclusion is not None:
        check_run["conclusion"] = signal.native_conclusion
    return check_run


def build_world(
    comments: list[dict[str, Any]] | None = None,
    threads: list[dict[str, Any]] | None = None,
    pull_request: dict[str, Any] | None = None,
) -> FakeGitHubApi:
    """A repository with one open, trusted pull request (number 7) at ``HEAD_SHA``."""
    fake = FakeGitHubApi()
    fake.add_pull_request(pull_request or build_pull_request())
    fake.issue_comments[PULL_NUMBER] = comments or []
    fake.review_threads[PULL_NUMBER] = threads or []
    return fake


def completed_review_comments() -> list[dict[str, Any]]:
    return [build_issue_comment(build_summary_body())]


def build_reconciler(fake: FakeGitHubApi, **config_overrides: Any) -> runtime.StageReconciler:
    return runtime.StageReconciler(build_config(**config_overrides), fake, REPOSITORY, None)


def request(mode: str, pull_number: int = PULL_NUMBER, **overrides: Any) -> runtime.ReconcileRequest:
    return runtime.ReconcileRequest(mode=mode, pull_number=pull_number, **overrides)
