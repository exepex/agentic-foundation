"""GitHubPlatformRenderer: Phase 1, Phase 2a, and Phase 2b artifact generator for GitHub Actions.

The renderer only builds text: every method returns a ``RenderedArtifact`` (repository-relative
path plus content) and nothing is written to disk here.

Phase 1 (render_stage): translates a (ExecutionPlan, NormalizedStage, RenderContext)
triple into a GitHub Actions workflow artifact (.github/workflows/stage-<id>.yml) and
returns it with a StageResultSpec that describes the Check Run this stage will emit at
run time.

Phase 2a (render_routing): builds the routing artifact
(.github/workflows/routing.yml) that classifies each PR head commit as FAST or NORMAL
and publishes a ``RouteClassification`` Check Run authenticated by the Stagr GitHub App.

Phase 2b (render_governance): builds the merge-gate workflow artifact
.github/workflows/governance.yml.  The workflow reads StageResultSignal values from
Check Runs published by stage execution artifacts, verifies publisher identity against
the Stagr App ID (rendered as a literal constant), and blocks merge when any blocking
stage has a BLOCKED or FAILED conclusion.  See _governance.py for the complete
governance logic specification.

Security invariant (stage workflows): stages with required_secrets (privileged stages)
MUST use ``pull_request_target`` — never ``pull_request``. The ``pull_request`` event
does not expose repository secrets, so any stage that needs them would fail silently.
More critically, ``pull_request_target`` runs with the base-branch workflow definition,
which is crucial for trusted execution. This renderer enforces the invariant at render
time so a misconfiguration is caught before deployment.

Token isolation (stage workflows): the Stagr GitHub App installation token (acquired
in step 1 and used in step 4 for Check Run creation) is NEVER passed to the backend
invocation step (step 3). The backend step receives only the secrets declared in
ExecutionPlan.required_secrets (resolved alias → env_name pairs). Mixing the App token
with backend invocation calls would grant the backend write access to platform
primitives (Check Runs) it must not control.

Stage workflow structure (see stage_workflow.py):
  execute job    1. App token acquisition   — always emitted
                 2. Eligibility check       — trust, fork policy, current head, route, dependencies
                                              (spec: #207); sets the output ``proceed`` that gates
                                              step 3. Runs on the declared triggers and, for a stage
                                              with dependencies, on upstream check_run/check_suite
                                              wake-ups.
                 3. Idempotency guard and   — PR_COMMENT backends (spec: #205): one step checks the
                    backend invocation        completion guard and the in-flight lease, then posts
                                              the comment; holds only the TRUSTED_COMMENTER_TOKEN
                                              secret. Other invocation kinds are rejected (V-S08
                                              rejects them before a config is rendered).
                 4. Result signaling        — Check Run carrying the StageResultSignal (spec: #206);
                                              the only step that creates the Check Run
  reconcile job  issue_comment wakeup; updates the Check Run in place (spec: #206)
  sweep job      scheduled backstop over open pull requests (spec: #206); updates only

Trigger mapping (design-doc 08):
  StageTrigger.PR_OPENED    → pull_request_target: [opened, reopened, ready_for_review]
  StageTrigger.PR_UPDATED   → pull_request_target: [synchronize]
  StageTrigger.MANUAL       → workflow_dispatch
  StageTrigger.ISSUE_LABELED → issues: [labeled]
"""
from __future__ import annotations

from stagr.core.enums import InvocationKind, StageResultSignalKind
from stagr.core.models import (
    ExecutionPlan,
    NormalizedStage,
    RenderContext,
    RenderedArtifact,
    StageRender,
    StageResultProvenance,
    StageResultSpec,
)
from stagr.platforms.github._governance import generate_governance_workflow_yaml
from stagr.platforms.github.routing_workflow import (
    generate_routing_workflow_yaml,
    ROUTING_WORKFLOW_FILENAME,
)
from stagr.platforms.github.stage_signal_config import (
    build_stage_check_run_name,
    build_stage_signal_config,
)
from stagr.platforms.github.stage_workflow import build_on_section, build_stage_workflow_yaml
from stagr.platforms.github.thread_resolution_workflow import (
    THREAD_RESOLUTION_WORKFLOW_FILENAME,
    generate_thread_resolution_workflow_yaml,
)


WORKFLOW_DIRECTORY = ".github/workflows"
GOVERNANCE_WORKFLOW_FILENAME = "governance.yml"
# First line of every workflow this renderer generates. `stagr apply` treats a file in
# WORKFLOW_DIRECTORY as its own only when it starts with this line, and deletes such a file once
# the config no longer produces it; a workflow without this line is never touched.
GENERATED_FILE_HEADER = (
    "# Generated by stagr from .agentic/config.yml. Do not edit: change the config and run `stagr apply`."
)


