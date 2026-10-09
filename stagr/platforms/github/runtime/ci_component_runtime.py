"""CI-component runtime for Stagr-generated GitHub stage workflows.

Embedded, next to ``stage_signal_runtime.py``, only into the workflow of a stage whose backend runs
as a CI component (for example Codex on an OpenAI API key), and run as
``python3 -c "$STAGR_CI_RUNTIME_SCRIPT"``. It builds on the stage signal runtime instead of copying
it: on the runner it executes ``STAGR_RUNTIME_SCRIPT`` as a module and uses its classes. Like that
runtime it uses the standard library only and must never contain the GitHub expression opener.

Modes (``STAGR_MODE``), both with the App token:
- ``ci_gate``     execute job, after ``eligibility``. Writes ``run_backend=true|false`` and the head
                  and base SHAs to ``GITHUB_OUTPUT``: ``false`` when the pull request is ineligible,
                  handed to a human, or already has completion evidence for its head. The component
                  runs in a separate job that holds the backend credential and no App token.
- ``post_review`` publish job, after the component ran. Posts its findings
                  (``STAGR_REVIEW_FINDINGS``) as one pull request review with a comment on each
                  finding's line, then the completion marker that satisfies the stage's evidence
                  rule. Posts nothing when the head moved since the review or the marker exists, and
                  only the marker when an earlier attempt already posted the review (the review
                  carries a hidden tag naming its stage and head), so a failed marker write is
                  resumed without a duplicate review.
"""
from __future__ import annotations

import json
import os
import re
import sys
import types
from dataclasses import dataclass
from typing import Any, Mapping


def _load_stage_signal_runtime() -> types.ModuleType:
    """The stage signal runtime: the embedded script on the runner, the package module in tests."""
    runtime_script = os.environ.get("STAGR_RUNTIME_SCRIPT")
    if runtime_script is None:
        from stagr.platforms.github.runtime import stage_signal_runtime

        return stage_signal_runtime
    module = types.ModuleType("stagr_stage_signal_runtime")
    sys.modules[module.__name__] = module
    exec(compile(runtime_script, "stage_signal_runtime.py", "exec"), module.__dict__)
    return module


runtime = _load_stage_signal_runtime()

MODE_CI_GATE = "ci_gate"
MODE_POST_REVIEW = "post_review"
ACTION_POSTED = "posted"

STEP_OUTPUT_RUN_BACKEND_NAME = "run_backend"
STEP_OUTPUT_HEAD_SHA_NAME = "head_sha"
STEP_OUTPUT_BASE_SHA_NAME = "base_sha"
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

# A review with more findings than this is refused (the stage fails) rather than posted in part,
# and each finding's text is cut to this many characters, so a runaway reply cannot flood the pull
# request and no reported finding is dropped silently.
MAX_POSTED_FINDINGS = 50
MAX_FINDING_TEXT_LENGTH = 3000
# GitHub lists at most this many changed files of a pull request.
MAX_LISTED_CHANGED_FILES = 3000
HUNK_HEADER_PATTERN = re.compile(r"^@@ -[0-9]+(?:,[0-9]+)? \+([0-9]+)(?:,[0-9]+)? @@")


class CiComponentGate:
    """``ci_gate``: run the component once per eligible head, and never after the hand-off."""

    def __init__(self, config: Any, github_api: Any, repository: str) -> None:
        self._config = config
        self._github_api = github_api
        self._repository = repository
        self._eligibility = runtime.PullRequestEligibility(config)
        self._evidence_evaluator = runtime.CommentEvidenceEvaluator(config.evidence_rules)
        self.reviewed_pull: Any = None

    def decide(self, request: Any) -> Any:
        pull = runtime.PullRequestView.from_api(
            self._github_api.get_object(f"repos/{self._repository}/pulls/{request.pull_number}")
        )
        ineligible_reason = self._eligibility.ineligible_reason(pull, request.event_head_sha)
        if ineligible_reason:
            return runtime.ReconcileResult(runtime.ACTION_SKIPPED, ineligible_reason)
        if self._config.hand_off_label and self._config.hand_off_label in pull.labels:
            return runtime.ReconcileResult(
                runtime.ACTION_SKIPPED,
                "the pull request was handed to a human; remove the hand-off label to resume reviews",
            )
        issue_comments = self._github_api.get_items(
            f"repos/{self._repository}/issues/{pull.number}/comments?per_page=100"
        )
        if self._evidence_evaluator.evaluate(issue_comments, pull.head_sha).is_present:
            return runtime.ReconcileResult(
                runtime.ACTION_SKIPPED, "completion evidence already exists for this head"
            )
        if not (FULL_SHA_PATTERN.match(pull.head_sha) and FULL_SHA_PATTERN.match(pull.base_sha)):
            raise runtime.GitHubApiError("the pull request head or base is not a full commit SHA")
        self.reviewed_pull = pull
        return runtime.ReconcileResult(runtime.ACTION_ELIGIBLE)


@dataclass(frozen=True)
class ReviewFinding:
    path: str
    line: int
    title: str
    body: str


