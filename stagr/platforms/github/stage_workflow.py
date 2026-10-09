"""Stage workflow assembly for GitHubPlatformRenderer (issues #194, #206, #205 and #207).

Builds the text of ``.github/workflows/stage-<id>.yml``. One workflow file per stage holds three
jobs, each guarded by an explicit ``github.event_name`` condition so that a wakeup can never
re-run a backend and an invocation trigger can never run the sweep:

- ``execute``   declared triggers (PR events, manual, issue label), plus, for a stage with
                dependencies, the ``check_run`` / ``check_suite`` wake-ups of its upstream stages.
                Step 1 acquires the App token. Step 2 ("Check eligibility", #207) decides through
                the shared runtime whether the backend may be invoked (trust, fork policy, current
                head, route, dependencies) and sets the output ``proceed``. One step (#205) checks
                the completion guard and the in-flight lease and posts the ``PR_COMMENT``
                invocation, holding only the backend secret; it runs only when ``proceed`` is
                ``true``. The last step
                publishes the result signal through the shared runtime (#206) and is the ONLY place
                a Check Run is created.
- ``reconcile`` ``issue_comment`` wakeup for a pull request, only when the comment author is a
                declared evidence producer. Updates an existing Check Run in place.
- ``sweep``     scheduled backstop over every open pull request. Updates in place.

A stage whose backend runs as a CI component (``CI_COMPONENT``, e.g. Codex on an API key) splits
``execute`` so that no job holds both the App token and the backend credential:

- ``execute``   App token: eligibility, then the ``ci_gate`` step that decides whether the
                component runs for the head (once per head, never after hand-off).
- ``review``    the backend credential and a read-only checkout, no App token: runs the component
                and hands its findings on as a job output.
- ``publish``   App token: posts the findings as one review plus the completion marker
                (``post_review``), then publishes the result signal exactly like ``execute`` does.

``reconcile`` and ``sweep`` observe the stage's evidence after the invocation was posted. Neither
holds the backend secret, so neither can post an invocation: an expired in-flight lease is recovered the next time ``execute``
runs (see design-docs/08-github-codex-mapping.md).

All per-stage data reaches the runtime as one JSON document in the workflow ``env`` (see
``stage_signal_config``); the runtime source is embedded once and run with ``python3 -c``.
"""
from __future__ import annotations

from pathlib import Path

import json

from stagr.core.enums import InvocationKind, StageTrigger
from stagr.core.models import ExecutionPlan, NormalizedStage
from stagr.platforms.github.action_pins import (
    APP_TOKEN_ACTION_REF,
    CHECKOUT_ACTION_REF,
    CODEX_ACTION_REF,
    CODEX_CLI_VERSION,
)
from stagr.platforms.github.stage_expressions import (
    build_concurrency_key_expression,
    build_event_head_sha_expression,
    build_pull_number_expression,
    build_wakeup_relevance_expression,
)
from stagr.platforms.github.stage_signal_config import StageSignalConfig

RUNTIME_SCRIPT_PATH = Path(__file__).parent / "runtime" / "stage_signal_runtime.py"
CI_RUNTIME_SCRIPT_PATH = Path(__file__).parent / "runtime" / "ci_component_runtime.py"

# Sweep cadence (design-doc 06 suggests every five minutes; five is also GitHub's minimum).
SWEEP_CRON_SCHEDULE = "*/5 * * * *"

_PULL_REQUEST_TARGET_EVENTS_FOR_PR_OPENED = ("opened", "reopened", "ready_for_review")
_PULL_REQUEST_TARGET_EVENTS_FOR_PR_UPDATED = ("synchronize",)

