"""Governance workflow YAML generator for GitHubPlatformRenderer.

Phase 2b: generates the merge-gate workflow artifact that reads
StageResultSignal values from Check Runs published by stage execution
artifacts, verifies publisher identity against the rendered Stagr App ID,
and blocks merge when any blocking stage has a BLOCKED or FAILED conclusion.

Security invariants maintained by the generated workflow:
- Publisher verification: only Check Runs whose app.id equals the Stagr App
  ID (rendered as a literal constant) are trusted.  A Check Run from any
  other GitHub App or GITHUB_TOKEN actor is rejected.
- Fail-closed on duplicates: when more than one Check Run matches a stage's
  signal selector on the current head SHA, merge is blocked and an explicit
  error naming the stage and head SHA is surfaced.
- Signal deserialization from output.summary: state and conclusion are read
  from the JSON payload in the Check Run's output.summary field (not from
  the native status/conclusion fields), and schemaVersion is validated first.
- Head SHA binding: only signals whose headSha matches the current PR head
  commit are accepted; stale signals for prior commits are ignored.
- Verdict on the PR head: the workflow publishes its verdict as the
  ``stagr/governance`` Check Run of the Stagr App on the PR head commit. A run
  started by ``check_suite`` executes on the default branch, so the job's own
  check would land on the default-branch commit and never reach the PR; the
  published Check Run is what a branch ruleset requires. An unchanged verdict
  is not re-published, so publishing cannot re-trigger the workflow forever.
"""
from __future__ import annotations

from stagr.core.models import RenderContext, StageResultSpec
from stagr.platforms.github.action_pins import APP_TOKEN_ACTION_REF

# Expected schemaVersion in the StageResultSignal JSON payload stored in
# a Check Run's output.summary field.  Must match the version emitted by
# stage execution artifacts at run time.
_EXPECTED_STAGE_RESULT_SIGNAL_SCHEMA_VERSION = "1"

# Name of the Check Run carrying the merge-gate verdict on the PR head commit.
# A branch ruleset requires this check (docs/CONFIGURATION.md, "Merge gate").
GOVERNANCE_CHECK_RUN_NAME = "stagr/governance"

# Width of the indentation block for run: | script content in the generated
# YAML (offset from the left edge of the file).
_YAML_SCRIPT_LINE_INDENT = "          "


