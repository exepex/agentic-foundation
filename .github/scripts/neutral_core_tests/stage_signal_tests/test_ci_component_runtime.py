"""CI-component runtime: the ``ci_gate`` decision and posting the findings (``post_review``)."""
from __future__ import annotations

import json
from typing import Any

from neutral_core_tests.stage_signal_tests.fake_github import REPOSITORY
from neutral_core_tests.stage_signal_tests.fixtures import (
    BASE_SHA,
    HEAD_SHA,
    OLD_HEAD_SHA,
    PULL_NUMBER,
    build_config,
    build_pull_request,
    build_review_thread,
    build_world,
)
from stagr.platforms.github.runtime import ci_component_runtime as ci_runtime
from stagr.platforms.github.runtime import stage_signal_runtime as runtime

APP_LOGIN = "stagr-demo[bot]"
APP_ACCOUNT = {"id": 4242, "login": APP_LOGIN, "type": "Bot"}
REVIEW_MARKER_SELECTOR = "stagr-review:review:v1 status=completed"
SECURITY_MARKER_SELECTOR = "stagr-review:security:v1 status=completed"
CHANGED_PATCH = "@@ -10,3 +10,4 @@ class CommentService\n context\n+added line\n-removed line\n context\n"


def _ci_config(selector: str = REVIEW_MARKER_SELECTOR, **overrides: Any) -> runtime.StageRuntimeConfig:
    return build_config(
        evidence=[{
            "kind": "comment_match",
            "selector": selector,
            "shaField": "headSha",
            "successCondition": "match_found",
            "producedBy": APP_LOGIN,
        }],
        gate={"kind": "no_open_threads", "selector": "", "createdBy": APP_LOGIN, "headShaBound": True},
        invocation={"kind": "ci_component"},
        **overrides,
    )


def _app_world(**world_arguments: Any):
    fake = build_world(**world_arguments)
    fake.authenticated_user = dict(APP_ACCOUNT)
    fake.authenticated_association = "NONE"
    fake.pull_files[PULL_NUMBER] = [
        {"filename": "src/CommentService.java", "patch": CHANGED_PATCH},
        {"filename": "README.md"},
    ]
    return fake


def _finding(path: str, line: int, title: str = "Missing owner check") -> dict[str, Any]:
    return {"path": path, "line": line, "title": title, "body": "Any user can edit any comment."}


def _post(fake, findings: list[dict[str, Any]], reviewed_head: str = HEAD_SHA, **config_overrides: Any):
    poster = ci_runtime.ReviewPoster(
        _ci_config(**config_overrides), fake, REPOSITORY, reviewed_head, json.dumps({"findings": findings})
    )
    return poster.post(runtime.ReconcileRequest(mode=ci_runtime.MODE_POST_REVIEW, pull_number=PULL_NUMBER))


def _decide(fake, **config_overrides: Any) -> tuple[runtime.ReconcileResult, ci_runtime.CiComponentGate]:
    gate = ci_runtime.CiComponentGate(_ci_config(**config_overrides), fake, REPOSITORY)
    result = gate.decide(runtime.ReconcileRequest(mode=ci_runtime.MODE_CI_GATE, pull_number=PULL_NUMBER))
    return result, gate


def test_commentable_lines_are_the_head_side_lines_of_each_hunk() -> None:
    assert ci_runtime.list_commentable_lines(CHANGED_PATCH) == [10, 11, 12]
    assert ci_runtime.list_commentable_lines("") == []
    two_hunks = "@@ -1 +1 @@\n-a\n+b\n@@ -40,2 +40,2 @@\n x\n+y\n\\ No newline at end of file\n"
    assert ci_runtime.list_commentable_lines(two_hunks) == [1, 40, 41]


def test_ci_gate_runs_the_component_for_an_eligible_head_and_hands_on_both_commits() -> None:
    result, gate = _decide(_app_world())
    assert result.action == runtime.ACTION_ELIGIBLE
    assert (gate.reviewed_pull.head_sha, gate.reviewed_pull.base_sha) == (HEAD_SHA, BASE_SHA)