_APP_TOKEN_OUTPUT_EXPRESSION = "${{ steps.app-token.outputs.token }}"
_RUN_RUNTIME_COMMAND = 'python3 -c "$STAGR_RUNTIME_SCRIPT"'
_RUN_CI_RUNTIME_COMMAND = 'python3 -c "$STAGR_CI_RUNTIME_SCRIPT"'
_EVENT_NAME_EXPRESSION = "${{ github.event_name }}"
# Steps that act on the pull request run only when the eligibility step said so; the implicit
# success() also keeps them from running after a failed eligibility step.
_PROCEED_CONDITION = "${{ steps.eligibility.outputs.proceed == 'true' }}"


def build_on_section(
    stage_triggers: tuple[StageTrigger, ...],
    has_dependency_wakeups: bool = False,
) -> str:
    """Return the indented YAML lines for the ``on:`` trigger section.

    Merges PR_OPENED and PR_UPDATED into a single pull_request_target block when both are present.
    MANUAL becomes workflow_dispatch and ISSUE_LABELED becomes an issues block. Reconciliation
    wakeups (``issue_comment`` and the scheduled sweep) are renderer-internal, not StageTriggers,
    and are always added, because a stage's completion is observed later as evidence. The
    ``check_run`` and ``check_suite`` wake-ups (an upstream signal may have changed) are added only
    for a stage that declares dependencies; the job conditions decide which of those events matter.
    """
    pull_request_target_events: list[str] = []
    include_workflow_dispatch = False
    issues_events: list[str] = []

    for trigger in stage_triggers:
        if trigger is StageTrigger.PR_OPENED:
            pull_request_target_events.extend(_PULL_REQUEST_TARGET_EVENTS_FOR_PR_OPENED)
        elif trigger is StageTrigger.PR_UPDATED:
            pull_request_target_events.extend(_PULL_REQUEST_TARGET_EVENTS_FOR_PR_UPDATED)
        elif trigger is StageTrigger.MANUAL:
            include_workflow_dispatch = True
        elif trigger is StageTrigger.ISSUE_LABELED:
            issues_events.append("labeled")

    lines: list[str] = []
    if pull_request_target_events:
        lines.append("  pull_request_target:\n")
        lines.append(f"    types: [{', '.join(pull_request_target_events)}]\n")
    if include_workflow_dispatch:
        lines.append("  workflow_dispatch:\n")
    if issues_events:
        lines.append("  issues:\n")
        lines.append(f"    types: [{', '.join(issues_events)}]\n")
    if has_dependency_wakeups:
        lines.append("  check_run:\n")
        lines.append("    types: [completed]\n")
        lines.append("  check_suite:\n")
        lines.append("    types: [completed]\n")
    lines.append("  issue_comment:\n")
    lines.append("    types: [created, edited]\n")
    lines.append("  schedule:\n")
    lines.append(f'    - cron: "{SWEEP_CRON_SCHEDULE}"\n')
    return "".join(lines)


def build_stage_workflow_yaml(
    plan: ExecutionPlan,
    stage: NormalizedStage,
    signal_config: StageSignalConfig,
    on_section: str,
    publisher_app_id: str,
    private_key_secret_name: str,
    component_starter_bots: tuple[str, ...] = (),
) -> str:
    """Return the complete GitHub Actions workflow YAML string for the stage."""
    token_step = _build_token_acquisition_step(publisher_app_id, private_key_secret_name)
    wakeup_relevance = _build_wakeup_relevance(signal_config, publisher_app_id)
    is_ci_component = plan.invocation.kind is InvocationKind.CI_COMPONENT
    if is_ci_component:
        stage_jobs = _build_ci_component_jobs(
            plan, stage, signal_config, token_step, wakeup_relevance, component_starter_bots
        )
    else:
        stage_jobs = [_build_execute_job(plan, stage, signal_config, token_step, wakeup_relevance)]
    jobs = [
        *stage_jobs,
        _build_reconcile_job(signal_config, token_step),
        _build_sweep_job(token_step),
    ]
    return (
        f'name: "Stagr stage: {stage.id}"\n'
        "\n"
        "on:\n"
        f"{on_section}"
        "\n"
        "concurrency:\n"
        f'  group: "stagr-{stage.id}-{build_concurrency_key_expression(wakeup_relevance)}"\n'
        "  cancel-in-progress: false\n"
        "\n"
        f"{_build_workflow_env(signal_config, is_ci_component)}"
        "\n"
        "jobs:\n"
        + "\n".join(jobs)
    )


