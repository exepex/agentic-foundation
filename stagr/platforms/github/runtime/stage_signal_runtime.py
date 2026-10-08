"""Stage runtime for Stagr-generated GitHub stage workflows (issues #206, #205 and #207).

This file is embedded verbatim into every generated ``stage-<id>.yml`` workflow and run on the
GitHub Actions runner as ``python3 -c "$STAGR_RUNTIME_SCRIPT"``. It uses the standard library only
and imports nothing from ``stagr``: Stagr is a control plane and is never involved at run time.
It must never contain the GitHub expression opener, because Actions would try to evaluate it while
the script sits in a workflow ``env:`` value.

Principle: the signal is a function of observed platform state. Every mode re-reads the pull request,
its comments, its review threads and the existing Check Run, and derives the desired
``StageResultSignal`` from them. No decision depends on the content of the triggering event, so
missed, duplicated or reordered events are harmless and the scheduled sweep is a pure backstop.

Modes (``STAGR_MODE``):
- ``eligibility`` execute job, first. Decides whether the backend may be invoked for this event and
                writes ``proceed=true|false`` to ``GITHUB_OUTPUT``; the invoke step runs only on
                ``true``. Holds the App token, never the backend credential (see "Eligibility").
- ``invoke``    execute job, before ``publish``. Posts the backend invocation comment at most once per
                (stage, head): skipped when completion evidence already exists for the head, or when a
                still-unexpired in-flight marker written by the trusted posting account exists.
                Runs ONLY with the backend credential (``TRUSTED_COMMENTER_TOKEN``), never the App token.
- ``publish``   execute job, after the backend was invoked. The ONLY mode that creates the Check Run
                (it runs inside the per-stage, per-pull-request concurrency group, so two creators
                can never race into the duplicate Check Runs that governance rejects).
- ``reconcile`` ``issue_comment`` wakeup for one pull request. Updates an existing Check Run only.
- ``sweep``     scheduled: runs the same routine for every open pull request. Updates only.

Eligibility (``StageEligibilityEvaluator``, one implementation shared by ``eligibility``, ``publish``,
``reconcile`` and ``sweep``), in this order; the first failing check wins:
1. the pull request: open, not a draft, trusted author, fork policy, event head is the current head;
2. route applicability: when a route rule is configured, the stage must apply to the route that the
   Stagr App published as ``RouteClassification`` for the head (missing, duplicated, forged or
   unrecognised classification fails closed; a MISSING one is waited for, see below);
3. dependency wake-ups only: a stage whose own signal is already ``pass`` or ``failed`` is final;
   only a re-run of the execute job retries (a wake-up fires on unrelated Check Run chatter and
   must never turn a failed backend into a retry loop);
4. dependencies: every upstream signal must be ``completed`` + ``pass`` for the current head. An
   upstream ``failed`` makes this stage ``failed`` without invoking the backend (FAILED
   propagation). Anything else (missing, malformed, wrong head, running, blocked) means "not yet".

An upstream signal is trusted only when it is the single Check Run of that stage on the head written
by the Stagr App, and its ``output.summary`` payload states the same stage, head and schema version;
the payload, not the native Check Run fields, is authoritative. Nothing derived from a payload is
ever printed (a forged value could otherwise inject workflow commands).

Only ``eligibility`` waits for a missing route classification (the routing workflow starts at the
same moment as the stage workflow); the other modes evaluate once. ``sweep`` and ``reconcile`` never
create a Check Run and never invoke, so a stage whose dependencies pass while only the sweep is
looking starts the next time its execute job runs (a dependency wake-up, a reopen, a re-run).

Invocation write policy (``invoke``): the only write is one issue comment carrying the lease marker
``<!-- stagr:stage:<stageId>:<headSha>:expires:<UTC ISO8601> -->``. A marker counts only when its
comment was written by the account that owns the invoke token (identity, id and type are read from
``GET /user`` at run time) with a trusted ``author_association``: comments on a public repository are
attacker-controlled, and a forged far-future marker must not be able to suppress an invocation.
``sweep`` and ``reconcile`` never invoke (they do not hold that credential), so an expired lease is
recovered the next time ``invoke`` runs (push, reopen, ready_for_review), not by the sweep.

Write policy: ``completed`` + ``pass`` and ``failed`` are terminal for wakeups and the sweep
(design-doc 06); a re-run of the execute job (``publish``) may replace ``failed`` with a fresh
attempt but never rewrites ``pass``. ``blocked`` is re-evaluated on every wakeup. A write happens
only when the desired signal differs from the existing Check Run.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

SIGNAL_SCHEMA_VERSION = 1

MODE_ELIGIBILITY = "eligibility"
MODE_INVOKE = "invoke"
MODE_PUBLISH = "publish"
MODE_RECONCILE = "reconcile"
MODE_SWEEP = "sweep"

ACTION_SKIPPED = "skipped"
ACTION_UNCHANGED = "unchanged"
ACTION_CREATED = "created"
ACTION_UPDATED = "updated"
ACTION_INVOKED = "invoked"
ACTION_ELIGIBLE = "eligible"

OUTCOME_PROCEED = "proceed"
OUTCOME_SKIP = "skip"
OUTCOME_DEPENDENCY_FAILED = "dependency_failed"

STEP_OUTPUT_PROCEED_NAME = "proceed"
# Events that wake a stage because an upstream signal may have changed (not a new head, not a comment).
DEPENDENCY_WAKEUP_EVENT_NAMES = frozenset({"check_run", "check_suite"})

ROUTE_FAST = "FAST"
ROUTE_NORMAL = "NORMAL"
ROUTE_TITLE_PREFIX = "RouteClassification="
CHECK_RUN_STATUS_COMPLETED = "completed"
# The routing workflow starts together with the stage workflow, so its Check Run is often not there
# yet when eligibility runs; wait for it for at most this long, then fail closed.
ROUTE_WAIT_ATTEMPTS = 18
ROUTE_WAIT_SECONDS = 10.0

STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"

CONCLUSION_PASS = "pass"
CONCLUSION_BLOCKED = "blocked"
CONCLUSION_FAILED = "failed"
CONCLUSION_UNKNOWN = "unknown"

# StageResultState -> Check Run status, and StageResultConclusion -> Check Run conclusion
# (issue #206 serialization contract). Consumers read the JSON payload, not these native fields.
NATIVE_STATUS_BY_STATE = {
    STATE_PENDING: "queued",
    STATE_RUNNING: "in_progress",
    STATE_COMPLETED: "completed",
    STATE_FAILED: "completed",
}
NATIVE_CONCLUSION_BY_CONCLUSION = {
    CONCLUSION_PASS: "success",
    CONCLUSION_BLOCKED: "action_required",
    CONCLUSION_FAILED: "failure",
    CONCLUSION_UNKNOWN: "neutral",
}
FINISHED_STATES = frozenset({STATE_COMPLETED, STATE_FAILED})

EVIDENCE_KIND_REVIEW_RESULT = "review_result"
EVIDENCE_KIND_COMMENT_MATCH = "comment_match"
REVIEW_SUMMARY_SHA_FIELD = "review_summary_sha"

GATE_KIND_ALWAYS_PASS = "always_pass"
GATE_KIND_EXPLICIT_PASS_MARKER = "explicit_pass_marker"
GATE_KIND_NO_OPEN_THREADS = "no_open_threads"

JOB_STATUS_FAILURE = "failure"

INVOCATION_KIND_PR_COMMENT = "pr_comment"
TRUSTED_COMMENTER_TOKEN_VARIABLE = "TRUSTED_COMMENTER_TOKEN"
DEFAULT_LEASE_MINUTES = 30
MAX_LEASE_MINUTES = 24 * 60
LEASE_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
LEASE_TIMESTAMP_PATTERN = r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"

REVIEW_THREADS_QUERY = (
    "query($owner:String!,$repo:String!,$number:Int!,$cursor:String){"
    "repository(owner:$owner,name:$repo){pullRequest(number:$number){"
    "reviewThreads(first:100,after:$cursor){"
    "nodes{isResolved comments(first:1){nodes{"
    "author{__typename login} pullRequestReview{commit{oid}}}}}"
    "pageInfo{hasNextPage endCursor}}}}}"
)


class GitHubApiError(RuntimeError):
    """A GitHub API call failed (network, HTTP error, or GraphQL error)."""


class AmbiguousSignalError(RuntimeError):
    """More than one Check Run of this stage exists for a head; nothing may be written."""


class RuntimeConfigError(ValueError):
    """The rendered per-stage configuration is malformed or unsupported."""


# ---------------------------------------------------------------------------
# Configuration rendered by Stagr at ``stagr apply`` time
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceRule:
    kind: str
    selector: str
    sha_field: str
    success_condition: str
    produced_by: str


@dataclass(frozen=True)
class GateRule:
    kind: str
    selector: str
    created_by: str
    head_sha_bound: bool


@dataclass(frozen=True)
class InvocationRule:
    """How ``invoke`` mode asks the backend to run: one PR comment, guarded by a lease."""

    kind: str
    body: str
    lease_minutes: int


@dataclass(frozen=True)
class DependencyRule:
    """An upstream stage that must be ``pass`` for the head, and the Check Run that says so."""

    stage_id: str
    check_run_name: str


@dataclass(frozen=True)
class RouteRule:
    """Which stages apply to each route, and the Check Run that carries the classification."""

    check_run_name: str
    fast_stage_ids: frozenset[str]
    normal_stage_ids: frozenset[str]

    def is_applicable(self, stage_id: str, route: str) -> bool:
        applicable_stage_ids = self.fast_stage_ids if route == ROUTE_FAST else self.normal_stage_ids
        return stage_id in applicable_stage_ids


@dataclass(frozen=True)
class StageRuntimeConfig:
    stage_id: str
    check_run_name: str
    publisher_app_id: str
    trusted_roles: frozenset[str]
    deny_forks: bool
    privileged_stage: bool
    evidence_rules: tuple[EvidenceRule, ...]
    gate_rule: GateRule
    invocation_rule: InvocationRule
    dependency_rules: tuple[DependencyRule, ...] = ()
    route_rule: RouteRule | None = None
    # Label that marks a pull request handed to a human after the automated fix rounds ran out;
    # while it is set, the stage requests no new review. None when automated fixing is off.
    hand_off_label: str | None = None

    @classmethod
    def from_json_text(cls, config_text: str) -> "StageRuntimeConfig":
        try:
            document = json.loads(config_text)
            gate_document = document["gate"]
            config = cls(
                stage_id=document["stageId"],
                check_run_name=document["checkRunName"],
                publisher_app_id=str(document["publisherAppId"]),
                trusted_roles=frozenset(role.upper() for role in document["trustedRoles"]),
                deny_forks=bool(document["denyForks"]),
                privileged_stage=bool(document["privilegedStage"]),
                evidence_rules=tuple(
                    EvidenceRule(
                        kind=item["kind"],
                        selector=item["selector"],
                        sha_field=item["shaField"],
                        success_condition=item["successCondition"],
                        produced_by=item["producedBy"],
                    )
                    for item in document["evidence"]
                ),
                gate_rule=GateRule(
                    kind=gate_document["kind"],
                    selector=gate_document["selector"],
                    created_by=gate_document["createdBy"],
                    head_sha_bound=bool(gate_document["headShaBound"]),
                ),
                invocation_rule=cls._parse_invocation_rule(document["invocation"]),
                dependency_rules=tuple(
                    DependencyRule(stage_id=item["stageId"], check_run_name=item["checkRunName"])
                    for item in document["dependencies"]
                ),
                route_rule=cls._parse_route_rule(document["routing"]),
                hand_off_label=document.get("handOffLabel"),
            )
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise RuntimeConfigError(f"Invalid STAGR_STAGE_CONFIG: {error!r}") from error
        config.reject_unsupported_rules()
        return config

    @staticmethod
    def _parse_invocation_rule(invocation_document: Mapping[str, Any]) -> InvocationRule:
        return InvocationRule(
            kind=invocation_document["kind"],
            body=invocation_document["body"],
            lease_minutes=invocation_document["leaseMinutes"],
        )

    @staticmethod
    def _parse_route_rule(routing_document: Mapping[str, Any] | None) -> RouteRule | None:
        if routing_document is None:
            return None
        return RouteRule(
            check_run_name=routing_document["checkRunName"],
            fast_stage_ids=frozenset(routing_document["fastStageIds"]),
            normal_stage_ids=frozenset(routing_document["normalStageIds"]),
        )

    def reject_unsupported_rules(self) -> None:
        """Fail closed on any rule this runtime cannot evaluate exactly (never a weaker check)."""
        if not self.evidence_rules:
            raise RuntimeConfigError("At least one evidence rule is required to prove completion")
        for rule in self.evidence_rules:
            is_supported_review_result = (
                rule.kind == EVIDENCE_KIND_REVIEW_RESULT
                and rule.sha_field == REVIEW_SUMMARY_SHA_FIELD
                and rule.success_condition == "completed"
            )
            is_supported_comment_match = (
                rule.kind == EVIDENCE_KIND_COMMENT_MATCH
                and rule.sha_field
                and rule.success_condition == "match_found"
            )
            if not (is_supported_review_result or is_supported_comment_match):
                raise RuntimeConfigError(f"Unsupported evidence rule: {rule!r}")
            if not rule.produced_by or not rule.selector.strip():
                raise RuntimeConfigError(f"Evidence rule needs a producer and selector: {rule!r}")
        gate = self.gate_rule
        if gate.kind not in (
            GATE_KIND_ALWAYS_PASS,
            GATE_KIND_EXPLICIT_PASS_MARKER,
            GATE_KIND_NO_OPEN_THREADS,
        ):
            raise RuntimeConfigError(f"Unsupported gate disposition kind: {gate.kind!r}")
        if gate.kind == GATE_KIND_NO_OPEN_THREADS and not gate.created_by:
            raise RuntimeConfigError("NO_OPEN_THREADS requires the finding author (createdBy)")
        if gate.kind == GATE_KIND_EXPLICIT_PASS_MARKER and not gate.selector:
            raise RuntimeConfigError("EXPLICIT_PASS_MARKER requires a selector")
        self._reject_unsupported_invocation_rule()
        self._reject_unsupported_eligibility_rules()

    def _reject_unsupported_eligibility_rules(self) -> None:
        for rule in self.dependency_rules:
            names_are_text = all(
                isinstance(name, str) and name for name in (rule.stage_id, rule.check_run_name)
            )
            if not names_are_text or rule.stage_id == self.stage_id:
                raise RuntimeConfigError(f"Unsupported dependency rule: {rule!r}")
        route_rule = self.route_rule
        if route_rule is not None and not (
            isinstance(route_rule.check_run_name, str) and route_rule.check_run_name
            and all(isinstance(item, str) for item in route_rule.fast_stage_ids)
            and all(isinstance(item, str) for item in route_rule.normal_stage_ids)
        ):
            raise RuntimeConfigError(f"Unsupported route rule: {route_rule!r}")

    def _reject_unsupported_invocation_rule(self) -> None:
        rule = self.invocation_rule
        if rule.kind != INVOCATION_KIND_PR_COMMENT:
            raise RuntimeConfigError(f"Unsupported invocation kind: {rule.kind!r}")
        if not isinstance(rule.body, str) or not rule.body.strip():
            raise RuntimeConfigError("A pr_comment invocation needs a non-empty body")
        is_integer = isinstance(rule.lease_minutes, int) and not isinstance(rule.lease_minutes, bool)
        if not is_integer or not 1 <= rule.lease_minutes <= MAX_LEASE_MINUTES:
            raise RuntimeConfigError(
                f"leaseMinutes must be an integer from 1 to {MAX_LEASE_MINUTES}: "
                f"{rule.lease_minutes!r}"
            )


# ---------------------------------------------------------------------------
# The signal and its Check Run serialization
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageSignal:
    """The desired StageResultSignal state and conclusion (stage id and head are added on write)."""

    state: str
    conclusion: str

    @property
    def native_status(self) -> str:
        return NATIVE_STATUS_BY_STATE[self.state]

    @property
    def native_conclusion(self) -> str | None:
        """Check Run conclusion; only sent once the run is finished (it would force ``completed``)."""
        if self.state in FINISHED_STATES:
            return NATIVE_CONCLUSION_BY_CONCLUSION[self.conclusion]
        return None


RUNNING_SIGNAL = StageSignal(STATE_RUNNING, CONCLUSION_UNKNOWN)
FAILED_SIGNAL = StageSignal(STATE_FAILED, CONCLUSION_FAILED)


def serialize_signal_payload(stage_id: str, head_sha: str, signal: StageSignal) -> str:
    """Return the compact JSON stored in ``output.summary`` (the consumers' authoritative source)."""
    return json.dumps(
        {
            "schemaVersion": SIGNAL_SCHEMA_VERSION,
            "stageId": stage_id,
            "headSha": head_sha,
            "state": signal.state,
            "conclusion": signal.conclusion,
        },
        separators=(",", ":"),
    )


KNOWN_STATES = frozenset({STATE_PENDING, STATE_RUNNING, STATE_COMPLETED, STATE_FAILED})
KNOWN_CONCLUSIONS = frozenset(
    {CONCLUSION_PASS, CONCLUSION_BLOCKED, CONCLUSION_FAILED, CONCLUSION_UNKNOWN}
)


def deserialize_signal_payload(
    payload: Mapping[str, Any] | None, stage_id: str, head_sha: str
) -> StageSignal | None:
    """Return the signal ``payload`` states for exactly ``(stage_id, head_sha)``, else ``None``.

    The payload is the consumers' authoritative source (issue #206). Anything that is not a
    well-formed, version-1 payload for this stage and this head yields ``None`` ("no trustworthy
    signal"), never a guess: wrong or missing schema version, another stage, a stale head, an
    unknown state or conclusion, or a value of the wrong type.
    """
    if payload is None:
        return None
    schema_version = payload.get("schemaVersion")
    if isinstance(schema_version, bool) or schema_version != SIGNAL_SCHEMA_VERSION:
        return None
    state, conclusion = payload.get("state"), payload.get("conclusion")
    is_bound_to_stage_and_head = (
        payload.get("stageId") == stage_id and payload.get("headSha") == head_sha
    )
    are_known_values = (
        isinstance(state, str) and state in KNOWN_STATES
        and isinstance(conclusion, str) and conclusion in KNOWN_CONCLUSIONS
    )
    if not (is_bound_to_stage_and_head and are_known_values):
        return None
    return StageSignal(state, conclusion)


# ---------------------------------------------------------------------------
# GitHub access
# ---------------------------------------------------------------------------


class GitHubApi(Protocol):
    def get_object(self, path: str) -> dict[str, Any]: ...

    def get_items(self, path: str, items_key: str | None = None) -> list[Any]: ...

    def run_graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]: ...

    def send_json(self, method: str, path: str, body: Mapping[str, Any]) -> dict[str, Any]: ...