def build_generated_artifact(path: str, workflow_yaml: str) -> RenderedArtifact:
    """Return the artifact for ``path`` with the ownership header as its first line."""
    return RenderedArtifact(path=path, content=f"{GENERATED_FILE_HEADER}\n{workflow_yaml}")


class GitHubPlatformRenderer:
    """PlatformRenderer that generates GitHub Actions workflow YAML artifacts.

    ``SUPPORTED_INVOCATION_KINDS`` declares which ``InvocationKind`` values
    this renderer can translate into GitHub Actions workflow steps.  The static
    validator (V-S08) reads this to ensure no stage backend requires a kind the
    platform cannot handle.

    ``ARTIFACT_DIRECTORY`` and ``GENERATED_FILE_HEADER`` tell ``stagr apply`` where generated files
    live and how to recognise them, so it can remove the ones the config no longer produces.

    Phase 1 (render_stage): returns the stage execution workflow artifact
    ``.github/workflows/stage-<id>.yml`` together with its StageResultSpec.

    Phase 2a (render_routing): returns the routing workflow artifact
    ``.github/workflows/routing.yml`` that classifies PR head commits and publishes
    an authenticated ``RouteClassification`` Check Run.

    Phase 2b (render_governance): returns the merge-gate workflow artifact
    ``.github/workflows/governance.yml``.

    Phase 2c (render_thread_resolution): returns the workflow artifact
    ``.github/workflows/resolve-outdated-threads.yml`` that resolves outdated finding threads.

    No method writes to the file system.
    """

    # Only the invocation kinds the stage workflow really performs are declared. A backend whose
    # plan uses another kind (CI_COMPONENT) is rejected by V-S08
    # instead of being rendered as a workflow that would report PASS without doing the work.
    SUPPORTED_INVOCATION_KINDS: frozenset[InvocationKind] = frozenset({
        InvocationKind.PR_COMMENT,
    })
    ARTIFACT_DIRECTORY = WORKFLOW_DIRECTORY
    GENERATED_FILE_HEADER = GENERATED_FILE_HEADER
    # `stagr apply --force` also claims a stale file without the header when its name is one this
    # renderer produces AND its first line is a Stagr workflow name, which is how files written by
    # an older Stagr start. A team's own `stage-deploy.yml` fails the second test and is kept.
    GENERATED_FILE_NAME_PATTERNS: tuple[str, ...] = (
        "stage-*.yml",
        ROUTING_WORKFLOW_FILENAME,
        GOVERNANCE_WORKFLOW_FILENAME,
        THREAD_RESOLUTION_WORKFLOW_FILENAME,
    )
    GENERATED_WORKFLOW_NAME_PREFIX = 'name: "Stagr '


    def __init__(
        self,
        publisher_app_id: str,
        publisher_private_key_secret: str,
    ) -> None:
        """Initialise the renderer.

        Args:
            publisher_app_id: Numeric GitHub App ID for the Stagr publisher App,
                              rendered as a literal into the token-acquisition step.
            publisher_private_key_secret: Name of the repository secret that holds
                              the App's RSA private key (e.g. ``STAGR_APP_PRIVATE_KEY``).
                              Rendered as ``${{ secrets.<name> }}`` in the workflow.
        """
        self._publisher_app_id = publisher_app_id
        self._publisher_private_key_secret = publisher_private_key_secret

    # ------------------------------------------------------------------
    # PlatformRenderer Protocol — Phase 1
    # ------------------------------------------------------------------

    def render_stage(
        self,
        plan: ExecutionPlan,
        stage: NormalizedStage,
        render_context: RenderContext,
    ) -> StageRender:
        """Generate the stage execution workflow artifact and its StageResultSpec.

        The artifact path is ``.github/workflows/stage-<stage.id>.yml``.

        Raises ValueError if the stage is privileged (non-empty
        ``plan.required_secrets``) but any of its triggers cannot be satisfied by
        ``pull_request_target`` — a security invariant violation.
        """
        is_privileged = bool(plan.required_secrets)
        check_run_name = build_stage_check_run_name(stage.id)
        signal_config = build_stage_signal_config(
            plan, stage, render_context, self._publisher_app_id, check_run_name
        )
        on_section_yaml = build_on_section(stage.triggers, signal_config.has_dependencies)
        self._assert_privileged_stage_on_section_is_safe(stage, on_section_yaml, is_privileged)

        workflow_yaml = build_stage_workflow_yaml(
            plan,
            stage,
            signal_config,
            on_section_yaml,
            self._publisher_app_id,
            self._publisher_private_key_secret,
        )

        return StageRender(
            result_spec=StageResultSpec(
                stage_id=stage.id,
                signal_kind=StageResultSignalKind.CHECK_RUN,
                signal_selector=check_run_name,
                provenance=StageResultProvenance(
                    publisher_identity=self._publisher_app_id,
                ),
                finding_author=plan.gate_disposition.scope.created_by if plan.gate_disposition.scope else None,
            ),
            artifact=build_generated_artifact(f"{WORKFLOW_DIRECTORY}/stage-{stage.id}.yml", workflow_yaml),
        )

    # ------------------------------------------------------------------
    # PlatformRenderer Protocol — Phase 2
    # ------------------------------------------------------------------

    def render_routing(self, render_context: RenderContext) -> RenderedArtifact:
        """Phase 2a: return the routing artifact ``.github/workflows/routing.yml``.

        When ``render_context.routing_policy.fast_path`` is ``None``, the workflow
        immediately emits ``RouteClassification=NORMAL`` with no path analysis.
        When a ``FastPathPolicy`` is present, the workflow fetches changed file
        paths, tests them against the configured glob patterns, and emits FAST or
        NORMAL accordingly.

        In both cases the ``RouteClassification`` result is published as an
        authenticated Check Run using the Stagr GitHub App installation token.
        """
        workflow_yaml = generate_routing_workflow_yaml(
            fast_path_policy=render_context.routing_policy.fast_path,
            publisher_app_id=self._publisher_app_id,
            publisher_private_key_secret=self._publisher_private_key_secret,
        )
        return build_generated_artifact(f"{WORKFLOW_DIRECTORY}/{ROUTING_WORKFLOW_FILENAME}", workflow_yaml)

    def render_governance(
        self,
        result_specs: tuple[StageResultSpec, ...],
        render_context: RenderContext,
    ) -> RenderedArtifact:
        """Phase 2b: return the governance / merge-gate artifact ``.github/workflows/governance.yml``.

        The workflow reads StageResultSignal values from Check Runs published
        by stage execution artifacts, verifies that each Check Run was
        published by the Stagr GitHub App (using the publisher_app_id rendered
        as a literal constant), and blocks merge when any blocking stage
        reports a BLOCKED or FAILED conclusion.

        Args:
            result_specs: StageResultSpec for every stage produced in Phase 1.
            render_context: RenderContext carrying MergePolicy, TrustPolicy,
                and RoutingPolicy used to determine blocking stages.
        """
        governance_yaml = generate_governance_workflow_yaml(
            publisher_app_id=self._publisher_app_id,
            publisher_private_key_secret=self._publisher_private_key_secret,
            result_specs=result_specs,
            render_context=render_context,
        )

        return build_generated_artifact(
            f"{WORKFLOW_DIRECTORY}/{GOVERNANCE_WORKFLOW_FILENAME}", governance_yaml
        )

    def render_thread_resolution(
        self,
        result_specs: tuple[StageResultSpec, ...],
        render_context: RenderContext,
        token_secret: str,
    ) -> RenderedArtifact | None:
        """Phase 2c: return ``.github/workflows/resolve-outdated-threads.yml``, or None.

        The workflow resolves outdated review threads written only by the stages' review
        backends. None when no stage reads review threads, so there is nothing to resolve.
        """
        finding_authors = tuple(
            result_spec.finding_author for result_spec in result_specs if result_spec.finding_author
        )
        if not finding_authors:
            return None
        workflow_yaml = generate_thread_resolution_workflow_yaml(finding_authors, token_secret, render_context)
        return build_generated_artifact(
            f"{WORKFLOW_DIRECTORY}/{THREAD_RESOLUTION_WORKFLOW_FILENAME}", workflow_yaml
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _assert_privileged_stage_on_section_is_safe(
        self,
        stage: NormalizedStage,
        on_section_yaml: str,
        is_privileged: bool,
    ) -> None:
        """Raise ValueError if a privileged stage's on-section would use pull_request.

        A privileged stage (non-empty required_secrets) MUST use pull_request_target
        — never the bare pull_request event — because only pull_request_target runs
        with base-branch secrets; pull_request runs in the fork context where secrets
        are unavailable. This renderer's trigger mapping always emits
        pull_request_target for PR triggers; this check is a defence-in-depth guard
        that catches any future change in the mapping that would introduce
        pull_request for a privileged stage.

        Raises ValueError when a bare ``pull_request:`` line appears in
        ``on_section_yaml`` for a privileged stage.
        """
        if not is_privileged:
            return
        for line in on_section_yaml.splitlines():
            stripped = line.strip()
            if stripped == "pull_request:" or stripped.startswith("pull_request: "):
                raise ValueError(
                    f"Security invariant violated: privileged stage '{stage.id}' "
                    f"(non-empty required_secrets) must use pull_request_target, "
                    f"not pull_request. A pull_request trigger does not expose "
                    f"base-branch secrets, so secret-dependent steps would silently "
                    f"fail. Fix the trigger mapping for StageTrigger values on this "
                    f"stage, or remove the required_secrets from the ExecutionPlan."
                )