def _build_wakeup_relevance(signal_config: StageSignalConfig, publisher_app_id: str) -> str | None:
    """The expression that selects the Check Run / Check Suite events to wake for, if any."""
    if not signal_config.has_dependencies:
        return None
    return build_wakeup_relevance_expression(
        publisher_app_id, signal_config.upstream_check_run_names
    )


def _build_workflow_env(signal_config: StageSignalConfig, is_ci_component: bool) -> str:
    runtime_script = RUNTIME_SCRIPT_PATH.read_text(encoding="utf-8")
    environment = (
        "env:\n"
        "  STAGR_STAGE_CONFIG: |\n"
        f"    {signal_config.to_json_text()}\n"
        "  STAGR_RUNTIME_SCRIPT: |\n"
        f"{_indent_block(runtime_script, '    ')}"
    )
    if is_ci_component:
        ci_runtime_script = CI_RUNTIME_SCRIPT_PATH.read_text(encoding="utf-8")
        environment += f"  STAGR_CI_RUNTIME_SCRIPT: |\n{_indent_block(ci_runtime_script, '    ')}"
    return environment


def _indent_block(text: str, indentation: str) -> str:
    """Indent every non-blank line; blank lines stay empty so the YAML block scalar is stable."""
    return "".join(
        (f"{indentation}{line}" if line.strip() else "") + "\n" for line in text.splitlines()
    )


def _build_token_acquisition_step(publisher_app_id: str, private_key_secret_name: str) -> str:
    private_key_expression = f"${{{{ secrets.{private_key_secret_name} }}}}"
    return (
        "      - name: Acquire Stagr App installation token\n"
        "        id: app-token\n"
        f"        uses: {APP_TOKEN_ACTION_REF}\n"
        "        with:\n"
        f'          app-id: "{publisher_app_id}"\n'
        f'          private-key: "{private_key_expression}"\n'
    )


def _declared_event_names(stage: NormalizedStage) -> list[str]:
    event_name_by_trigger = {
        StageTrigger.PR_OPENED: "pull_request_target",
        StageTrigger.PR_UPDATED: "pull_request_target",
        StageTrigger.MANUAL: "workflow_dispatch",
        StageTrigger.ISSUE_LABELED: "issues",
    }
    declared = {event_name_by_trigger[trigger] for trigger in stage.triggers}
    return [name for name in ("pull_request_target", "workflow_dispatch", "issues") if name in declared]


def _build_execute_condition(stage: NormalizedStage, wakeup_relevance: str | None) -> str:
    """The events that start the stage: its declared triggers, plus upstream wake-ups if any."""
    event_conditions = [f"github.event_name == '{name}'" for name in _declared_event_names(stage)]
    execute_condition = " || ".join(event_conditions) or "false"
    if wakeup_relevance is not None:
        execute_condition += f" || {wakeup_relevance}"
    return execute_condition