def generate_governance_workflow_yaml(
    publisher_app_id: str,
    publisher_private_key_secret: str,
    result_specs: tuple[StageResultSpec, ...],
    render_context: RenderContext,
) -> str:
    """Return the complete governance workflow YAML string.

    Renders the publisher_app_id as a literal constant so the generated
    workflow can verify publisher identity at run time without any
    additional configuration.  The private_key_secret is referenced by
    name only (never inlined).

    Args:
        publisher_app_id: Numeric Stagr GitHub App ID rendered as a literal
            for both token acquisition and publisher-identity verification.
        publisher_private_key_secret: Name of the repository secret that
            holds the App RSA private key.
        result_specs: StageResultSpec for every stage produced in Phase 1.
        render_context: RenderContext carrying MergePolicy (used to derive
            which stages are blocking).
    """
    private_key_expr = "${{ secrets." + publisher_private_key_secret + " }}"
    app_token_output_expr = "${{ steps.app-token.outputs.token }}"
    pr_head_sha_expr = (
        "${{ github.event.pull_request.head.sha"
        " || github.event.check_suite.head_sha }}"
    )
    pr_number_expr = (
        "${{ github.event.pull_request.number"
        " || github.event.check_suite.pull_requests[0].number }}"
    )
    repo_expr = "${{ github.repository }}"

    evaluation_script = _build_evaluation_script(result_specs, render_context)
    indented_script = _indent_script_for_yaml(evaluation_script)

    return (
        'name: "Stagr governance"\n'
        "\n"
        "on:\n"
        "  pull_request_target:\n"
        "    types: [opened, synchronize, reopened, ready_for_review, labeled, unlabeled]\n"
        "  check_suite:\n"
        "    types: [completed]\n"
        "\n"
        "permissions:\n"
        "  pull-requests: read\n"
        "  checks: read\n"
        "\n"
        "concurrency:\n"
        f'  group: "stagr-governance-{pr_number_expr}"\n'
        "  cancel-in-progress: false\n"
        "\n"
        "jobs:\n"
        "  publish-merge-verdict:\n"
        "    # A check suite on a commit that heads no pull request has no verdict to publish.\n"
        "    if: github.event_name == 'pull_request_target'"
        " || github.event.check_suite.pull_requests[0].number\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: Acquire Stagr App installation token\n"
        "        id: app-token\n"
        f"        uses: {APP_TOKEN_ACTION_REF}\n"
        "        with:\n"
        f'          app-id: "{publisher_app_id}"\n'
        f'          private-key: "{private_key_expr}"\n'
        "\n"
        "      - name: Evaluate stage result signals\n"
        "        id: evaluate\n"
        "        # The verdict, pass or block, is published by the next step.\n"
        "        continue-on-error: true\n"
        "        env:\n"
        f'          GH_TOKEN: "{app_token_output_expr}"\n'
        f'          STAGR_APP_ID: "{publisher_app_id}"\n'
        f'          PR_HEAD_SHA: "{pr_head_sha_expr}"\n'
        f'          REPO: "{repo_expr}"\n'
        "        run: |\n"
        f"{indented_script}"
        "\n"
        "      - name: Publish the merge-gate verdict on the pull request head\n"
        "        env:\n"
        f'          GH_TOKEN: "{app_token_output_expr}"\n'
        f'          STAGR_APP_ID: "{publisher_app_id}"\n'
        f'          PR_HEAD_SHA: "{pr_head_sha_expr}"\n'
        f'          REPO: "{repo_expr}"\n'
        '          EVALUATION_OUTCOME: "${{ steps.evaluate.outcome }}"\n'
        '          RUN_URL: "${{ github.server_url }}/${{ github.repository }}'
        '/actions/runs/${{ github.run_id }}"\n'
        "        run: |\n"
        f"{_indent_script_for_yaml(_build_verdict_publishing_script())}"
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _indent_script_for_yaml(script: str) -> str:
    """Prefix each line of script with the YAML run-block indent.

    Empty lines remain empty (no trailing spaces) to keep YAML valid.
    The result ends with a single newline.
    """
    lines: list[str] = []
    for raw_line in script.splitlines():
        if raw_line.strip():
            lines.append(_YAML_SCRIPT_LINE_INDENT + raw_line)
        else:
            lines.append("")
    return "\n".join(lines) + "\n"


def _build_verdict_publishing_script() -> str:
    """Return shell code that publishes the verdict as the governance Check Run.

    Fail closed: any evaluation outcome other than success publishes a failure.
    The Stagr App's existing Check Run for the head is updated in place, and
    left alone when its conclusion already matches, so a publication that
    completes the App's check suite again ends in a run that publishes nothing.
    """
    return (
        "set -euo pipefail\n"
        'if [[ "${EVALUATION_OUTCOME}" == "success" ]]; then\n'
        "  verdict_conclusion=success\n"
        '  verdict_title="Merge is eligible"\n'
        "else\n"
        "  verdict_conclusion=failure\n"
        '  verdict_title="Merge is blocked"\n'
        "fi\n"
        'verdict_summary="Stagr governance evaluated the blocking stages on ${PR_HEAD_SHA}.'
        ' Details: ${RUN_URL}"\n'
        "existing_check_run=\"$(gh api \\\n"
        f'  "repos/${{REPO}}/commits/${{PR_HEAD_SHA}}/check-runs?check_name={GOVERNANCE_CHECK_RUN_NAME}'
        '&filter=latest&per_page=10" \\\n'
        "  --jq \"[.check_runs[] | select(.app.id | tostring == \\\"${STAGR_APP_ID}\\\")][0] // empty\")\"\n"
        'if [[ -z "${existing_check_run}" ]]; then\n'
        "  gh api --method POST \"repos/${REPO}/check-runs\" \\\n"
        f'    -f "name={GOVERNANCE_CHECK_RUN_NAME}" -f "head_sha=${{PR_HEAD_SHA}}" -f status=completed \\\n'
        '    -f "conclusion=${verdict_conclusion}" -f "output[title]=${verdict_title}" \\\n'
        '    -f "output[summary]=${verdict_summary}" >/dev/null\n'
        "elif [[ \"$(echo \"${existing_check_run}\" | jq -r '.conclusion')\" != \"${verdict_conclusion}\" ]]; then\n"
        "  existing_check_run_id=\"$(echo \"${existing_check_run}\" | jq -r '.id')\"\n"
        "  gh api --method PATCH \"repos/${REPO}/check-runs/${existing_check_run_id}\" \\\n"
        '    -f status=completed -f "conclusion=${verdict_conclusion}" -f "output[title]=${verdict_title}" \\\n'
        '    -f "output[summary]=${verdict_summary}" >/dev/null\n'
        "else\n"
        f'  echo "{GOVERNANCE_CHECK_RUN_NAME} already reads ${{verdict_conclusion}} on ${{PR_HEAD_SHA}}; nothing to publish."\n'
        "  exit 0\n"
        "fi\n"
        f'echo "Published {GOVERNANCE_CHECK_RUN_NAME}: ${{verdict_title}} (${{PR_HEAD_SHA}})."\n'
    )


def _build_route_reading_block() -> str:
    """Return shell code that reads the RouteClassification Check Run for the current PR.

    The block queries the stagr/route-classification Check Run, validates that
    exactly one exists for the current head SHA, and sets current_route to
    either "FAST" or "NORMAL". Any deviation fails closed with an explicit error.
    """
    return (
        "# Read route classification to restrict stage evaluation to applicable stages.\n"
        "route_check_runs_json=\"$(gh api \\\n"
        "  \\\"repos/${REPO}/commits/${PR_HEAD_SHA}/check-runs?check_name=stagr/route-classification&filter=all&per_page=2\\\" \\\n"
        "  --jq '.check_runs' 2>&1)\" || {\n"
        "  echo \"::error::Failed to query RouteClassification Check Run for SHA '${PR_HEAD_SHA}'.\"\n"
        "  exit 1\n"
        "}\n"
        "route_run_count=\"$(echo \"${route_check_runs_json}\" | jq 'length')\"\n"
        "if [[ \"${route_run_count}\" -eq 0 ]]; then\n"
        "  echo \"::error::No RouteClassification Check Run found for SHA '${PR_HEAD_SHA}'.\"\n"
        "  echo \"::error::Merge blocked until the routing workflow completes.\"\n"
        "  exit 1\n"
        "fi\n"
        "if [[ \"${route_run_count}\" -gt 1 ]]; then\n"
        "  echo \"::error::Duplicate RouteClassification Check Runs found for SHA '${PR_HEAD_SHA}'.\"\n"
        "  exit 1\n"
        "fi\n"
        "route_app_id=\"$(echo \"${route_check_runs_json}\" | jq -r '.[0].app.id | tostring')\"\n"
        "if [[ \"${route_app_id}\" != \"${STAGR_APP_ID}\" ]]; then\n"
        "  echo \"::error::RouteClassification Check Run publisher mismatch for SHA '${PR_HEAD_SHA}':\"\\\n"
        "    \" expected Stagr App ID '${STAGR_APP_ID}', found '${route_app_id}'.\"\\\n"
        "    \" Rejecting classification — Check Run not published by the trusted Stagr App.\"\n"
        "  exit 1\n"
        "fi\n"
        "route_title=\"$(echo \"${route_check_runs_json}\" | jq -r '.[0].output.title // \"\"')\"\n"
        "current_route=\"${route_title#RouteClassification=}\"\n"
        "if [[ \"${current_route}\" != \"FAST\" && \"${current_route}\" != \"NORMAL\" ]]; then\n"
        "  echo \"::error::Unrecognised route '${current_route}' for SHA '${PR_HEAD_SHA}'.\"\n"
        "  exit 1\n"
        "fi\n"
        "\n"
    )


def _build_evaluation_script(
    result_specs: tuple[StageResultSpec, ...],
    render_context: RenderContext,
) -> str:
    """Return the complete shell script body for the evaluation step."""
    stage_call_lines = _build_stage_evaluation_call_lines(result_specs, render_context)

    route_reading_block = ""
    if render_context.routing_policy.fast_path is not None:
        route_reading_block = _build_route_reading_block()

    return (
        _EVALUATE_SIGNAL_FUNCTION_BODY
        + "\n"
        + route_reading_block
        + "overall_pass=true\n"
        + stage_call_lines
        + "\n"
        + 'if [[ "${overall_pass}" == "false" ]]; then\n'
        + "  echo \"::error::One or more blocking stage signals did not pass."
        + " Merge is not eligible.\"\n"
        + "  exit 1\n"
        + "fi\n"
        + 'echo "All blocking stage signals evaluated. Merge is eligible."\n'
    )


def _build_stage_evaluation_call_lines(
    result_specs: tuple[StageResultSpec, ...],
    render_context: RenderContext,
) -> str:
    """Return shell lines that call evaluate_stage_signal for each stage spec.

    Blocking stages set overall_pass=false on failure; non-blocking stages are
    observed for informational purposes only and never affect merge eligibility.
    When routing_policy.fast_path is set, each stage call is wrapped in a route
    conditional so only route-applicable stages are evaluated.
    """
    blocking_stage_ids = set(render_context.merge_policy.blocking_stage_ids)
    fast_path = render_context.routing_policy.fast_path

    fast_stage_ids: set[str] = set()
    normal_stage_ids: set[str] = set()
    if fast_path is not None:
        fast_stage_ids = set(fast_path.stages.fast)
        normal_stage_ids = set(fast_path.stages.normal)

    lines: list[str] = []
    for spec in result_specs:
        is_blocking = spec.stage_id in blocking_stage_ids
        gate_argument = "blocking" if is_blocking else "non_blocking"
        fail_suffix = " || overall_pass=false" if is_blocking else " || true"
        eval_call = (
            f'evaluate_stage_signal'
            f' "{spec.stage_id}"'
            f' "{spec.signal_selector}"'
            f' "{gate_argument}"'
            f'{fail_suffix}'
        )

        if fast_path is None:
            lines.append(eval_call + "\n")
        else:
            in_fast = spec.stage_id in fast_stage_ids
            in_normal = spec.stage_id in normal_stage_ids
            if in_fast and in_normal:
                lines.append(eval_call + "\n")
            elif in_fast:
                lines.append('if [[ "${current_route}" == "FAST" ]]; then\n')
                lines.append(f'  {eval_call}\n')
                lines.append('fi\n')
            elif in_normal:
                lines.append('if [[ "${current_route}" == "NORMAL" ]]; then\n')
                lines.append(f'  {eval_call}\n')
                lines.append('fi\n')
            else:
                pass  # stage not applicable to either route; skip
    return "".join(lines)


# ---------------------------------------------------------------------------
# Embedded shell function definition
# ---------------------------------------------------------------------------

# The evaluate_stage_signal function is a fixed template. It uses shell
# variables REPO, PR_HEAD_SHA, and STAGR_APP_ID (all passed via the env:
# block of the generated step) plus three positional arguments:
#   $1 = stage_id   (for error messages)
#   $2 = signal_selector (Check Run name)
#   $3 = blocking_gate  ("blocking" | "non_blocking")
#
# The expected schemaVersion is hardcoded as "1" (matches
# _EXPECTED_STAGE_RESULT_SIGNAL_SCHEMA_VERSION).

_EVALUATE_SIGNAL_FUNCTION_BODY = """\
set -euo pipefail

evaluate_stage_signal() {
  local stage_id="$1"
  local signal_selector="$2"
  local blocking_gate="$3"

  # Locate the single Check Run for this stage on the current head SHA.
  local check_runs_json
  check_runs_json="$(gh api \\
    "repos/${REPO}/commits/${PR_HEAD_SHA}/check-runs?check_name=${signal_selector}&filter=all&per_page=10" \\
    --jq '[.check_runs[]]' 2>&1)" || {
    echo "::error::Failed to query Check Runs for stage '${stage_id}'" \\
      "on SHA '${PR_HEAD_SHA}': ${check_runs_json}"
    return 1
  }

  local check_run_count
  check_run_count="$(echo "${check_runs_json}" | jq 'length')"

  # Fail closed: no Check Run found.
  if [[ "${check_run_count}" -eq 0 ]]; then
    echo "::error::No Check Run found for stage '${stage_id}'" \\
      "(selector: '${signal_selector}') on SHA '${PR_HEAD_SHA}'." \\
      "Merge blocked until the stage has run."
    return 1
  fi

  # Fail closed: duplicate Check Runs — never silently resolve ambiguity.
  if [[ "${check_run_count}" -gt 1 ]]; then
    echo "::error::Duplicate Check Runs found for stage '${stage_id}'" \\
      "on SHA '${PR_HEAD_SHA}': ${check_run_count} found, expected exactly 1." \\
      "Merge blocked — duplicates must be resolved explicitly."
    return 1
  fi

  local check_run
  check_run="$(echo "${check_runs_json}" | jq '.[0]')"

  # Publisher identity verification: only trust Check Runs from the Stagr App.
  local publisher_app_id
  publisher_app_id="$(echo "${check_run}" | jq -r '.app.id | tostring')"
  if [[ "${publisher_app_id}" != "${STAGR_APP_ID}" ]]; then
    echo "::error::Publisher identity mismatch for stage '${stage_id}':" \\
      "expected Stagr App ID '${STAGR_APP_ID}', found '${publisher_app_id}'." \\
      "Rejecting signal — Check Run not published by the trusted Stagr App."
    return 1
  fi

  # Signal deserialization: read from output.summary JSON, not native fields.
  local summary_json
  summary_json="$(echo "${check_run}" | jq -r '.output.summary // ""')"
  if [[ -z "${summary_json}" ]]; then
    echo "::error::Check Run for stage '${stage_id}' has no output.summary payload." \\
      "Cannot deserialize StageResultSignal."
    return 1
  fi

  # Validate schemaVersion before deserializing any other fields.
  local schema_version
  schema_version="$(echo "${summary_json}" | jq -r '.schemaVersion // ""' 2>/dev/null || echo "")"
  if [[ "${schema_version}" != "1" ]]; then
    echo "::error::Unsupported schemaVersion '${schema_version}' for stage '${stage_id}'." \\
      "Expected '1'. Cannot safely deserialize StageResultSignal."
    return 1
  fi

  # Deserialize StageResultSignal fields from the validated payload.
  local signal_head_sha signal_state signal_conclusion
  signal_head_sha="$(echo "${summary_json}" | jq -r '.headSha // ""')"
  signal_state="$(echo "${summary_json}" | jq -r '.state // ""')"
  signal_conclusion="$(echo "${summary_json}" | jq -r '.conclusion // ""')"

  # Head SHA binding: ignore stale signals bound to a prior commit.
  if [[ "${signal_head_sha}" != "${PR_HEAD_SHA}" ]]; then
    echo "::warning::Stale StageResultSignal for stage '${stage_id}':" \\
      "signal bound to '${signal_head_sha}', current head is '${PR_HEAD_SHA}'." \\
      "Ignoring signal for prior commit."
    return 1
  fi

  # State check: stage must be COMPLETED before the conclusion is meaningful.
  if [[ "${signal_state}" != "completed" ]]; then
    echo "::warning::Stage '${stage_id}' has not completed (state: '${signal_state}')." \\
      "Waiting for stage completion before evaluating conclusion."
    return 1
  fi

  # Conclusion evaluation — PASS is the only merge-eligible outcome.
  if [[ "${signal_conclusion}" == "pass" ]]; then
    echo "Stage '${stage_id}': PASS (eligible for merge)"
    return 0
  fi

  # Non-blocking stages are informational; their conclusions do not block merge.
  if [[ "${blocking_gate}" != "blocking" ]]; then
    echo "Stage '${stage_id}': conclusion '${signal_conclusion}' (non-blocking — informational only)"
    return 0
  fi

  # Blocking stage: BLOCKED and FAILED have distinct, actionable messages.
  if [[ "${signal_conclusion}" == "blocked" ]]; then
    echo "::error::Stage '${stage_id}' is BLOCKED:" \\
      "findings must be resolved before merge is allowed."
    return 1
  elif [[ "${signal_conclusion}" == "failed" ]]; then
    echo "::error::Stage '${stage_id}' FAILED:" \\
      "stage did not complete due to an infrastructure failure." \\
      "Retry the stage workflow to unblock."
    return 1
  else
    echo "::error::Stage '${stage_id}' has unrecognised conclusion '${signal_conclusion}'." \\
      "Merge blocked."
    return 1
  fi
}
"""
