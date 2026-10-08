"""In-memory fake GitHub for the stage signal runtime tests.

``FakeGitHubApi`` implements the runtime's ``GitHubApi`` protocol and enforces the rules of the
real Checks API that earlier attempts violated (``output`` needs both ``title`` and ``summary``; a
``conclusion`` only belongs with ``status=completed`` and vice versa). Unknown paths raise, so a
typo in the runtime cannot silently "work" against the fake.

``StrictGhCliShim`` exposes the same fake behind a real ``gh`` executable that REJECTS any flag or
argument shape the runtime is not documented to use, which is what catches command-line mistakes
(such as passing ``--arg`` to ``gh api``) that a Python-level fake cannot see.

Invocation support (issue #205): ``GET /user`` answers for the token in ``GH_TOKEN``; when
``token_accounts`` is non-empty the shim rejects every call made with any other token (a leaked or
wrong credential fails like a 401) and records each token it saw in ``used_tokens``. Posting an
issue comment records the authenticated account as its author.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from stagr.platforms.github.runtime import stage_signal_runtime as runtime

REPOSITORY = "octo/repo"
PUBLISHER_APP_ID = "99001"
COMMENTER_USER = {"id": 7001, "login": "stagr-commenter", "type": "User"}
COMMENTER_ASSOCIATION = "OWNER"
COMMENTER_TOKEN = "commenter-token-value"
ALLOWED_CHECK_RUN_STATUSES = {"queued", "in_progress", "completed"}
ALLOWED_CHECK_RUN_CONCLUSIONS = {
    "success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required",
}


class FakeGitHubApi:
    """State plus behavior of one fake repository."""

    def __init__(self, publisher_app_id: str = PUBLISHER_APP_ID) -> None:
        self.publisher_app_id = publisher_app_id
        self.pull_requests: dict[int, dict[str, Any]] = {}
        self.issue_comments: dict[int, list[dict[str, Any]]] = {}
        self.review_threads: dict[int, list[dict[str, Any]]] = {}
        self.pull_files: dict[int, list[dict[str, Any]]] = {}
        self.reviews: list[dict[str, Any]] = []
        self.check_runs: list[dict[str, Any]] = []
        self.write_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.failing_path_fragments: set[str] = set()
        self.failing_write_fragments: list[str] = []
        self.graphql_page_size = 100
        self.next_identifier = 1000
        self.authenticated_user: dict[str, Any] = dict(COMMENTER_USER)
        self.authenticated_association = COMMENTER_ASSOCIATION
        self.token_accounts: dict[str, dict[str, Any]] = {}
        self.active_token: str | None = None
        self.used_tokens: list[str] = []

    # ---- state serialization (used by the gh shim, which runs in another process) ----

    def to_state(self) -> dict[str, Any]:
        state = dict(vars(self))
        state["failing_path_fragments"] = sorted(self.failing_path_fragments)
        state["pull_requests"] = {str(key): value for key, value in self.pull_requests.items()}
        state["issue_comments"] = {str(key): value for key, value in self.issue_comments.items()}
        state["review_threads"] = {str(key): value for key, value in self.review_threads.items()}
        state["pull_files"] = {str(key): value for key, value in self.pull_files.items()}
        return state

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> "FakeGitHubApi":
        fake = cls(state["publisher_app_id"])
        for name, value in state.items():
            if name in ("pull_requests", "issue_comments", "review_threads", "pull_files"):
                value = {int(key): item for key, item in value.items()}
            elif name == "failing_path_fragments":
                value = set(value)
            elif name == "write_calls":
                value = [tuple(call) for call in value]
            setattr(fake, name, value)
        return fake

    # ---- GitHubApi protocol ----

    def get_object(self, path: str) -> dict[str, Any]:
        self._raise_if_failing(path)
        if path == "user":
            return dict(self._authenticated_account())
        segments = urlsplit(path).path.split("/")
        assert segments[:3] == ["repos", *REPOSITORY.split("/")] and segments[3] == "pulls", path
        return self.pull_requests[int(segments[4])]

    def get_items(self, path: str, items_key: str | None = None) -> list[Any]:
        self._raise_if_failing(path)
        parts = urlsplit(path)
        query = parse_qs(parts.query)
        segments = parts.path.split("/")
        assert segments[:3] == ["repos", *REPOSITORY.split("/")], path
        resource = segments[3:]
        if resource == ["pulls"]:
            assert items_key is None and query["state"] == ["open"], path
            return [pull for pull in self.pull_requests.values() if pull["state"] == "open"]
        if resource[0] == "pulls" and resource[2:] == ["files"]:
            assert items_key is None, path
            return list(self.pull_files.get(int(resource[1]), []))
        if resource[0] == "issues" and resource[2:] == ["comments"]:
            assert items_key is None, path
            return list(self.issue_comments.get(int(resource[1]), []))
        if resource[0] == "commits" and resource[2:] == ["check-runs"]:
            assert items_key == "check_runs" and query["filter"] == ["all"], path
            return [
                run for run in self.check_runs
                if run["head_sha"] == resource[1] and run["name"] == query["check_name"][0]
            ]
        raise AssertionError(f"Unexpected GET path: {path}")

    def run_graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        self._raise_if_failing("graphql")
        assert query == runtime.REVIEW_THREADS_QUERY, "unexpected GraphQL query"
        assert set(variables) == {"owner", "repo", "number", "cursor"}, variables
        assert f"{variables['owner']}/{variables['repo']}" == REPOSITORY
        threads = self.review_threads.get(variables["number"], [])
        start = int(variables["cursor"] or 0)
        end = start + self.graphql_page_size
        return {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": threads[start:end],
            "pageInfo": {"hasNextPage": end < len(threads), "endCursor": str(end)},
        }}}}}

    def send_json(self, method: str, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        self._raise_if_failing(path)
        self.write_calls.append((method, path, json.loads(json.dumps(body))))
        for fragment in self.failing_write_fragments:
            if fragment in path:
                raise runtime.GitHubApiError(f"injected write failure for {fragment}")
        if method == "POST" and path.endswith("/comments"):
            return self._post_issue_comment(path, body)
        if method == "POST" and path.endswith("/reviews"):
            return self._post_review(path, body)
        self._assert_valid_check_run_body(method, body)
        if method == "POST":
            assert path == f"repos/{REPOSITORY}/check-runs", path
            self.next_identifier += 1
            check_run = {"id": self.next_identifier, "app": {"id": int(self.publisher_app_id)}}
            check_run.update(json.loads(json.dumps(body)))
            self.check_runs.append(check_run)
            return check_run
        assert method == "PATCH", method
        identifier = int(path.rsplit("/", 1)[1])
        assert path == f"repos/{REPOSITORY}/check-runs/{identifier}", path
        check_run = next(run for run in self.check_runs if run["id"] == identifier)
        check_run.update(json.loads(json.dumps(body)))
        return check_run

    # ---- helpers for tests ----

    def add_pull_request(self, pull: dict[str, Any]) -> dict[str, Any]:
        self.pull_requests[pull["number"]] = pull
        return pull

    def stage_check_runs(self, head_sha: str) -> list[dict[str, Any]]:
        return [run for run in self.check_runs if run["head_sha"] == head_sha]

    def comments_posted_by_writes(self) -> list[dict[str, Any]]:
        """The bodies of issue comments created through ``send_json``."""
        return [body for method, path, body in self.write_calls
                if method == "POST" and path.endswith("/comments")]

    def _authenticated_account(self) -> dict[str, Any]:
        if not self.token_accounts:
            return self.authenticated_user
        if self.active_token not in self.token_accounts:
            raise runtime.GitHubApiError("HTTP 401: Bad credentials")
        return self.token_accounts[self.active_token]

    def _post_issue_comment(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        segments = path.split("/")
        assert segments[:3] == ["repos", *REPOSITORY.split("/")] and segments[3] == "issues", path
        assert len(segments) == 6 and segments[5] == "comments", path
        assert set(body) == {"body"} and isinstance(body["body"], str) and body["body"].strip(), body
        issue_number = int(segments[4])
        assert issue_number in self.pull_requests, f"no issue {issue_number}"
        self.next_identifier += 1
        comment = {
            "id": self.next_identifier,
            "body": body["body"],
            "user": dict(self._authenticated_account()),
            "author_association": self.authenticated_association,
        }
        self.issue_comments.setdefault(issue_number, []).append(comment)
        return comment

    def _post_review(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """A pull request review, as the real endpoint accepts it: inline comments need a line."""
        segments = path.split("/")
        assert segments[3] == "pulls" and segments[5] == "reviews" and len(segments) == 6, path
        assert set(body) == {"commit_id", "event", "body", "comments"} and body["event"] == "COMMENT", body
        for comment in body["comments"]:
            assert set(comment) == {"path", "line", "side", "body"} and comment["side"] == "RIGHT", comment
        review = {"pull_number": int(segments[4]), "user": dict(self._authenticated_account()), **body}
        self.reviews.append(json.loads(json.dumps(review)))
        return review

    def _raise_if_failing(self, path: str) -> None:
        for fragment in self.failing_path_fragments:
            if fragment in path:
                raise runtime.GitHubApiError(f"injected failure for {fragment}")

    @staticmethod
    def _assert_valid_check_run_body(method: str, body: Mapping[str, Any]) -> None:
        status, conclusion = body.get("status"), body.get("conclusion")
        assert status in ALLOWED_CHECK_RUN_STATUSES, f"invalid status {status!r}"
        assert (conclusion is not None) == (status == "completed"), (
            f"conclusion {conclusion!r} is only valid with status=completed (status={status!r})"
        )
        if conclusion is not None:
            assert conclusion in ALLOWED_CHECK_RUN_CONCLUSIONS, conclusion
        output = body.get("output") or {}
        assert output.get("title") and output.get("summary"), "output needs title and summary"
        if method == "POST":
            assert body.get("name") and body.get("head_sha"), "POST needs name and head_sha"


class StrictGhCliShim:
    """Writes an executable ``gh`` that serves a ``FakeGitHubApi`` and rejects unknown usage."""

    def __init__(self, fake: FakeGitHubApi) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self._state_path = self.directory / "state.json"
        self._state_path.write_text(json.dumps(fake.to_state()))
        script_path = self.directory / "gh"
        repository_root = Path(__file__).resolve().parents[4]
        script_path.write_text(
            f"#!{sys.executable}\n"
            "import importlib.util, sys\n"
            f"sys.path.insert(0, {str(repository_root)!r})\n"
            "spec = importlib.util.spec_from_file_location(\n"
            f"    'fake_github_for_shim', {str(Path(__file__).resolve())!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "sys.modules[spec.name] = module\n"
            "spec.loader.exec_module(module)\n"
            f"sys.exit(module.run_gh_shim(sys.argv[1:], {str(self._state_path)!r}))\n"
        )
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)

    def environment_with_shim_on_path(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        environment = dict(os.environ if base is None else base)
        environment["PATH"] = f"{self.directory}{os.pathsep}{environment.get('PATH', '')}"
        return environment

    def current_state(self) -> FakeGitHubApi:
        return FakeGitHubApi.from_state(json.loads(self._state_path.read_text()))

    def cleanup(self) -> None:
        self._directory.cleanup()


def run_gh_shim(arguments: list[str], state_path: str) -> int:
    """Strictly parse a ``gh`` invocation, execute it on the fake, and persist the new state."""
    fake = FakeGitHubApi.from_state(json.loads(Path(state_path).read_text()))
    fake.active_token = os.environ.get("GH_TOKEN")
    if fake.active_token:
        fake.used_tokens.append(fake.active_token)
    try:
        if fake.token_accounts and fake.active_token not in fake.token_accounts:
            raise runtime.GitHubApiError("HTTP 401: Bad credentials")
        output = _dispatch_gh_arguments(fake, arguments)
    except runtime.GitHubApiError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (AssertionError, ValueError, KeyError) as error:
        print(f"strict gh shim rejected {arguments[:6]!r}: {error!r}", file=sys.stderr)
        return 2
    Path(state_path).write_text(json.dumps(fake.to_state()))
    print(json.dumps(output))
    return 0


def _dispatch_gh_arguments(fake: FakeGitHubApi, arguments: list[str]) -> Any:
    assert arguments and arguments[0] == "api", f"only 'gh api' is supported: {arguments!r}"
    remaining = arguments[1:]
    if remaining[0] == "graphql":
        return _dispatch_graphql(fake, remaining[1:])
    paginate = slurp = False
    method, use_stdin, path = "GET", False, None
    while remaining:
        argument = remaining.pop(0)
        if argument == "--paginate":
            paginate = True
        elif argument == "--slurp":
            slurp = True
        elif argument == "--method":
            method = remaining.pop(0)
        elif argument == "--input":
            assert remaining.pop(0) == "-", "--input must read stdin"
            use_stdin = True
        elif argument.startswith("-"):
            raise AssertionError(f"unknown gh flag {argument!r}")
        else:
            assert path is None, f"unexpected extra argument {argument!r}"
            path = argument
    assert path, "missing API path"
    if method == "GET":
        assert not use_stdin and (paginate == slurp), "--slurp requires --paginate (and vice versa)"
        if not paginate:
            return fake.get_object(path)
        items_key = "check_runs" if "/check-runs" in path else None
        items = fake.get_items(path, items_key)
        return [{"check_runs": items}] if items_key else [items]
    assert use_stdin and not paginate and not slurp, "writes send a JSON body on stdin"
    return fake.send_json(method, path, json.loads(sys.stdin.read()))


def _dispatch_graphql(fake: FakeGitHubApi, remaining: list[str]) -> Any:
    variables: dict[str, Any] = {"cursor": None}
    query = None
    while remaining:
        flag, assignment = remaining.pop(0), remaining.pop(0)
        assert flag in ("-f", "-F"), f"unknown gh graphql flag {flag!r}"
        name, _, value = assignment.partition("=")
        if name == "query":
            assert flag == "-f"
            query = value
        else:
            variables[name] = int(value) if flag == "-F" else value
    assert query, "graphql needs a query"
    return fake.run_graphql(query, variables)