def _build_execute_job(
    plan: ExecutionPlan,
    stage: NormalizedStage,
    signal_config: StageSignalConfig,
    token_step: str,
    wakeup_relevance: str | None,
) -> str:
    execute_condition = _build_execute_condition(stage, wakeup_relevance)
    event_lines = _build_event_environment_lines(signal_config)
    return (
        "  execute:\n"
        f'    if: "${{{{ {execute_condition} }}}}"\n'
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        "      pull-requests: read\n"
        "      contents: read\n"
        "    steps:\n"
        f"{token_step}"
        "\n"
        "      - name: Check eligibility\n"
        "        id: eligibility\n"
        "        env:\n"
        f'          GH_TOKEN: "{_APP_TOKEN_OUTPUT_EXPRESSION}"\n'
        "          STAGR_MODE: eligibility\n"
        f"{''.join(event_lines)}"
        f"        run: {_RUN_RUNTIME_COMMAND}\n"
        "\n"
        f"{_build_invocation_steps(plan, signal_config)}"
        "\n"
        "      - name: Publish result signal\n"
        '        if: "${{ !cancelled() }}"\n'
        "        env:\n"
        f'          GH_TOKEN: "{_APP_TOKEN_OUTPUT_EXPRESSION}"\n'
        "          STAGR_MODE: publish\n"
        f"{''.join(event_lines)}"
        '          STAGR_JOB_STATUS: "${{ job.status }}"\n'
        f"        run: {_RUN_RUNTIME_COMMAND}\n"
    )


# The publish job reports a failure of either earlier job; otherwise its own status.
_CI_JOB_STATUS_EXPRESSION = (
    "${{ (needs.execute.result == 'failure' || needs.review.result == 'failure') "
    "&& 'failure' || job.status }}"
)


def _build_ci_component_jobs(
    plan: ExecutionPlan,
    stage: NormalizedStage,
    signal_config: StageSignalConfig,
    token_step: str,
    wakeup_relevance: str | None,
    component_starter_bots: tuple[str, ...],
) -> list[str]:
    """Return the ``execute``, ``review`` and ``publish`` jobs of a CI-component stage."""
    event_lines = _build_event_environment_lines(signal_config)
    gate_lines = _build_event_environment_lines(signal_config, include_event_name=False)
    execute_job = (
        "  execute:\n"
        f'    if: "${{{{ {_build_execute_condition(stage, wakeup_relevance)} }}}}"\n'
        "    runs-on: ubuntu-latest\n"
        "    permissions: {}\n"
        "    outputs:\n"
        '      run_backend: "${{ steps.ci-gate.outputs.run_backend }}"\n'
        '      head_sha: "${{ steps.ci-gate.outputs.head_sha }}"\n'
        '      base_sha: "${{ steps.ci-gate.outputs.base_sha }}"\n'
        "    steps:\n"
        f"{token_step}"
        "\n"
        "      - name: Check eligibility\n"
        "        id: eligibility\n"
        "        env:\n"
        f'          GH_TOKEN: "{_APP_TOKEN_OUTPUT_EXPRESSION}"\n'
        "          STAGR_MODE: eligibility\n"
        f"{''.join(event_lines)}"
        f"        run: {_RUN_RUNTIME_COMMAND}\n"
        "\n"
        "      - name: Decide whether the review runs for this head\n"
        "        id: ci-gate\n"
        f'        if: "{_PROCEED_CONDITION}"\n'
        "        env:\n"
        f'          GH_TOKEN: "{_APP_TOKEN_OUTPUT_EXPRESSION}"\n'
        "          STAGR_MODE: ci_gate\n"
        f"{''.join(gate_lines)}"
        f"        run: {_RUN_CI_RUNTIME_COMMAND}\n"
    )
    publish_job = (
        "  publish:\n"
        "    needs: [execute, review]\n"
        "    if: \"${{ !cancelled() && needs.execute.result != 'skipped' }}\"\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 10\n"
        "    permissions: {}\n"
        "    steps:\n"
        f"{token_step}"
        "\n"
        "      - name: Post the review findings\n"
        "        if: \"${{ needs.review.result == 'success' }}\"\n"
        "        env:\n"
        f'          GH_TOKEN: "{_APP_TOKEN_OUTPUT_EXPRESSION}"\n'
        "          STAGR_MODE: post_review\n"
        f"{gate_lines[0]}"
        '          STAGR_REVIEWED_HEAD_SHA: "${{ needs.execute.outputs.head_sha }}"\n'
        '          STAGR_REVIEW_FINDINGS: "${{ needs.review.outputs.findings }}"\n'
        f"        run: {_RUN_CI_RUNTIME_COMMAND}\n"
        "\n"
        "      - name: Publish result signal\n"
        '        if: "${{ !cancelled() }}"\n'
        "        env:\n"
        f'          GH_TOKEN: "{_APP_TOKEN_OUTPUT_EXPRESSION}"\n'
        "          STAGR_MODE: publish\n"
        f"{''.join(event_lines)}"
        f'          STAGR_JOB_STATUS: "{_CI_JOB_STATUS_EXPRESSION}"\n'
        f"        run: {_RUN_RUNTIME_COMMAND}\n"
    )
    return [execute_job, _build_codex_review_job(plan, component_starter_bots), publish_job]