class GitHubCliApi:
    """GitHub API access through the ``gh`` CLI, always via argument lists (no shell quoting).

    Reads are retried because they are idempotent. Writes are attempted once: repeating a
    Check Run creation after an ambiguous failure could create the duplicate that governance rejects.
    """

    READ_ATTEMPTS = 3

    def __init__(
        self,
        run_process: Callable[..., Any] = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
        retry_delay_seconds: float = 1.0,
        process_environment: Mapping[str, str] | None = None,
    ) -> None:
        self._run_process = run_process
        self._sleep = sleep
        self._retry_delay_seconds = retry_delay_seconds
        self._extra_process_options: dict[str, Any] = (
            {} if process_environment is None else {"env": dict(process_environment)}
        )

    def get_object(self, path: str) -> dict[str, Any]:
        return self._parse_json(self._run(["api", path], attempts=self.READ_ATTEMPTS))

    def get_items(self, path: str, items_key: str | None = None) -> list[Any]:
        pages = self._parse_json(
            self._run(["api", "--paginate", "--slurp", path], attempts=self.READ_ATTEMPTS)
        )
        items: list[Any] = []
        try:
            for page in pages:
                items.extend(page if items_key is None else page[items_key])
        except (KeyError, TypeError) as error:
            raise GitHubApiError(f"Unexpected paginated response shape for {path}") from error
        return items

    def run_graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        arguments = ["api", "graphql", "-f", f"query={query}"]
        for name, value in variables.items():
            if value is None:
                continue
            is_integer = isinstance(value, int) and not isinstance(value, bool)
            arguments += ["-F" if is_integer else "-f", f"{name}={value}"]
        response = self._parse_json(self._run(arguments, attempts=self.READ_ATTEMPTS))
        if response.get("errors") or not response.get("data"):
            raise GitHubApiError(f"GraphQL query failed: {response.get('errors')!r:.300}")
        return response

    def send_json(self, method: str, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        arguments = ["api", "--method", method, path, "--input", "-"]
        self._run(arguments, standard_input=json.dumps(body), attempts=1)
        return {}

    @staticmethod
    def _parse_json(text: str) -> Any:
        try:
            return json.loads(text)
        except ValueError as error:
            raise GitHubApiError("GitHub returned a response that is not valid JSON") from error

    def _run(
        self, arguments: list[str], standard_input: str | None = None, attempts: int = 1
    ) -> str:
        for attempt in range(1, attempts + 1):
            completed = self._run_process(
                ["gh", *arguments],
                input=standard_input,
                capture_output=True,
                text=True,
                check=False,
                **self._extra_process_options,
            )
            if completed.returncode == 0:
                return completed.stdout
            if attempt < attempts:
                self._sleep(self._retry_delay_seconds * 2 ** (attempt - 1))
        raise GitHubApiError(
            f"gh {' '.join(arguments[:3])} failed with exit code {completed.returncode}: "
            f"{completed.stderr.strip()[:300]}"
        )


# ---------------------------------------------------------------------------
# Identity matching (evidence and finding authors are untrusted unless they match)
# ---------------------------------------------------------------------------


def is_rest_user_expected_identity(rest_user: Mapping[str, Any], expected_identity: str) -> bool:
    """Match a REST ``user`` object. A ``[bot]`` identity matches only a real Bot actor.

    The suffix is never stripped here: doing so would let a human account named like a bot pass.
    """
    login = str(rest_user.get("login", "")).lower()
    expects_bot = expected_identity.lower().endswith("[bot]")
    is_bot = rest_user.get("type") == "Bot"
    return login == expected_identity.lower() and is_bot == expects_bot


def is_graphql_actor_expected_identity(
    graphql_actor: Mapping[str, Any] | None, expected_identity: str
) -> bool:
    """Match a GraphQL ``author``. GraphQL reports Bot logins without the ``[bot]`` suffix."""
    if not graphql_actor:
        return False
    login = str(graphql_actor.get("login", "")).lower()
    is_bot = graphql_actor.get("__typename") == "Bot"
    if login.endswith("[bot]"):
        login = login[: -len("[bot]")]
    expected = expected_identity.lower()
    expects_bot = expected.endswith("[bot]")
    if expects_bot:
        expected = expected[: -len("[bot]")]
    return login == expected and is_bot == expects_bot


# ---------------------------------------------------------------------------
# Evidence: "has the backend finished for the current head?"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceOutcome:
    is_present: bool
    matched_bodies: tuple[str, ...] = ()


def split_compound_selector(selector: str) -> tuple[str, dict[str, str]]:
    """Split ``<marker-prefix> [key=value ...]`` (design-doc 06 compound selector convention)."""
    tokens = selector.split()
    predicates: dict[str, str] = {}
    for token in tokens[1:]:
        key, _, expected_value = token.partition("=")
        predicates[key] = expected_value
    return tokens[0], predicates


def json_value_as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def extract_marker_objects(body: str, marker_prefix: str) -> list[dict[str, Any]]:
    """Return every JSON object that follows ``marker_prefix`` inside an HTML comment."""
    objects: list[dict[str, Any]] = []
    cursor = 0
    while True:
        start = body.find(marker_prefix, cursor)
        if start == -1:
            return objects
        content_start = start + len(marker_prefix)
        content_end = body.find("-->", content_start)
        if content_end == -1:
            return objects
        cursor = content_start
        try:
            parsed = json.loads(body[content_start:content_end].strip())
        except ValueError:
            continue
        if isinstance(parsed, dict):
            objects.append(parsed)


def review_summary_row_completed_for_head(summary_body: str, head_sha: str) -> bool:
    """True if the summary table's "Code Review" row says Completed for a prefix of ``head_sha``."""
    for line in summary_body.splitlines():
        if not line.lstrip().startswith("|") or not re.search(r"code review", line, re.I):
            continue
        commit_match = re.search(r"`([0-9a-f]{7,40})`", line, re.I)
        return bool(
            re.search(r"\bcompleted\b", line, re.I)
            and commit_match
            and head_sha.lower().startswith(commit_match.group(1).lower())
        )
    return False


class CommentEvidenceEvaluator:
    """Evaluates comment-based EvidenceSpecs (REVIEW_RESULT and COMMENT_MATCH) for one head.

    Only comments authored by the rule's ``produced_by`` identity count, and only the latest such
    comment containing the selector prefix is considered. All rules must hold.
    """

    def __init__(self, evidence_rules: tuple[EvidenceRule, ...]) -> None:
        self._evidence_rules = evidence_rules

    def evaluate(self, issue_comments: list[Mapping[str, Any]], head_sha: str) -> EvidenceOutcome:
        matched_bodies: list[str] = []
        for rule in self._evidence_rules:
            body = self._latest_producer_body(rule, issue_comments)
            if body is None or not self._body_satisfies_rule(rule, body, head_sha):
                return EvidenceOutcome(False)
            matched_bodies.append(body)
        return EvidenceOutcome(True, tuple(matched_bodies))

    def _latest_producer_body(
        self, rule: EvidenceRule, issue_comments: list[Mapping[str, Any]]
    ) -> str | None:
        marker_prefix, _ = split_compound_selector(rule.selector)
        candidates = [
            comment
            for comment in issue_comments
            if is_rest_user_expected_identity(comment.get("user") or {}, rule.produced_by)
            and marker_prefix in (comment.get("body") or "")
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda comment: comment.get("id", 0))["body"]

    def _body_satisfies_rule(self, rule: EvidenceRule, body: str, head_sha: str) -> bool:
        if rule.kind == EVIDENCE_KIND_REVIEW_RESULT:
            return review_summary_row_completed_for_head(body, head_sha)
        marker_prefix, predicates = split_compound_selector(rule.selector)
        return any(
            all(
                json_value_as_text(marker.get(key)) == expected
                for key, expected in predicates.items()
            )
            and str(marker.get(rule.sha_field, "")).lower() == head_sha.lower()
            for marker in extract_marker_objects(body, marker_prefix)
        )


# ---------------------------------------------------------------------------
# Gate disposition: "given it finished, PASS or BLOCKED?"
# ---------------------------------------------------------------------------


class GateEvaluator:
    def __init__(
        self, gate_rule: GateRule, github_api: GitHubApi, repository_owner: str, repository_name: str
    ) -> None:
        self._gate_rule = gate_rule
        self._github_api = github_api
        self._repository_owner = repository_owner
        self._repository_name = repository_name

    def evaluate(self, pull_number: int, head_sha: str, evidence_bodies: tuple[str, ...]) -> str:
        kind = self._gate_rule.kind
        if kind == GATE_KIND_ALWAYS_PASS:
            return CONCLUSION_PASS
        if kind == GATE_KIND_EXPLICIT_PASS_MARKER:
            has_marker = any(self._gate_rule.selector in body for body in evidence_bodies)
            return CONCLUSION_PASS if has_marker else CONCLUSION_BLOCKED
        if kind == GATE_KIND_NO_OPEN_THREADS:
            open_count = self._count_open_threads(pull_number, head_sha)
            return CONCLUSION_PASS if open_count == 0 else CONCLUSION_BLOCKED
        raise RuntimeConfigError(f"Unsupported gate disposition kind: {kind!r}")

    def _count_open_threads(self, pull_number: int, head_sha: str) -> int:
        open_count = 0
        cursor: str | None = None
        while True:
            response = self._github_api.run_graphql(
                REVIEW_THREADS_QUERY,
                {
                    "owner": self._repository_owner,
                    "repo": self._repository_name,
                    "number": pull_number,
                    "cursor": cursor,
                },
            )
            try:
                threads = response["data"]["repository"]["pullRequest"]["reviewThreads"]
            except (KeyError, TypeError) as error:
                raise GitHubApiError("Unexpected GraphQL review thread response shape") from error
            open_count += sum(
                1 for thread in threads["nodes"] if self._is_open_finding(thread, head_sha)
            )
            if not threads["pageInfo"]["hasNextPage"]:
                return open_count
            cursor = threads["pageInfo"]["endCursor"]

    def _is_open_finding(self, thread: Mapping[str, Any], head_sha: str) -> bool:
        if thread.get("isResolved"):
            return False
        first_comments = (thread.get("comments") or {}).get("nodes") or []
        if not first_comments:
            return False
        first_comment = first_comments[0]
        if not is_graphql_actor_expected_identity(
            first_comment.get("author"), self._gate_rule.created_by
        ):
            return False
        if not self._gate_rule.head_sha_bound:
            return True
        reviewed_commit = (
            ((first_comment.get("pullRequestReview") or {}).get("commit") or {}).get("oid")
        )
        # Fail closed: a finding whose review commit is unknown cannot be proven to be from an
        # older head, so it keeps the stage blocked.
        return reviewed_commit is None or reviewed_commit.lower() == head_sha.lower()


# ---------------------------------------------------------------------------
# The Check Run that carries the signal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExistingSignalRun:
    check_run_id: int
    status: str | None
    conclusion: str | None
    payload: Mapping[str, Any] | None
    title: str | None = None
    head_sha: str | None = None

    def has_signal_for(self, stage_id: str, head_sha: str, state: str, conclusion: str) -> bool:
        return (
            self.payload is not None
            and self.payload.get("schemaVersion") == SIGNAL_SCHEMA_VERSION
            and self.payload.get("stageId") == stage_id
            and self.payload.get("headSha") == head_sha
            and self.payload.get("state") == state
            and self.payload.get("conclusion") == conclusion
        )


class StagrCheckRunReader:
    """Finds the single Check Run of a given name that the Stagr App wrote for a head.

    Runs of the same name written by any other app are ignored (they cannot be trusted and must
    not be able to displace or impersonate the real one); two Stagr runs are ambiguous and raise.
    """

    def __init__(self, github_api: GitHubApi, repository: str, publisher_app_id: str) -> None:
        self._github_api = github_api
        self._repository = repository
        self._publisher_app_id = publisher_app_id

    def find_single(self, check_run_name: str, head_sha: str) -> ExistingSignalRun | None:
        listing_path = (
            f"repos/{self._repository}/commits/{head_sha}/check-runs"
            f"?check_name={quote(check_run_name, safe='')}&filter=all&per_page=100"
        )
        published_by_stagr = [
            check_run
            for check_run in self._github_api.get_items(listing_path, items_key="check_runs")
            if check_run.get("name") == check_run_name
            and str((check_run.get("app") or {}).get("id")) == self._publisher_app_id
        ]
        if len(published_by_stagr) > 1:
            raise AmbiguousSignalError(
                f"{len(published_by_stagr)} Check Runs named {check_run_name!r} "
                f"exist for head {head_sha}; refusing to use them (governance rejects duplicates)."
            )
        if not published_by_stagr:
            return None
        check_run = published_by_stagr[0]
        output = check_run.get("output") or {}
        return ExistingSignalRun(
            check_run_id=check_run["id"],
            status=check_run.get("status"),
            conclusion=check_run.get("conclusion"),
            payload=parse_signal_payload_text(output.get("summary")),
            title=output.get("title"),
            head_sha=check_run.get("head_sha"),
        )


def parse_signal_payload_text(summary: str | None) -> Mapping[str, Any] | None:
    try:
        parsed = json.loads(summary or "")
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


class CheckRunStore:
    """Finds, creates and updates the single Check Run of this stage for a head."""

    def __init__(
        self, config: StageRuntimeConfig, github_api: GitHubApi, repository: str, run_url: str | None
    ) -> None:
        self._config = config
        self._github_api = github_api
        self._repository = repository
        self._run_url = run_url
        self._reader = StagrCheckRunReader(github_api, repository, config.publisher_app_id)

    def find_existing(self, head_sha: str) -> ExistingSignalRun | None:
        return self._reader.find_single(self._config.check_run_name, head_sha)

    def write_if_changed(
        self, existing: ExistingSignalRun | None, head_sha: str, signal: StageSignal
    ) -> str:
        if existing is not None and self._already_matches(existing, head_sha, signal):
            return ACTION_UNCHANGED
        body: dict[str, Any] = {
            "status": signal.native_status,
            "output": {
                "title": self._title(signal),
                "summary": serialize_signal_payload(self._config.stage_id, head_sha, signal),
            },
        }
        if signal.native_conclusion is not None:
            body["conclusion"] = signal.native_conclusion
        if self._run_url:
            body["details_url"] = self._run_url
        if existing is None:
            body.update(name=self._config.check_run_name, head_sha=head_sha)
            self._github_api.send_json("POST", f"repos/{self._repository}/check-runs", body)
            return ACTION_CREATED
        self._github_api.send_json(
            "PATCH", f"repos/{self._repository}/check-runs/{existing.check_run_id}", body
        )
        return ACTION_UPDATED

    def _already_matches(
        self, existing: ExistingSignalRun, head_sha: str, signal: StageSignal
    ) -> bool:
        expected_payload = json.loads(
            serialize_signal_payload(self._config.stage_id, head_sha, signal)
        )
        return (
            existing.payload == expected_payload
            and existing.status == signal.native_status
            and existing.conclusion == signal.native_conclusion
        )

    def _title(self, signal: StageSignal) -> str:
        suffix = f" ({signal.conclusion})" if signal.state in FINISHED_STATES else ""
        return f"Stagr stage {self._config.stage_id}: {signal.state}{suffix}"


# ---------------------------------------------------------------------------
# The reconciliation routine (one implementation for every mode)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconcileRequest:
    mode: str
    pull_number: int
    event_head_sha: str | None = None
    job_status: str | None = None
    event_name: str | None = None

    @property
    def is_dependency_wakeup(self) -> bool:
        """True when a Check Run or Check Suite event, not a new head, started this run."""
        return self.event_name in DEPENDENCY_WAKEUP_EVENT_NAMES


@dataclass(frozen=True)
class ReconcileResult:
    action: str
    reason: str = ""


@dataclass(frozen=True)
class PullRequestView:
    number: int
    state: str
    is_draft: bool
    author_association: str
    head_sha: str
    head_repository_id: int | None
    base_repository_id: int | None
    labels: frozenset[str] = frozenset()

    @classmethod
    def from_api(cls, payload: Mapping[str, Any]) -> "PullRequestView":
        head_repository = payload["head"].get("repo") or {}
        return cls(
            number=payload["number"],
            state=payload["state"],
            is_draft=bool(payload.get("draft", False)),
            author_association=str(payload.get("author_association") or ""),
            head_sha=payload["head"]["sha"],
            head_repository_id=head_repository.get("id"),
            base_repository_id=payload["base"]["repo"]["id"],
            labels=frozenset(str(label.get("name") or "") for label in payload.get("labels") or ()),
        )

    @property
    def is_fork(self) -> bool:
        return self.head_repository_id != self.base_repository_id


class PullRequestEligibility:
    """Which pull requests, and which events about them, may drive this stage."""

    def __init__(self, config: StageRuntimeConfig) -> None:
        self._config = config

    def ineligible_reason(self, pull: PullRequestView, event_head_sha: str | None) -> str:
        """Return why ``pull`` may not drive the stage, or an empty string when it may."""
        if pull.state != "open":
            return "pull request is not open"
        if pull.is_draft:
            return "pull request is a draft"
        if pull.author_association.upper() not in self._config.trusted_roles:
            return "pull request author is not a trusted role"
        if pull.is_fork and (self._config.deny_forks or self._config.privileged_stage):
            return "fork pull requests may not drive this stage"
        if event_head_sha and event_head_sha.lower() != pull.head_sha.lower():
            return "event is stale: head moved since it fired"
        return ""


@dataclass(frozen=True)
class EligibilityDecision:
    """Whether the stage may run for a pull request head, and why not."""

    outcome: str
    reason: str = ""


@dataclass(frozen=True)
class RouteReading:
    """The route classification of a head: a route, or the reason there is none yet."""

    route: str | None
    reason: str = ""
    is_missing: bool = False


class RouteClassificationReader:
    """Reads the ``RouteClassification`` Check Run the Stagr App published for a head.

    Fails closed: a run that is absent, still running, not bound to this head, or whose title is not
    exactly ``RouteClassification=FAST`` or ``RouteClassification=NORMAL`` yields no route. Only an
    absent or unfinished run is "missing" (worth waiting for); the others are final. Two Stagr runs
    raise ``AmbiguousSignalError``. A run written by another app is ignored, i.e. it is missing.
    """

    def __init__(self, route_rule: RouteRule, check_run_reader: StagrCheckRunReader) -> None:
        self._route_rule = route_rule
        self._check_run_reader = check_run_reader

    def read(self, head_sha: str) -> RouteReading:
        route_run = self._check_run_reader.find_single(self._route_rule.check_run_name, head_sha)
        if route_run is None or route_run.status != CHECK_RUN_STATUS_COMPLETED:
            return RouteReading(None, "route classification has not been published yet", True)
        if route_run.head_sha != head_sha:
            return RouteReading(None, "route classification is not bound to this head")
        title = route_run.title or ""
        route = title[len(ROUTE_TITLE_PREFIX):] if title.startswith(ROUTE_TITLE_PREFIX) else ""
        if route not in (ROUTE_FAST, ROUTE_NORMAL):
            return RouteReading(None, "route classification is not FAST or NORMAL")
        return RouteReading(route)


class DependencyGate:
    """Decides from the upstream signals of a head whether this stage may start."""

    def __init__(
        self, dependency_rules: tuple[DependencyRule, ...], check_run_reader: StagrCheckRunReader
    ) -> None:
        self._dependency_rules = dependency_rules
        self._check_run_reader = check_run_reader

    def evaluate(self, head_sha: str) -> EligibilityDecision:
        """Proceed when every upstream passed, fail when one failed, else wait (skip)."""
        waiting_reason = ""
        for rule in self._dependency_rules:
            signal = self._read_upstream_signal(rule, head_sha)
            if signal is None:
                waiting_reason = waiting_reason or (
                    f"dependency '{rule.stage_id}' has not published a valid signal for this head"
                )
            elif signal.state == STATE_FAILED or signal.conclusion == CONCLUSION_FAILED:
                return EligibilityDecision(
                    OUTCOME_DEPENDENCY_FAILED, f"dependency '{rule.stage_id}' failed"
                )
            elif not (signal.state == STATE_COMPLETED and signal.conclusion == CONCLUSION_PASS):
                waiting_reason = waiting_reason or f"dependency '{rule.stage_id}' has not passed"
        if waiting_reason:
            return EligibilityDecision(OUTCOME_SKIP, waiting_reason)
        return EligibilityDecision(OUTCOME_PROCEED)

    def _read_upstream_signal(self, rule: DependencyRule, head_sha: str) -> StageSignal | None:
        upstream_run = self._check_run_reader.find_single(rule.check_run_name, head_sha)
        if upstream_run is None:
            return None
        return deserialize_signal_payload(upstream_run.payload, rule.stage_id, head_sha)


class StageEligibilityEvaluator:
    """May this stage run for this pull request and event? One answer for every mode."""

    def __init__(
        self,
        config: StageRuntimeConfig,
        github_api: GitHubApi,
        repository: str,
        route_wait_attempts: int = 1,
        route_wait_seconds: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._pull_request_eligibility = PullRequestEligibility(config)
        self._check_run_reader = StagrCheckRunReader(github_api, repository, config.publisher_app_id)
        self._dependency_gate = DependencyGate(config.dependency_rules, self._check_run_reader)
        self._route_wait_attempts = max(1, route_wait_attempts)
        self._route_wait_seconds = route_wait_seconds
        self._sleep = sleep

    def evaluate(self, pull: PullRequestView, request: ReconcileRequest) -> EligibilityDecision:
        ineligible_reason = self._pull_request_eligibility.ineligible_reason(
            pull, request.event_head_sha
        )
        if not ineligible_reason:
            ineligible_reason = self._route_skip_reason(pull.head_sha)
        if not ineligible_reason and request.is_dependency_wakeup:
            ineligible_reason = self._final_signal_skip_reason(pull.head_sha)
        if ineligible_reason:
            return EligibilityDecision(OUTCOME_SKIP, ineligible_reason)
        return self._dependency_gate.evaluate(pull.head_sha)

    def _route_skip_reason(self, head_sha: str) -> str:
        route_rule = self._config.route_rule
        if route_rule is None:
            return ""
        route_reader = RouteClassificationReader(route_rule, self._check_run_reader)
        reading = route_reader.read(head_sha)
        for _ in range(self._route_wait_attempts - 1):
            if not reading.is_missing:
                break
            self._sleep(self._route_wait_seconds)
            reading = route_reader.read(head_sha)
        if reading.route is None:
            return reading.reason
        if not route_rule.is_applicable(self._config.stage_id, reading.route):
            return f"stage does not apply to the {reading.route} route"
        return ""

    def _final_signal_skip_reason(self, head_sha: str) -> str:
        """A wake-up never restarts a stage whose own signal is already final for this head."""
        own_run = self._check_run_reader.find_single(self._config.check_run_name, head_sha)
        if own_run is None:
            return ""
        stage_id = self._config.stage_id
        for state, conclusion in (
            (STATE_COMPLETED, CONCLUSION_PASS),
            (STATE_FAILED, CONCLUSION_FAILED),
        ):
            if own_run.has_signal_for(stage_id, head_sha, state, conclusion):
                return "signal is already final; only a re-run of the execute job retries"
        return ""


class EligibilityChecker:
    """``eligibility`` mode: may the backend be invoked for the pull request this event is about?"""

    def __init__(
        self,
        evaluator: StageEligibilityEvaluator,
        github_api: GitHubApi,
        repository: str,
    ) -> None:
        self._evaluator = evaluator
        self._github_api = github_api
        self._repository = repository

    def check(self, request: ReconcileRequest) -> ReconcileResult:
        pull = PullRequestView.from_api(
            self._github_api.get_object(f"repos/{self._repository}/pulls/{request.pull_number}")
        )
        decision = self._evaluator.evaluate(pull, request)
        if decision.outcome == OUTCOME_PROCEED:
            return ReconcileResult(ACTION_ELIGIBLE)
        return ReconcileResult(ACTION_SKIPPED, decision.reason)


class StageReconciler:
    def __init__(
        self, config: StageRuntimeConfig, github_api: GitHubApi, repository: str, run_url: str | None
    ) -> None:
        owner, _, name = repository.partition("/")
        self._config = config
        self._github_api = github_api
        self._repository = repository
        self._eligibility = StageEligibilityEvaluator(config, github_api, repository)
        self._check_run_store = CheckRunStore(config, github_api, repository, run_url)
        self._evidence_evaluator = CommentEvidenceEvaluator(config.evidence_rules)
        self._gate_evaluator = GateEvaluator(config.gate_rule, github_api, owner, name)

    def reconcile_pull_request(
        self, request: ReconcileRequest, prefetched_pull: Mapping[str, Any] | None = None
    ) -> ReconcileResult:
        pull = PullRequestView.from_api(
            prefetched_pull
            or self._github_api.get_object(f"repos/{self._repository}/pulls/{request.pull_number}")
        )
        decision = self._eligibility.evaluate(pull, request)
        if decision.outcome == OUTCOME_SKIP:
            return ReconcileResult(ACTION_SKIPPED, decision.reason)
        existing = self._check_run_store.find_existing(pull.head_sha)
        if existing is None and request.mode != MODE_PUBLISH:
            return ReconcileResult(ACTION_SKIPPED, self._describe_missing_signal(decision))
        stage_id = self._config.stage_id
        if existing and existing.has_signal_for(
            stage_id, pull.head_sha, STATE_COMPLETED, CONCLUSION_PASS
        ):
            return ReconcileResult(ACTION_SKIPPED, "signal is already completed and passed")
        if (
            existing
            and request.mode != MODE_PUBLISH
            and existing.has_signal_for(stage_id, pull.head_sha, STATE_FAILED, CONCLUSION_FAILED)
        ):
            return ReconcileResult(ACTION_SKIPPED, "signal failed; re-run the stage to retry")
        if decision.outcome == OUTCOME_DEPENDENCY_FAILED:
            signal: StageSignal | None = FAILED_SIGNAL
        else:
            signal = self._derive_signal_or_failed(request, pull)
        if signal is None:
            return ReconcileResult(ACTION_SKIPPED, "completion evidence is absent")
        action = self._check_run_store.write_if_changed(existing, pull.head_sha, signal)
        return ReconcileResult(action)

    def _describe_missing_signal(self, decision: EligibilityDecision) -> str:
        """Why a wakeup or the sweep did nothing when the stage has no signal for the head yet.

        Only the execute job creates a stage's Check Run and invokes its backend, so a stage whose
        dependencies have passed (or failed) while only a wakeup or the sweep is looking waits for
        its next execute run.
        """
        if decision.outcome == OUTCOME_DEPENDENCY_FAILED:
            return f"{decision.reason}; the execute job publishes the failure"
        if self._config.dependency_rules:
            return (
                "dependencies have passed but the stage has not started; "
                "the execute job starts it and creates its signal"
            )
        return "no signal has been published for this head"

    def _derive_signal_or_failed(
        self, request: ReconcileRequest, pull: PullRequestView
    ) -> StageSignal | None:
        try:
            return self._derive_signal(request, pull)
        except GitHubApiError as error:
            print(f"::warning::Stage {self._config.stage_id}: evaluation failed: {error}")
            return FAILED_SIGNAL

    def _derive_signal(self, request: ReconcileRequest, pull: PullRequestView) -> StageSignal | None:
        is_publish = request.mode == MODE_PUBLISH
        if is_publish and request.job_status == JOB_STATUS_FAILURE:
            return FAILED_SIGNAL
        issue_comments = self._github_api.get_items(
            f"repos/{self._repository}/issues/{pull.number}/comments?per_page=100"
        )
        evidence = self._evidence_evaluator.evaluate(issue_comments, pull.head_sha)
        if not evidence.is_present:
            return RUNNING_SIGNAL if is_publish else None
        conclusion = self._gate_evaluator.evaluate(pull.number, pull.head_sha, evidence.matched_bodies)
        return StageSignal(STATE_COMPLETED, conclusion)


class OpenPullRequestSweeper:
    """Runs the reconciliation routine for every open pull request, isolating per-PR failures."""

    def __init__(self, github_api: GitHubApi, repository: str, reconciler: StageReconciler) -> None:
        self._github_api = github_api
        self._repository = repository
        self._reconciler = reconciler

    def sweep(self) -> int:
        """Return the number of pull requests that could not be reconciled."""
        open_pulls = self._github_api.get_items(
            f"repos/{self._repository}/pulls?state=open&sort=updated&direction=asc&per_page=100"
        )
        failure_count = 0
        for pull in open_pulls:
            try:
                result = self._reconciler.reconcile_pull_request(
                    ReconcileRequest(MODE_SWEEP, pull["number"]), prefetched_pull=pull
                )
            except (GitHubApiError, AmbiguousSignalError) as error:
                failure_count += 1
                print(f"::error::Pull request #{pull['number']}: {error}")
                continue
            print(f"pull request #{pull['number']}: {result.action} {result.reason}".rstrip())
        return failure_count


# ---------------------------------------------------------------------------
# Backend invocation: "should the backend be asked to run for this head, and has it been?"
# ---------------------------------------------------------------------------


def format_lease_timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(LEASE_TIMESTAMP_FORMAT)


def format_lease_marker(stage_id: str, head_sha: str, expires_at: datetime) -> str:
    """Return the in-flight marker for a (stage, head) pair, valid until ``expires_at``."""
    return (
        f"<!-- stagr:stage:{stage_id}:{head_sha.lower()}"
        f":expires:{format_lease_timestamp(expires_at)} -->"
    )


def parse_lease_expiries(body: str, stage_id: str, head_sha: str) -> list[datetime]:
    """Return the expiry of every well-formed marker for exactly this stage and full head SHA."""
    marker_pattern = re.compile(
        "<!-- stagr:stage:" + re.escape(stage_id) + ":" + re.escape(head_sha.lower())
        + ":expires:(" + LEASE_TIMESTAMP_PATTERN + ") -->"
    )
    expiries: list[datetime] = []
    for marker_match in marker_pattern.finditer(body):
        try:
            parsed = datetime.strptime(marker_match.group(1), LEASE_TIMESTAMP_FORMAT)
        except ValueError:
            continue  # e.g. month 13: not a real timestamp, so not a marker
        expiries.append(parsed.replace(tzinfo=timezone.utc))
    return expiries


@dataclass(frozen=True)
class PostingAccount:
    """The account that owns the invoke token, i.e. the only author whose markers are believed."""

    account_id: int
    login: str
    account_type: str

    @classmethod
    def from_api(cls, payload: Mapping[str, Any]) -> "PostingAccount":
        account_id, login, account_type = payload.get("id"), payload.get("login"), payload.get("type")
        if not isinstance(account_id, int) or not login or not account_type:
            raise GitHubApiError("GET /user did not return an account with id, login and type")
        return cls(account_id=account_id, login=str(login), account_type=str(account_type))

    def is_author_of(self, comment: Mapping[str, Any]) -> bool:
        """Strict match on id, login and type, so a look-alike name or a Bot twin never matches."""
        author = comment.get("user") or {}
        return (
            author.get("id") == self.account_id
            and str(author.get("login", "")).lower() == self.login.lower()
            and author.get("type") == self.account_type
        )


class InFlightLeaseGuard:
    """Finds a still-valid in-flight marker among the comments of a pull request."""

    def __init__(self, config: StageRuntimeConfig, posting_account: PostingAccount) -> None:
        self._config = config
        self._posting_account = posting_account

    def has_unexpired_marker(
        self, issue_comments: list[Mapping[str, Any]], head_sha: str, now: datetime
    ) -> bool:
        return any(
            expires_at > now
            for comment in issue_comments
            if self._is_trusted_posting(comment)
            for expires_at in parse_lease_expiries(
                comment.get("body") or "", self._config.stage_id, head_sha
            )
        )

    def _is_trusted_posting(self, comment: Mapping[str, Any]) -> bool:
        association = str(comment.get("author_association") or "").upper()
        return (
            self._posting_account.is_author_of(comment)
            and association in self._config.trusted_roles
        )


class BackendInvoker:
    """Posts the backend invocation comment unless it is already done or already in flight."""

    def __init__(
        self,
        config: StageRuntimeConfig,
        github_api: GitHubApi,
        repository: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._config = config
        self._github_api = github_api
        self._repository = repository
        self._clock = clock
        self._eligibility = PullRequestEligibility(config)
        self._evidence_evaluator = CommentEvidenceEvaluator(config.evidence_rules)

    def invoke_if_needed(self, request: ReconcileRequest) -> ReconcileResult:
        invocation_rule = self._config.invocation_rule
        pull = PullRequestView.from_api(
            self._github_api.get_object(f"repos/{self._repository}/pulls/{request.pull_number}")
        )
        ineligible_reason = self._eligibility.ineligible_reason(pull, request.event_head_sha)
        if ineligible_reason:
            return ReconcileResult(ACTION_SKIPPED, ineligible_reason)
        if self._config.hand_off_label and self._config.hand_off_label in pull.labels:
            return ReconcileResult(
                ACTION_SKIPPED, "the pull request was handed to a human; remove the hand-off label to resume reviews"
            )
        comments_path = f"repos/{self._repository}/issues/{pull.number}/comments"
        issue_comments = self._github_api.get_items(f"{comments_path}?per_page=100")
        if self._evidence_evaluator.evaluate(issue_comments, pull.head_sha).is_present:
            return ReconcileResult(ACTION_SKIPPED, "completion evidence already exists for this head")
        posting_account = PostingAccount.from_api(self._github_api.get_object("user"))
        now = self._clock()
        lease_guard = InFlightLeaseGuard(self._config, posting_account)
        if lease_guard.has_unexpired_marker(issue_comments, pull.head_sha, now):
            return ReconcileResult(ACTION_SKIPPED, "an invocation for this head is still in flight")
        expires_at = now + timedelta(minutes=invocation_rule.lease_minutes)
        marker = format_lease_marker(self._config.stage_id, pull.head_sha, expires_at)
        self._github_api.send_json(
            "POST", comments_path, {"body": f"{invocation_rule.body}\n\n{marker}"}
        )
        return ReconcileResult(ACTION_INVOKED)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_run_url(environment: Mapping[str, str]) -> str | None:
    server_url = environment.get("GITHUB_SERVER_URL")
    run_id = environment.get("GITHUB_RUN_ID")
    repository = environment.get("GITHUB_REPOSITORY")
    if server_url and run_id and repository:
        return f"{server_url}/{repository}/actions/runs/{run_id}"
    return None


def read_current_utc_time() -> datetime:
    return datetime.now(timezone.utc)


def build_invoke_process_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """The environment ``gh`` runs with in invoke mode: the backend credential, and only it."""
    backend_token = environment.get(TRUSTED_COMMENTER_TOKEN_VARIABLE)
    if not backend_token:
        raise RuntimeConfigError(f"invoke mode needs {TRUSTED_COMMENTER_TOKEN_VARIABLE}")
    return {**environment, "GH_TOKEN": backend_token}


def record_proceed_output(environment: Mapping[str, str], is_eligible: bool) -> None:
    """Tell later steps of the job whether to go on (only constants are ever written)."""
    output_path = environment.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output_file:
            output_file.write(f"{STEP_OUTPUT_PROCEED_NAME}={'true' if is_eligible else 'false'}\n")


def main(
    environment: Mapping[str, str] | None = None,
    github_api: GitHubApi | None = None,
    clock: Callable[[], datetime] = read_current_utc_time,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    environment = os.environ if environment is None else environment
    try:
        config = StageRuntimeConfig.from_json_text(environment["STAGR_STAGE_CONFIG"])
        mode = environment["STAGR_MODE"]
        repository = environment["GITHUB_REPOSITORY"]
        if mode == MODE_INVOKE:
            github_api = github_api or GitHubCliApi(
                process_environment=build_invoke_process_environment(environment)
            )
            handle_pull_request = BackendInvoker(
                config, github_api, repository, clock
            ).invoke_if_needed
        else:
            github_api = github_api or GitHubCliApi()
            reconciler = StageReconciler(config, github_api, repository, build_run_url(environment))
            if mode == MODE_SWEEP:
                return 1 if OpenPullRequestSweeper(github_api, repository, reconciler).sweep() else 0
            if mode == MODE_ELIGIBILITY:
                evaluator = StageEligibilityEvaluator(
                    config, github_api, repository, ROUTE_WAIT_ATTEMPTS, ROUTE_WAIT_SECONDS, sleep
                )
                handle_pull_request = EligibilityChecker(evaluator, github_api, repository).check
            elif mode in (MODE_PUBLISH, MODE_RECONCILE):
                handle_pull_request = reconciler.reconcile_pull_request
            else:
                raise RuntimeConfigError(f"Unknown STAGR_MODE {mode!r}")
        if not environment.get("STAGR_PULL_NUMBER"):
            print(f"Stage {config.stage_id}: no pull request context; no signal published.")
            if mode == MODE_ELIGIBILITY:
                record_proceed_output(environment, False)
            return 0
        request = ReconcileRequest(
            mode=mode,
            pull_number=int(environment["STAGR_PULL_NUMBER"]),
            event_head_sha=environment.get("STAGR_EVENT_HEAD_SHA") or None,
            job_status=environment.get("STAGR_JOB_STATUS") or None,
            event_name=environment.get("STAGR_EVENT_NAME") or None,
        )
        result = handle_pull_request(request)
    except (KeyError, ValueError, GitHubApiError, AmbiguousSignalError) as error:
        print(f"::error::Stage signal runtime failed: {error}")
        return 1
    if mode == MODE_ELIGIBILITY:
        record_proceed_output(environment, result.action == ACTION_ELIGIBLE)
    print(
        f"Stage {config.stage_id} pull request #{request.pull_number}: "
        f"{result.action} {result.reason}".rstrip()
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
