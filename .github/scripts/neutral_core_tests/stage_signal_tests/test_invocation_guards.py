"""Completion guard, in-flight lease and invocation posting (issue #205), against the fake GitHub."""
from __future__ import annotations

import re
from datetime import timedelta

from neutral_core_tests.stage_signal_tests.fake_github import COMMENTER_USER
from neutral_core_tests.stage_signal_tests.fixtures import (
    HEAD_SHA,
    OLD_HEAD_SHA,
    PULL_NUMBER,
    build_issue_comment,
    build_pull_request,
    build_summary_body,
    build_world,
    completed_review_comments,
    no_open_threads_gate,
)
from neutral_core_tests.stage_signal_tests.invocation_fixtures import (
    FIXED_NOW,
    INVOCATION_BODY,
    build_marker_comment,
    expired_marker_comment,
    invoke,
    posted_bodies,
    unexpired_marker_comment,
)
from stagr.platforms.github.runtime import stage_signal_runtime as runtime

MARKER_PATTERN = re.compile(
    r"<!-- stagr:stage:(?P<stage>[^:]+):(?P<head>[0-9a-f]{40}):expires:"
    r"(?P<expires>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) -->"
)


def _assert_invoked(fake, result) -> None:
    assert result.action == runtime.ACTION_INVOKED, result
    assert len(posted_bodies(fake)) == 1


def _assert_not_invoked(fake, result, reason_fragment: str = "") -> None:
    assert result.action == runtime.ACTION_SKIPPED, result
    assert reason_fragment in result.reason, result
    assert posted_bodies(fake) == []


# ---- completion guard ----


def test_completion_evidence_for_the_head_skips_the_invocation() -> None:
    fake = build_world(comments=completed_review_comments())
    _assert_not_invoked(fake, invoke(fake), "completion evidence")


def test_completion_evidence_skips_even_when_the_gate_would_block() -> None:
    fake = build_world(comments=completed_review_comments())
    _assert_not_invoked(fake, invoke(fake, gate=no_open_threads_gate()), "completion evidence")


def test_completion_evidence_for_an_older_head_does_not_skip_the_invocation() -> None:
    stale_summary = build_issue_comment(build_summary_body(code_review_sha=OLD_HEAD_SHA[:7]))
    fake = build_world(comments=[stale_summary])
    _assert_invoked(fake, invoke(fake))


def test_completion_evidence_from_a_human_does_not_skip_the_invocation() -> None:
    forged = build_issue_comment(build_summary_body(), login="attacker", user_type="User")
    fake = build_world(comments=[forged])
    _assert_invoked(fake, invoke(fake))


# ---- in-flight lease ----


def test_unexpired_marker_from_the_trusted_account_skips_the_invocation() -> None:
    fake = build_world(comments=[unexpired_marker_comment()])
    _assert_not_invoked(fake, invoke(fake), "still in flight")


def test_expired_marker_is_treated_as_absent_and_a_fresh_invocation_is_posted() -> None:
    fake = build_world(comments=[expired_marker_comment()])
    _assert_invoked(fake, invoke(fake))


def test_marker_expiring_exactly_now_is_expired() -> None:
    fake = build_world(comments=[build_marker_comment(FIXED_NOW)])
    _assert_invoked(fake, invoke(fake))


def test_any_unexpired_marker_wins_over_older_expired_ones() -> None:
    fake = build_world(comments=[expired_marker_comment(comment_id=40), unexpired_marker_comment()])
    _assert_not_invoked(fake, invoke(fake), "still in flight")


def test_second_run_after_a_fresh_invocation_does_not_post_again() -> None:
    fake = build_world()
    _assert_invoked(fake, invoke(fake))
    second_result = invoke(fake)
    assert second_result.action == runtime.ACTION_SKIPPED and len(posted_bodies(fake)) == 1


def test_the_lease_lapses_after_its_window_and_allows_a_new_invocation() -> None:
    fake = build_world()
    _assert_invoked(fake, invoke(fake))
    within_lease = invoke(fake, now=FIXED_NOW + timedelta(minutes=29))
    assert within_lease.action == runtime.ACTION_SKIPPED and len(posted_bodies(fake)) == 1
    after_lease = FIXED_NOW + timedelta(minutes=31)
    assert invoke(fake, now=after_lease).action == runtime.ACTION_INVOKED
    first_body, second_body = posted_bodies(fake)
    assert runtime.format_lease_timestamp(FIXED_NOW + timedelta(minutes=61)) in second_body
    assert first_body != second_body