def _build_codex_review_job(plan: ExecutionPlan, component_starter_bots: tuple[str, ...]) -> str:
    """The job that runs Codex read-only on the head with the API key, and no App token.

    The checkout keeps no credentials and Codex runs without sudo in a read-only sandbox. Codex
    reads no ``AGENTS.md`` from the checkout (``project_doc_max_bytes=0``): the change under review
    must not be able to instruct its own reviewer. The CLI version is pinned like the action. The
    action refuses a run started by a bot it does not list, so ``component_starter_bots`` (already
    vetted by the eligibility step's trust checks) are listed.
    """
    params = plan.invocation.params
    (credential,) = (
        secret for secret in plan.required_secrets if secret.alias == params["credential_alias"]
    )
    prompt = (
        f"{params['prompt']}\n\n"
        "Base commit: ${{ needs.execute.outputs.base_sha }}\n"
        "Head commit: ${{ needs.execute.outputs.head_sha }}"
    )
    optional_inputs = ""
    if component_starter_bots:
        optional_inputs += f"          allow-bot-users: {','.join(component_starter_bots)}\n"
    if params.get("model"):
        optional_inputs += f"          model: {params['model']}\n"
    return (
        "  review:\n"
        "    needs: execute\n"
        "    if: \"${{ needs.execute.outputs.run_backend == 'true' }}\"\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 30\n"
        "    permissions:\n"
        "      contents: read\n"
        "    outputs:\n"
        '      findings: "${{ steps.findings.outputs.findings }}"\n'
        "    steps:\n"
        "      - name: Check out the pull request head\n"
        f"        uses: {CHECKOUT_ACTION_REF}\n"
        "        with:\n"
        '          ref: "${{ needs.execute.outputs.head_sha }}"\n'
        "          fetch-depth: 0\n"
        "          persist-credentials: false\n"
        "\n"
        "      - name: Review the change with Codex\n"
        f"        uses: {CODEX_ACTION_REF}\n"
        "        with:\n"
        f'          openai-api-key: "${{{{ secrets.{credential.env_name} }}}}"\n'
        f"          prompt: {json.dumps(prompt)}\n"
        f"          output-schema: {json.dumps(params['output_schema'])}\n"
        '          output-file: "${{ runner.temp }}/stagr-review-findings.json"\n'
        "          sandbox: read-only\n"
        "          safety-strategy: drop-sudo\n"
        f'          codex-version: "{CODEX_CLI_VERSION}"\n'
        """          codex-args: '["--config", "project_doc_max_bytes=0"]'\n"""
        f"          effort: {params['effort']}\n"
        f"{optional_inputs}"
        "\n"
        "      - name: Hand the findings to the publish job\n"
        "        id: findings\n"
        "        run: |\n"
        '          delimiter="stagr_findings_$(cat /proc/sys/kernel/random/uuid)"\n'
        '          { echo "findings<<${delimiter}"; cat "${RUNNER_TEMP}/stagr-review-findings.json"; echo; echo "${delimiter}"; } >> "${GITHUB_OUTPUT}"\n'
    )