def test_ci_gate_skips_after_completion_hand_off_or_for_an_ineligible_pull_request() -> None:
    fake = _app_world()
    _post(fake, [])
    assert _decide(fake)[0].reason == "completion evidence already exists for this head"
    labelled = _app_world(pull_request={**build_pull_request(), "labels": [{"name": "human-merge"}]})
    assert "handed to a human" in _decide(labelled, handOffLabel="human-merge")[0].reason
    draft = _app_world(pull_request=build_pull_request(is_draft=True))
    result, gate = _decide(draft)
    assert result.action == runtime.ACTION_SKIPPED and gate.reviewed_pull is None


def test_ci_gate_outputs_only_validated_shas() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile("r+", encoding="utf-8") as output_file:
        _, gate = _decide(_app_world())
        ci_runtime.record_ci_gate_outputs({"GITHUB_OUTPUT": output_file.name}, gate.reviewed_pull)
        ci_runtime.record_ci_gate_outputs({"GITHUB_OUTPUT": output_file.name}, None)
        assert output_file.read().splitlines() == [
            "run_backend=true", f"head_sha={HEAD_SHA}", f"base_sha={BASE_SHA}", "run_backend=false"
        ]
    malformed = _app_world(pull_request={**build_pull_request(), "base": {"sha": "main", "repo": {"id": 500}}})
    try:
        _decide(malformed)
    except runtime.GitHubApiError:
        return
    raise AssertionError("a non-SHA base must not be handed to the component job")


def test_findings_are_posted_as_one_review_then_the_marker_that_completes_the_stage() -> None:
    fake = _app_world()
    result = _post(fake, [
        _finding("src/CommentService.java", 11),
        _finding("./src/CommentService.java", 30, "Delete is unguarded"),
        _finding("src/Untouched.java", 5, "Old bug"),
    ])
    assert result.action == ci_runtime.ACTION_POSTED and result.reason == "3 finding(s)"
    (review,) = fake.reviews
    assert review["commit_id"] == HEAD_SHA and review["user"]["login"] == APP_LOGIN
    assert [(comment["path"], comment["line"]) for comment in review["comments"]] == [
        ("src/CommentService.java", 11), ("src/CommentService.java", 12)]
    assert "Reported on line 30" in review["comments"][1]["body"]
    assert "Reported on line" not in review["comments"][0]["body"]
    assert "`src/Untouched.java:5` **Old bug**" in review["body"] and "not blocking" in review["body"]
    evidence = runtime.CommentEvidenceEvaluator(_ci_config().evidence_rules)
    assert evidence.evaluate(fake.issue_comments[PULL_NUMBER], HEAD_SHA).is_present
    assert not evidence.evaluate(fake.issue_comments[PULL_NUMBER], OLD_HEAD_SHA).is_present


def test_a_clean_review_posts_only_the_marker() -> None:
    fake = _app_world()
    assert _post(fake, []).reason == "0 finding(s)"
    assert fake.reviews == []
    (marker_comment,) = fake.issue_comments[PULL_NUMBER]
    assert "0 finding(s)" in marker_comment["body"] and REVIEW_MARKER_SELECTOR.split()[0] in marker_comment["body"]


def test_nothing_is_posted_for_a_moved_head_or_twice_for_one_head() -> None:
    fake = _app_world()
    assert "head moved" in _post(fake, [_finding("README.md", 1)], reviewed_head=OLD_HEAD_SHA).reason
    assert fake.write_calls == []
    _post(fake, [])
    assert "already posted" in _post(fake, [_finding("src/CommentService.java", 11)]).reason
    assert fake.reviews == [] and len(fake.issue_comments[PULL_NUMBER]) == 1