# ---- marker authenticity: comments are attacker-controlled on public repositories ----


def _forged_far_future_marker(**overrides):
    return build_marker_comment(FIXED_NOW + timedelta(days=3650), **overrides)


def _assert_forgery_is_ignored(**overrides) -> None:
    fake = build_world(comments=[_forged_far_future_marker(**overrides)])
    _assert_invoked(fake, invoke(fake))


def test_marker_forged_by_an_untrusted_account_is_ignored() -> None:
    attacker = {"id": 666, "login": "attacker", "type": "User"}
    _assert_forgery_is_ignored(author=attacker, author_association="NONE")


def test_marker_by_a_look_alike_login_is_ignored() -> None:
    for look_alike_login in ("stagr-commenter-", "stagr_commenter", "stagr-cornmenter", "stagr"):
        look_alike = {**COMMENTER_USER, "id": 667, "login": look_alike_login}
        _assert_forgery_is_ignored(author=look_alike)


def test_marker_by_the_same_login_but_a_different_account_id_is_ignored() -> None:
    _assert_forgery_is_ignored(author={**COMMENTER_USER, "id": 9999})


def test_marker_by_the_same_account_id_under_a_different_login_is_ignored() -> None:
    _assert_forgery_is_ignored(author={**COMMENTER_USER, "login": "someone-else"})


def test_marker_by_a_bot_twin_of_the_trusted_login_is_ignored() -> None:
    _assert_forgery_is_ignored(author={**COMMENTER_USER, "login": "stagr-commenter[bot]", "type": "Bot"})
    _assert_forgery_is_ignored(author={**COMMENTER_USER, "type": "Bot"})


def test_marker_by_the_trusted_account_without_a_trusted_association_is_ignored() -> None:
    for association in ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "MEMBER", ""):
        _assert_forgery_is_ignored(author_association=association)


def test_marker_comment_without_an_author_is_ignored() -> None:
    forged = _forged_far_future_marker()
    forged["user"] = None
    fake = build_world(comments=[forged])
    _assert_invoked(fake, invoke(fake))


def test_login_case_difference_alone_does_not_disqualify_the_trusted_account() -> None:
    shouting = {**COMMENTER_USER, "login": COMMENTER_USER["login"].upper()}
    fake = build_world(comments=[unexpired_marker_comment(author=shouting)])
    _assert_not_invoked(fake, invoke(fake), "still in flight")


def test_marker_for_another_stage_is_ignored() -> None:
    fake = build_world(comments=[_forged_far_future_marker(stage_id="security")])
    _assert_invoked(fake, invoke(fake))


def test_marker_for_a_stage_whose_id_extends_this_one_is_ignored() -> None:
    fake = build_world(comments=[_forged_far_future_marker(stage_id="review:extra")])
    _assert_invoked(fake, invoke(fake))


def test_marker_for_an_older_head_is_ignored() -> None:
    fake = build_world(comments=[_forged_far_future_marker(head_sha=OLD_HEAD_SHA)])
    _assert_invoked(fake, invoke(fake))


def test_marker_for_an_abbreviated_head_is_ignored() -> None:
    fake = build_world(comments=[_forged_far_future_marker(head_sha=HEAD_SHA[:12])])
    _assert_invoked(fake, invoke(fake))


def test_malformed_markers_are_ignored() -> None:
    for malformed in (
        f"<!-- stagr:stage:review:{HEAD_SHA} -->",  # legacy short form has no lease, so no expiry
        f"<!-- stagr:stage:review:{HEAD_SHA}:expires:never -->",
        f"<!-- stagr:stage:review:{HEAD_SHA}:expires:2099-13-45T99:99:99Z -->",
        f"<!-- stagr:stage:review:{HEAD_SHA}:expires:2099-01-01T00:00:00+05:00 -->",
        f"<!-- stagr:stage:review:{HEAD_SHA}:expires:2099-01-01T00:00:00Z",
    ):
        comment = unexpired_marker_comment()
        comment["body"] = f"{INVOCATION_BODY}\n{malformed}"
        fake = build_world(comments=[comment])
        _assert_invoked(fake, invoke(fake))


# ---- the posted invocation ----