def parse_review_findings(findings_text: str) -> tuple[ReviewFinding, ...]:
    """Parse ``{"findings": [{path, line, title, body}, ...]}``; raise ValueError on any other shape."""
    document = json.loads(findings_text)
    raw_findings = document.get("findings") if isinstance(document, dict) else None
    if not isinstance(raw_findings, list):
        raise ValueError("the review output has no findings list")
    if len(raw_findings) > MAX_POSTED_FINDINGS:
        raise ValueError(
            f"the review reported {len(raw_findings)} findings, more than the {MAX_POSTED_FINDINGS} "
            "a review may post; review the change by hand"
        )
    findings: list[ReviewFinding] = []
    for raw_finding in raw_findings:
        if not isinstance(raw_finding, dict):
            raise ValueError("a review finding is not an object")
        path, line = raw_finding.get("path"), raw_finding.get("line")
        title, body = raw_finding.get("title"), raw_finding.get("body")
        is_line = isinstance(line, int) and not isinstance(line, bool) and line >= 1
        is_text = all(isinstance(value, str) for value in (path, title, body))
        if not (is_line and is_text and path.strip()):
            raise ValueError("a review finding lacks a path, a line, a title or a body")
        findings.append(
            ReviewFinding(
                path=path.strip().removeprefix("./"),
                line=line,
                title=title.strip()[:MAX_FINDING_TEXT_LENGTH],
                body=body.strip()[:MAX_FINDING_TEXT_LENGTH],
            )
        )
    return tuple(findings)


def list_commentable_lines(patch: str) -> list[int]:
    """Return the head-side line numbers a review comment can be attached to in a file's patch."""
    commentable_lines: list[int] = []
    next_line = 0
    for patch_line in patch.splitlines():
        hunk_match = HUNK_HEADER_PATTERN.match(patch_line)
        if hunk_match:
            next_line = int(hunk_match.group(1))
        elif next_line and patch_line[:1] in (" ", "+"):
            commentable_lines.append(next_line)
            next_line += 1
    return commentable_lines


class ReviewPoster:
    """``post_review``: post the component's findings as one review, then the completion marker.

    A finding on a changed file is attached to its line, or to the nearest line the review can
    reach (its text then names the reported line). A finding on a file the pull request does not
    change, or deletes, has no line to attach to: it is listed in the review body and does not
    block. A finding that cannot be placed for want of data (a changed file GitHub shows no patch
    for, or a file list cut at GitHub's limit) fails the step instead, so it never passes unseen.
    """

    def __init__(
        self, config: Any, github_api: Any, repository: str, reviewed_head_sha: str, findings_text: str
    ) -> None:
        self._config = config
        self._github_api = github_api
        self._repository = repository
        self._reviewed_head_sha = reviewed_head_sha.lower()
        self._findings_text = findings_text
        self._evidence_evaluator = runtime.CommentEvidenceEvaluator(config.evidence_rules)

    def post(self, request: Any) -> Any:
        findings = parse_review_findings(self._findings_text)
        pull = runtime.PullRequestView.from_api(
            self._github_api.get_object(f"repos/{self._repository}/pulls/{request.pull_number}")
        )
        if pull.head_sha.lower() != self._reviewed_head_sha:
            return runtime.ReconcileResult(
                runtime.ACTION_SKIPPED, "the head moved since the review; the new head gets its own"
            )
        comments_path = f"repos/{self._repository}/issues/{pull.number}/comments"
        existing_comments = self._github_api.get_items(f"{comments_path}?per_page=100")
        if self._evidence_evaluator.evaluate(existing_comments, pull.head_sha).is_present:
            return runtime.ReconcileResult(
                runtime.ACTION_SKIPPED, "the review for this head was already posted"
            )
        if findings and not self._has_posted_review(pull):
            self._post_review(pull, findings)
        marker_comment = {"body": self._build_marker_comment(pull, len(findings))}
        self._github_api.send_json("POST", comments_path, marker_comment)
        return runtime.ReconcileResult(ACTION_POSTED, f"{len(findings)} finding(s)")

    def _review_tag(self, pull: Any) -> str:
        return f"<!-- stagr-review-posted:{self._config.stage_id}:{pull.head_sha.lower()} -->"

    def _has_posted_review(self, pull: Any) -> bool:
        """True when the stage's review for this head exists (written by the evidence producer)."""
        producers = {rule.produced_by for rule in self._config.evidence_rules}
        tag = self._review_tag(pull)
        return any(
            tag in (review.get("body") or "")
            and any(runtime.is_rest_user_expected_identity(review.get("user") or {}, producer) for producer in producers)
            for review in self._github_api.get_items(
                f"repos/{self._repository}/pulls/{pull.number}/reviews?per_page=100"
            )
        )

    def _post_review(self, pull: Any, findings: tuple[ReviewFinding, ...]) -> None:
        changed_files = self._github_api.get_items(
            f"repos/{self._repository}/pulls/{pull.number}/files?per_page=100"
        )
        is_file_list_complete = len(changed_files) < MAX_LISTED_CHANGED_FILES
        changed_file_by_path = {str(changed_file.get("filename")): changed_file for changed_file in changed_files}
        inline_comments: list[dict[str, Any]] = []
        unattached_findings: list[ReviewFinding] = []
        for finding in findings:
            changed_file = changed_file_by_path.get(finding.path)
            is_unchanged_file = changed_file is None and is_file_list_complete
            if is_unchanged_file or (changed_file or {}).get("status") == "removed":
                unattached_findings.append(finding)
                continue
            commentable_lines = list_commentable_lines(str((changed_file or {}).get("patch") or ""))
            if not commentable_lines:
                raise ValueError(
                    f"a finding on {finding.path} cannot be attached: GitHub shows no patch for it, or "
                    "lists too many changed files to show it; review the change by hand"
                )
            line = min(commentable_lines, key=lambda candidate: abs(candidate - finding.line))
            text = f"**{finding.title}**\n\n{finding.body}"
            if line != finding.line:
                text += f"\n\n_Reported on line {finding.line}, which this change does not touch._"
            inline_comments.append({"path": finding.path, "line": line, "side": "RIGHT", "body": text})
        body = f"**{self._describe_review(pull, len(findings))}**\n\n{self._review_tag(pull)}"
        if unattached_findings:
            body += "\n\nIn files this pull request does not change (not blocking):\n" + "\n".join(
                f"- `{finding.path}:{finding.line}` **{finding.title}**: {finding.body}"
                for finding in unattached_findings
            )
        self._github_api.send_json(
            "POST",
            f"repos/{self._repository}/pulls/{pull.number}/reviews",
            {"commit_id": pull.head_sha, "event": "COMMENT", "body": body, "comments": inline_comments},
        )

    def _build_marker_comment(self, pull: Any, finding_count: int) -> str:
        """The summary line plus one marker per evidence rule, built to satisfy exactly that rule."""
        markers = []
        for rule in self._config.evidence_rules:
            marker_prefix, predicates = runtime.split_compound_selector(rule.selector)
            marker = {**predicates, rule.sha_field: pull.head_sha, "findings": finding_count}
            markers.append(f"<!-- {marker_prefix} {json.dumps(marker, sort_keys=True)} -->")
        return "\n\n".join((self._describe_review(pull, finding_count), *markers))

    def _describe_review(self, pull: Any, finding_count: int) -> str:
        """``Codex review of `abc1234` (stage `security`): 1 finding``."""
        plural = "" if finding_count == 1 else "s"
        return (
            f"Codex review of `{pull.head_sha[:7]}` (stage `{self._config.stage_id}`): "
            f"{finding_count} finding{plural}"
        )