def test_a_marker_from_anyone_but_the_app_does_not_complete_the_stage() -> None:
    forged = {"id": 1, "user": {"login": "stagr-demo", "type": "User"},
              "body": f'<!-- stagr-review:review:v1 {{"status": "completed", "headSha": "{HEAD_SHA}"}} -->'}
    _, gate = _decide(_app_world(comments=[forged]))
    assert gate.reviewed_pull is not None, "a look-alike human account must not suppress the review"


def test_the_security_marker_does_not_hide_the_review_marker() -> None:
    fake = _app_world()
    _post(fake, [])
    security_poster = ci_runtime.ReviewPoster(
        _ci_config(SECURITY_MARKER_SELECTOR, stageId="security"), fake, REPOSITORY, HEAD_SHA, '{"findings": []}'
    )
    security_poster.post(runtime.ReconcileRequest(mode=ci_runtime.MODE_POST_REVIEW, pull_number=PULL_NUMBER))
    assert len(fake.issue_comments[PULL_NUMBER]) == 2
    for selector in (REVIEW_MARKER_SELECTOR, SECURITY_MARKER_SELECTOR):
        evidence = runtime.CommentEvidenceEvaluator(_ci_config(selector).evidence_rules)
        assert evidence.evaluate(fake.issue_comments[PULL_NUMBER], HEAD_SHA).is_present, selector


def test_posted_findings_block_the_gate_until_resolved() -> None:
    fake = _app_world(threads=[build_review_thread(author_login="stagr-demo")])
    gate = runtime.GateEvaluator(_ci_config().gate_rule, fake, *REPOSITORY.split("/"))
    assert gate.evaluate(PULL_NUMBER, HEAD_SHA, ()) == runtime.CONCLUSION_BLOCKED
    fake.review_threads[PULL_NUMBER] = [build_review_thread(author_login="stagr-demo", is_resolved=True)]
    assert gate.evaluate(PULL_NUMBER, HEAD_SHA, ()) == runtime.CONCLUSION_PASS


def test_malformed_component_output_fails_the_step() -> None:
    for findings_text in ("not json", "[]", '{"findings": [{"path": "a", "line": 0, "title": "t", "body": "b"}]}',
                          '{"findings": [{"path": "a", "line": true, "title": "t", "body": "b"}]}'):
        environment = {
            "STAGR_STAGE_CONFIG": json.dumps(_config_document()),
            "STAGR_MODE": ci_runtime.MODE_POST_REVIEW,
            "GITHUB_REPOSITORY": REPOSITORY,
            "STAGR_PULL_NUMBER": str(PULL_NUMBER),
            "STAGR_REVIEWED_HEAD_SHA": HEAD_SHA,
            "STAGR_REVIEW_FINDINGS": findings_text,
        }
        fake = _app_world()
        assert ci_runtime.main(environment, fake) == 1, findings_text
        assert fake.write_calls == []


def test_too_many_findings_fail_instead_of_being_cut() -> None:
    findings = [_finding("src/CommentService.java", 11, "x" * 5000)] * ci_runtime.MAX_POSTED_FINDINGS
    parsed = ci_runtime.parse_review_findings(json.dumps({"findings": findings}))
    assert len(parsed) == ci_runtime.MAX_POSTED_FINDINGS
    assert len(parsed[0].title) == ci_runtime.MAX_FINDING_TEXT_LENGTH
    try:
        ci_runtime.parse_review_findings(json.dumps({"findings": findings + findings[:1]}))
    except ValueError as error:
        assert "more than the" in str(error)
        return
    raise AssertionError("an overlong finding list must fail, not be cut")