def test_fresh_commit_posts_the_command_and_a_well_formed_future_marker() -> None:
    fake = build_world()
    _assert_invoked(fake, invoke(fake))
    (body,) = posted_bodies(fake)
    assert body.startswith(INVOCATION_BODY + "\n")
    (marker,) = MARKER_PATTERN.findall(body)
    assert marker[0] == "review" and marker[1] == HEAD_SHA
    assert marker[2] == (FIXED_NOW + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (call,) = fake.write_calls
    assert call[:2] == ("POST", f"repos/octo/repo/issues/{PULL_NUMBER}/comments")


def test_the_posted_invocation_carries_a_marker_the_next_run_recognizes() -> None:
    fake = build_world()
    invoke(fake)
    (posted_comment,) = fake.issue_comments[PULL_NUMBER]
    assert posted_comment["user"] == COMMENTER_USER
    assert runtime.parse_lease_expiries(posted_comment["body"], "review", HEAD_SHA) == [
        FIXED_NOW + timedelta(minutes=30)
    ]


def test_the_rendered_lease_length_sets_the_expiry() -> None:
    fake = build_world()
    rule = {"kind": "pr_comment", "body": "@codex security review", "leaseMinutes": 45}
    invoke(fake, invocation=rule)
    (body,) = posted_bodies(fake)
    assert body.startswith("@codex security review\n")
    (marker,) = MARKER_PATTERN.findall(body)
    assert marker[2] == (FIXED_NOW + timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_new_head_commit_is_invoked_even_though_the_previous_head_is_in_flight() -> None:
    new_head = "b" * 40
    fake = build_world(
        comments=[unexpired_marker_comment(head_sha=HEAD_SHA)],
        pull_request=build_pull_request(head_sha=new_head),
    )
    _assert_invoked(fake, invoke(fake, event_head_sha=new_head))
    assert new_head in posted_bodies(fake)[0]


# ---- eligibility and failure behavior ----


def test_an_event_for_a_head_that_has_since_moved_is_not_invoked() -> None:
    fake = build_world()
    _assert_not_invoked(fake, invoke(fake, event_head_sha=OLD_HEAD_SHA), "stale")


def test_untrusted_authors_forks_drafts_and_closed_pull_requests_are_not_invoked() -> None:
    for pull_request, fragment in (
        (build_pull_request(author_association="NONE"), "not a trusted role"),
        (build_pull_request(is_fork=True), "fork"),
        (build_pull_request(state="closed"), "not open"),
        (build_pull_request(is_draft=True), "draft"),
    ):
        fake = build_world(pull_request=pull_request)
        _assert_not_invoked(fake, invoke(fake), fragment)


def test_a_failing_account_lookup_posts_nothing_and_raises() -> None:
    fake = build_world()
    fake.failing_path_fragments.add("user")
    try:
        invoke(fake)
    except runtime.GitHubApiError:
        assert posted_bodies(fake) == []
        return
    raise AssertionError("expected GitHubApiError")


def test_an_account_without_an_id_is_rejected() -> None:
    fake = build_world()
    fake.authenticated_user = {"login": "stagr-commenter", "type": "User"}
    try:
        invoke(fake)
    except runtime.GitHubApiError:
        assert posted_bodies(fake) == []
        return
    raise AssertionError("expected GitHubApiError")


def test_a_failing_post_raises_after_a_single_attempt() -> None:
    fake = build_world()
    fake.failing_write_fragments.append(f"issues/{PULL_NUMBER}/comments")
    try:
        invoke(fake)
    except runtime.GitHubApiError:
        assert fake.issue_comments[PULL_NUMBER] == [] and len(fake.write_calls) == 1
        return
    raise AssertionError("expected GitHubApiError")


# ---- hand-off to a human (remediation round limit) ----


def test_a_pull_request_handed_to_a_human_gets_no_new_review_request() -> None:
    fake = build_world()
    fake.pull_requests[PULL_NUMBER]["labels"] = [{"name": "human-merge"}]
    _assert_not_invoked(fake, invoke(fake, handOffLabel="human-merge"), "handed to a human")


def test_removing_the_hand_off_label_resumes_review_requests() -> None:
    fake = build_world()
    fake.pull_requests[PULL_NUMBER]["labels"] = [{"name": "bug"}]
    _assert_invoked(fake, invoke(fake, handOffLabel="human-merge"))


def test_the_label_is_ignored_when_remediation_is_off() -> None:
    fake = build_world()
    fake.pull_requests[PULL_NUMBER]["labels"] = [{"name": "human-merge"}]
    _assert_invoked(fake, invoke(fake, handOffLabel=None))