def record_ci_gate_outputs(environment: Mapping[str, str], reviewed_pull: Any) -> None:
    """Tell the component job whether to run, and which commits to review (validated SHAs only)."""
    output_path = environment.get("GITHUB_OUTPUT")
    if not output_path:
        return
    lines = [f"{STEP_OUTPUT_RUN_BACKEND_NAME}={'true' if reviewed_pull else 'false'}\n"]
    if reviewed_pull is not None:
        lines.append(f"{STEP_OUTPUT_HEAD_SHA_NAME}={reviewed_pull.head_sha}\n")
        lines.append(f"{STEP_OUTPUT_BASE_SHA_NAME}={reviewed_pull.base_sha}\n")
    with open(output_path, "a", encoding="utf-8") as output_file:
        output_file.writelines(lines)


def main(environment: Mapping[str, str] | None = None, github_api: Any = None) -> int:
    environment = os.environ if environment is None else environment
    try:
        config = runtime.StageRuntimeConfig.from_json_text(environment["STAGR_STAGE_CONFIG"])
        mode = environment["STAGR_MODE"]
        repository = environment["GITHUB_REPOSITORY"]
        if mode not in (MODE_CI_GATE, MODE_POST_REVIEW):
            raise runtime.RuntimeConfigError(f"Unknown STAGR_MODE {mode!r}")
        if not environment.get("STAGR_PULL_NUMBER"):
            print(f"Stage {config.stage_id}: no pull request context; nothing to do.")
            if mode == MODE_CI_GATE:
                record_ci_gate_outputs(environment, None)
            return 0
        github_api = github_api or runtime.GitHubCliApi()
        request = runtime.ReconcileRequest(
            mode=mode,
            pull_number=int(environment["STAGR_PULL_NUMBER"]),
            event_head_sha=environment.get("STAGR_EVENT_HEAD_SHA") or None,
        )
        if mode == MODE_CI_GATE:
            ci_gate = CiComponentGate(config, github_api, repository)
            result = ci_gate.decide(request)
            record_ci_gate_outputs(environment, ci_gate.reviewed_pull)
        else:
            result = ReviewPoster(
                config,
                github_api,
                repository,
                environment["STAGR_REVIEWED_HEAD_SHA"],
                environment["STAGR_REVIEW_FINDINGS"],
            ).post(request)
    except (KeyError, ValueError, runtime.GitHubApiError, runtime.AmbiguousSignalError) as error:
        print(f"::error::CI component runtime failed: {error}")
        return 1
    print(
        f"Stage {config.stage_id} pull request #{request.pull_number}: "
        f"{result.action} {result.reason}".rstrip()
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