def test_a_finding_that_cannot_be_placed_fails_instead_of_passing_unseen() -> None:
    fake = _app_world()
    fake.pull_files[PULL_NUMBER].append({"filename": "src/Big.java", "status": "modified"})
    try:
        _post(fake, [_finding("src/Big.java", 5)])
    except ValueError as error:
        assert "src/Big.java" in str(error)
    else:
        raise AssertionError("a changed file without a patch must fail the step")
    truncated = _app_world()
    truncated.pull_files[PULL_NUMBER] = [{"filename": f"f{index}", "patch": CHANGED_PATCH}
                                         for index in range(ci_runtime.MAX_LISTED_CHANGED_FILES)]
    try:
        _post(truncated, [_finding("src/Beyond.java", 5)])
    except ValueError:
        pass
    else:
        raise AssertionError("a path missing from a cut file list must fail the step")
    removed = _app_world()
    removed.pull_files[PULL_NUMBER].append({"filename": "src/Gone.java", "status": "removed", "patch": "@@ -1 +0,0 @@\n-x\n"})
    assert _post(removed, [_finding("src/Gone.java", 1)]).action == ci_runtime.ACTION_POSTED
    assert "`src/Gone.java:1`" in removed.reviews[0]["body"] and removed.reviews[0]["comments"] == []


def test_a_failed_marker_write_is_resumed_without_a_second_review() -> None:
    fake = _app_world()
    fake.failing_write_fragments.append(f"issues/{PULL_NUMBER}/comments")
    try:
        _post(fake, [_finding("src/CommentService.java", 11)])
    except runtime.GitHubApiError:
        pass
    else:
        raise AssertionError("the injected marker failure must surface")
    assert len(fake.reviews) == 1 and not fake.issue_comments[PULL_NUMBER]
    fake.failing_write_fragments.clear()
    assert _post(fake, [_finding("src/CommentService.java", 11)]).action == ci_runtime.ACTION_POSTED
    assert len(fake.reviews) == 1, "the review of this head was already posted"
    assert len(fake.issue_comments[PULL_NUMBER]) == 1
    other_author = _app_world()
    other_author.reviews.append({"pull_number": PULL_NUMBER, "user": {"login": "stagr-demo", "type": "User"},
                                 "body": f"<!-- stagr-review-posted:review:{HEAD_SHA} -->"})
    _post(other_author, [_finding("src/CommentService.java", 11)])
    assert len(other_author.reviews) == 2, "a look-alike human's tag must not suppress the review"



def test_the_entry_point_runs_both_modes_and_rejects_others() -> None:
    fake = _app_world()
    base_environment = {
        "STAGR_STAGE_CONFIG": json.dumps(_config_document()),
        "GITHUB_REPOSITORY": REPOSITORY,
        "STAGR_PULL_NUMBER": str(PULL_NUMBER),
    }
    assert ci_runtime.main({**base_environment, "STAGR_MODE": ci_runtime.MODE_CI_GATE}, fake) == 0
    posted = ci_runtime.main({
        **base_environment,
        "STAGR_MODE": ci_runtime.MODE_POST_REVIEW,
        "STAGR_REVIEWED_HEAD_SHA": HEAD_SHA,
        "STAGR_REVIEW_FINDINGS": json.dumps({"findings": [_finding("src/CommentService.java", 10)]}),
    }, fake)
    assert posted == 0 and len(fake.reviews) == 1
    assert ci_runtime.main({**base_environment, "STAGR_MODE": "publish"}, fake) == 1


def _config_document() -> dict[str, Any]:
    config = _ci_config()
    return {
        "schemaVersion": 1,
        "stageId": config.stage_id,
        "checkRunName": config.check_run_name,
        "publisherAppId": config.publisher_app_id,
        "trustedRoles": sorted(config.trusted_roles),
        "denyForks": config.deny_forks,
        "privilegedStage": config.privileged_stage,
        "evidence": [{"kind": rule.kind, "selector": rule.selector, "shaField": rule.sha_field,
                      "successCondition": rule.success_condition, "producedBy": rule.produced_by}
                     for rule in config.evidence_rules],
        "gate": {"kind": "no_open_threads", "selector": "", "createdBy": APP_LOGIN, "headShaBound": True},
        "invocation": {"kind": "ci_component"},
        "dependencies": [],
        "routing": None,
    }