def _build_event_environment_lines(
    signal_config: StageSignalConfig, include_event_name: bool = True
) -> tuple[str, ...]:
    """The env lines that hand the runtime what the triggering event says (data only, no logic).

    A stage with dependencies also reads the pull request and head of a ``check_run`` /
    ``check_suite`` wake-up. The event name lets the runtime tell such a wake-up apart; the invoke
    step does not need it.
    """
    has_wakeups = signal_config.has_dependencies
    lines = (
        f'          STAGR_PULL_NUMBER: "{build_pull_number_expression(has_wakeups)}"\n',
        f'          STAGR_EVENT_HEAD_SHA: "{build_event_head_sha_expression(has_wakeups)}"\n',
    )
    if include_event_name:
        lines += (f'          STAGR_EVENT_NAME: "{_EVENT_NAME_EXPRESSION}"\n',)
    return lines


def _build_invocation_steps(plan: ExecutionPlan, signal_config: StageSignalConfig) -> str:
    """Return the step that asks the backend to run, gated on the eligibility step.

    The ``invoke`` step (#205) is one process that checks the completion guard and the in-flight
    lease, then posts the comment, so a skipped invocation is simply a step that exits
    successfully and the publish step still runs.
    """
    invoke_environment_lines = (
        "          STAGR_MODE: invoke\n",
        *_build_event_environment_lines(signal_config, include_event_name=False),
    )
    return (
        "      - name: Invoke backend (idempotent)\n"
        f'        if: "{_PROCEED_CONDITION}"\n'
        f"{_build_backend_env_section(plan, invoke_environment_lines)}"
        f"        run: {_RUN_RUNTIME_COMMAND}\n"
    )


def _build_backend_env_section(plan: ExecutionPlan, leading_lines: tuple[str, ...]) -> str:
    """Return the YAML env block for the backend invocation step.

    Emits ``leading_lines`` (already indented) and then one line per resolved SecretRef, mapping
    alias -> secrets.<env_name>. The App token is never part of this block.
    """
    lines = ["        env:\n", *leading_lines]
    for secret_ref in plan.required_secrets:
        secret_expression = f"${{{{ secrets.{secret_ref.env_name} }}}}"
        lines.append(f'          {secret_ref.alias}: "{secret_expression}"\n')
    return "".join(lines)


def _build_reconcile_job(signal_config: StageSignalConfig, token_step: str) -> str:
    producer_conditions = " || ".join(
        f"github.event.comment.user.login == '{producer}'"
        for producer in signal_config.evidence_producers
    )
    wakeup_condition = (
        "github.event_name == 'issue_comment' && github.event.issue.pull_request"
        f" && ({producer_conditions})"
    )
    return (
        "  reconcile:\n"
        f'    if: "${{{{ {wakeup_condition} }}}}"\n'
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 10\n"
        "    permissions: {}\n"
        "    steps:\n"
        f"{token_step}"
        "\n"
        "      - name: Reconcile result signal\n"
        "        env:\n"
        f'          GH_TOKEN: "{_APP_TOKEN_OUTPUT_EXPRESSION}"\n'
        "          STAGR_MODE: reconcile\n"
        '          STAGR_PULL_NUMBER: "${{ github.event.issue.number }}"\n'
        f"        run: {_RUN_RUNTIME_COMMAND}\n"
    )


def _build_sweep_job(token_step: str) -> str:
    return (
        "  sweep:\n"
        "    if: \"${{ github.event_name == 'schedule' }}\"\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 10\n"
        "    permissions: {}\n"
        "    steps:\n"
        f"{token_step}"
        "\n"
        "      - name: Sweep open pull requests\n"
        "        env:\n"
        f'          GH_TOKEN: "{_APP_TOKEN_OUTPUT_EXPRESSION}"\n'
        "          STAGR_MODE: sweep\n"
        f"        run: {_RUN_RUNTIME_COMMAND}\n"
    )
