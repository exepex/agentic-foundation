"""GitHub Actions expressions of a generated stage workflow (issues #194, #206 and #207).

Everything here is rendered into ``if:`` conditions, ``concurrency:`` keys and step ``env:`` values.
Only values Stagr validated at render time reach an expression: the numeric publisher App id and
stage ids that match the stage id pattern (see ``stage_signal_config``). Event payload values are
only ever read by these expressions and handed to the runtime through ``env``; they are never
placed in a ``run:`` script.

Dependency wake-ups (issue #207). A stage with dependencies also listens to ``check_run`` and
``check_suite`` events. Both fire for EVERY check in the repository, so the expressions below decide
which events matter, and the same decision guards the job and picks the concurrency group:

- ``check_run``   relevant only when the Stagr App wrote it, it is one of the upstream stage Check
                  Runs, and it names a pull request of this repository (the payload carries none
                  for a fork, so a fork pull request is not woken this way);
- ``check_suite`` relevant only when the Stagr App wrote it and it names a pull request.

A relevant event joins the concurrency group of its pull request, i.e. the group of every other
event that can create or update this stage's Check Run, so Check Run creation stays serialized. An
irrelevant event gets a group of its own: with ``cancel-in-progress: false`` GitHub keeps one pending
run per group and replaces it when another arrives, so an irrelevant event sharing the group could
displace a real wake-up that is waiting its turn. (Two relevant events can still replace one
another; that is harmless because every run reads the current state instead of trusting its event.)
"""
from __future__ import annotations

_PULL_NUMBER_OF_RELEVANT_WAKEUP = (
    "github.event.check_run.pull_requests[0].number"
    " || github.event.check_suite.pull_requests[0].number"
)


def build_wakeup_relevance_expression(
    publisher_app_id: str, upstream_check_run_names: tuple[str, ...]
) -> str:
    """Return the expression that is truthy for a Check Run / Check Suite event worth waking for."""
    upstream_names = " || ".join(
        f"github.event.check_run.name == '{name}'" for name in upstream_check_run_names
    ) or "false"
    relevant_check_run = (
        f"github.event_name == 'check_run'"
        f" && github.event.check_run.app.id == {publisher_app_id}"
        f" && github.event.check_run.pull_requests[0].number"
        f" && ({upstream_names})"
    )
    relevant_check_suite = (
        f"github.event_name == 'check_suite'"
        f" && github.event.check_suite.app.id == {publisher_app_id}"
        f" && github.event.check_suite.pull_requests[0].number"
    )
    return f"({relevant_check_run}) || ({relevant_check_suite})"


def build_concurrency_key_expression(wakeup_relevance_expression: str | None) -> str:
    """Per-pull-request key for events; a repository-wide key for the scheduled sweep (#194).

    ``wakeup_relevance_expression`` is ``None`` for a stage without dependencies, whose key is
    unchanged.
    """
    key = (
        "github.event_name == 'schedule' && 'sweep'"
        " || github.event.pull_request.number || github.event.issue.number"
    )
    if wakeup_relevance_expression is not None:
        key += (
            f" || ({wakeup_relevance_expression}) && ({_PULL_NUMBER_OF_RELEVANT_WAKEUP})"
            " || (github.event_name == 'check_run' || github.event_name == 'check_suite')"
            " && github.run_id"
        )
    return "${{ " + key + " }}"


def build_pull_number_expression(has_dependency_wakeups: bool) -> str:
    expression = "github.event.pull_request.number"
    if has_dependency_wakeups:
        expression += f" || {_PULL_NUMBER_OF_RELEVANT_WAKEUP}"
    return "${{ " + expression + " }}"


def build_event_head_sha_expression(has_dependency_wakeups: bool) -> str:
    expression = "github.event.pull_request.head.sha"
    if has_dependency_wakeups:
        expression += " || github.event.check_run.head_sha || github.event.check_suite.head_sha"
    return "${{ " + expression + " }}"
