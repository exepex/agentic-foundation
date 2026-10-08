"""BackendRenderer for the OpenAI/Codex review and security-review backends.

Produces the ExecutionPlan for stages that use Codex (OpenAI's AI code-review
tool) as a PR-comment-driven review backend. Both review (code review) and
security stages share this renderer; the stage kind determines the comment body
posted as the invocation param.

Spike A finding (invocation independence): ``@codex review`` and
``@codex security review`` are independently triggerable PR comments. The
sequential scheduling enforced by the current hand-written workflows is an
operational guard against a Codex backend concurrency limitation (two reviews
running at the same time on one PR), not a protocol requirement of the backend
itself. Each invocation kind is independently valid.

Spike B finding (finding correlation): The GitHub API does not expose a
reliably observable field that unambiguously associates individual review
threads with their originating stage invocation when code review and security
review are performed by the same Codex bot identity on the same head commit.
``pull_request_review_id`` would require a runtime lookup of the formal review
object per thread, and the existing workflows do not record or expose the
per-review discriminator needed to scope thread counts to one stage.
Therefore ``invocation_correlation`` is ``None`` in the ``FindingScopeSpec``;
both stages share the conservative requirement: zero unresolved Codex-bot
threads on the current head commit. Cross-stage blocking (a REVIEW finding
keeping SECURITY BLOCKED) is acceptable for V1 — it fails closed rather than
permitting an unsafe merge.

Evidence kinds by stage kind:
- REVIEW stages use ``EvidenceKind.REVIEW_RESULT`` with ``COMPLETED``.
  Per design-doc 06, ``COMPLETED`` is the correct ``EvidenceSuccessCondition``
  for review stages — it means "the reviewer finished processing," regardless
  of findings. Findings are handled separately by ``GateDispositionSpec``.
- SECURITY stages use ``EvidenceKind.COMMENT_MATCH`` with ``MATCH_FOUND``:
  Codex embeds a machine-readable marker inside the existing review-summary
  comment (``codex-pull-request-review-summary``):
  ``<!-- codex-security-review:v1 {"blockingSeverityThreshold":"P0",
  "headSha":"<full40>","status":"completed"} -->``
  The marker format is as observed on Codex review-summary comments; a marker with
  ``status="running"`` must never count as completion.

  Compound selector convention: the ``selector`` field for COMMENT_MATCH
  evidence on the Codex backend is a space-separated compound expression.
  The first token is the marker-type prefix (literal string match in the
  comment body). Subsequent ``key=value`` tokens specify JSON field value
  requirements evaluated by the PlatformRenderer against the parsed marker
  JSON blob. All predicates must be satisfied for MATCH_FOUND to succeed.
  The selector ``codex-security-review:v1 status=completed`` therefore
  requires both the marker prefix and ``"status":"completed"`` in the parsed
  JSON — it does NOT match a marker with ``"status":"running"``. The
  ``CorrelationSpec.sha_field`` handles the headSha check separately.

Gate disposition: ``NO_OPEN_THREADS`` with ``FindingScopeSpec(created_by=
"chatgpt-codex-connector[bot]", head_sha=True, invocation_correlation=None)``
for both stage kinds. The security marker format (as observed on Codex review-summary
comments) is ``{"blockingSeverityThreshold":"P0","headSha":"...","status":"..."}``
— there is no findings or verdict field; ``status=completed`` proves the review
finished, not that it passed cleanly. ``NO_OPEN_THREADS`` is therefore the
correct conservative disposition for SECURITY as well as REVIEW.
``FindingScopeSpec.head_sha=True`` is a declaration to the PlatformRenderer
that thread filtering must be head-bound; the concrete mechanism (e.g., the
``commit_id`` field on GitHub review objects) is PlatformRenderer scope.

Renderer raises ``ValueError`` for any stage kind other than REVIEW or
SECURITY; both review kinds are the only supported backends for this renderer.

Secret alias contract: only ``SecretRef.alias`` is set here; ``env_name`` is
resolved by the Phase 1 alias-resolution step (see issue #193) before the
PlatformRenderer is invoked.
"""
from __future__ import annotations

from stagr.core.backend_names import BACKEND_CODEX, PROVIDER_OPENAI
from stagr.core.enums import (
    EvidenceKind,
    EvidenceSuccessCondition,
    GateDispositionKind,
    InvocationKind,
    StageGate,
    StageKind,
)
from stagr.core.models import (
    CorrelationSpec,
    EvidenceSpec,
    ExecutionPlan,
    FindingScopeSpec,
    GateDispositionSpec,
    Invocation,
    NormalizedStage,
    SecretRef,
)

# Commands that trigger each Codex review kind.
_CODEX_CODE_REVIEW_COMMAND = "@codex review"
_CODEX_SECURITY_REVIEW_COMMAND = "@codex security review"

# Instructions posted with every review command. Codex reads text after the command as guidance
# for that review. Findings drive automated fixes, so a speculative finding becomes a needless
# patch: the reviewer must report only what a realistic input or caller actually breaks.
CODEX_REVIEW_GUIDELINES = (
    "Review guidelines for this request:\n"
    "- Report a finding only when it describes a real problem in this change: a concrete input,"
    " caller or sequence of events that realistically reaches it, and the wrong result it causes.\n"
    "- Do not report hypothetical or highly unlikely cases, misuse the code's contract or existing"
    " validation already rules out, style preferences, or extra hardening whose cost exceeds the"
    " risk.\n"
    "- For each finding, state the triggering input or path and the impact in one or two sentences.\n"
    "- When unsure whether a finding is real, leave it out."
)

# Alias for the credential that allows posting PR comments as a trusted user
# (Codex honours @codex commands only from trusted authors, not from the
# github-actions bot). env_name is intentionally absent — alias-only per contract.
_TRUSTED_COMMENTER_TOKEN_ALIAS = "TRUSTED_COMMENTER_TOKEN"

# Backend-defined selector that identifies the Codex review-summary comment
# block; consumed by the PlatformRenderer when constructing the governance
# artifact's evidence-detection logic.
_CODEX_REVIEW_SUMMARY_SELECTOR = "codex-pull-request-review-summary"

# The field within the Codex review-summary evidence item that carries the head
# SHA (the backtick-formatted SHA in the Code Review row).
_REVIEW_SUMMARY_SHA_FIELD = "review_summary_sha"

# Machine-readable marker embedded by Codex inside the review-summary comment
# when the security review completes. Format (as observed on Codex review-summary comments):
#   <!-- codex-security-review:v1 {"blockingSeverityThreshold":"P0",
#        "headSha":"<full40>","status":"completed"} -->
#
# The selector is a compound expression (see module docstring). The first token
# is the marker prefix; subsequent key=value tokens are JSON field requirements
# evaluated by the PlatformRenderer against the parsed marker JSON. Both the
# marker prefix and status=completed must be present for MATCH_FOUND to succeed.
# A marker with status="running" must not satisfy MATCH_FOUND.
_CODEX_SECURITY_REVIEW_MARKER_SELECTOR = "codex-security-review:v1 status=completed"

# The JSON field within the codex-security-review:v1 blob that carries the
# full 40-character head SHA of the reviewed commit.
_SECURITY_REVIEW_MARKER_SHA_FIELD = "headSha"

# Authoritative Codex bot GitHub login (empirically grounded in the deployed gate's
# CODEX_BOT_LOGIN: chatgpt-codex-connector[bot]). Used to scope NO_OPEN_THREADS to findings
# posted by the Codex reviewer identity, excluding other actors' comments.
_CODEX_BOT_IDENTITY = "chatgpt-codex-connector[bot]"


class OpenAICodexBackendRenderer:
    """BackendRenderer that produces an ExecutionPlan for the OpenAI/Codex backend.

    Handles REVIEW (code review) and SECURITY stages only. The invocation kind
    is PR_COMMENT for both; the comment body and evidence kind differ per stage
    kind. Gate disposition is NO_OPEN_THREADS scoped to the Codex bot identity
    and the current head SHA (see module docstring). Raises ValueError for any
    other stage kind.
    """

    provider: str = PROVIDER_OPENAI
    backend: str = BACKEND_CODEX
    supported_stage_kinds: frozenset[StageKind] = frozenset({StageKind.REVIEW, StageKind.SECURITY})
    supported_gates: frozenset[StageGate] = frozenset({StageGate.BLOCKING})

    def render(self, stage: NormalizedStage) -> ExecutionPlan:
        """Produce an ExecutionPlan for the given review or security stage.

        The plan declares a PR_COMMENT invocation with the appropriate
        ``@codex`` command, an alias-only required secret, a stage-kind-specific
        EvidenceSpec correlated to the head SHA, and a NO_OPEN_THREADS gate
        disposition scoped to the Codex bot identity and the current head SHA.

        Raises ValueError for stage kinds other than REVIEW and SECURITY, and
        for stages configured with NON_BLOCKING gate semantics (V1 shared-scope
        constraint — see module docstring and design-doc 06).
        """
        if stage.gate not in self.supported_gates:
            raise ValueError(
                f"OpenAICodexBackendRenderer V1 shared-scope NO_OPEN_THREADS mode "
                f"requires BLOCKING gate semantics; stage '{stage.id}' has gate "
                f"{stage.gate!r}. Configure an alternative backend or disposition "
                f"for advisory (NON_BLOCKING) stages."
            )
        invocation = Invocation(
            kind=InvocationKind.PR_COMMENT,
            params={"body": self._resolve_codex_comment_command(stage.kind)},
        )

        review_evidence = self._build_evidence_spec(stage.kind)

        gate_disposition = self._build_gate_disposition(stage.kind)

        required_secrets = (SecretRef(alias=_TRUSTED_COMMENTER_TOKEN_ALIAS),)

        return ExecutionPlan(
            stage_id=stage.id,
            invocation=invocation,
            gate_disposition=gate_disposition,
            required_secrets=required_secrets,
            evidence=(review_evidence,),
        )

    def _build_evidence_spec(self, stage_kind: StageKind) -> EvidenceSpec:
        """Return the EvidenceSpec appropriate for the stage kind.

        REVIEW stages produce a REVIEW_RESULT spec with COMPLETED (the reviewer
        finished; findings are handled by GateDispositionSpec). SECURITY stages
        produce a COMMENT_MATCH spec with
        MATCH_FOUND using the security-review completion comment selector.
        Raises ValueError for any other stage kind.
        """
        if stage_kind is StageKind.REVIEW:
            return EvidenceSpec(
                kind=EvidenceKind.REVIEW_RESULT,
                selector=_CODEX_REVIEW_SUMMARY_SELECTOR,
                correlation=CorrelationSpec(
                    head_sha=True,
                    sha_field=_REVIEW_SUMMARY_SHA_FIELD,
                ),
                success_condition=EvidenceSuccessCondition.COMPLETED,
                produced_by=_CODEX_BOT_IDENTITY,
            )
        if stage_kind is StageKind.SECURITY:
            return EvidenceSpec(
                kind=EvidenceKind.COMMENT_MATCH,
                selector=_CODEX_SECURITY_REVIEW_MARKER_SELECTOR,
                correlation=CorrelationSpec(
                    head_sha=True,
                    sha_field=_SECURITY_REVIEW_MARKER_SHA_FIELD,
                ),
                success_condition=EvidenceSuccessCondition.MATCH_FOUND,
                produced_by=_CODEX_BOT_IDENTITY,
            )
        raise ValueError(
            f"OpenAICodexBackendRenderer does not support stage kind {stage_kind!r}; "
            f"only REVIEW and SECURITY are valid"
        )

    def _resolve_codex_comment_command(self, stage_kind: StageKind) -> str:
        """Return the PR comment body that triggers the correct Codex review kind.

        REVIEW stages use ``@codex review``; SECURITY stages use ``@codex security review``. Both
        are followed by ``CODEX_REVIEW_GUIDELINES``. Raises ValueError for any other stage kind.
        """
        if stage_kind is StageKind.REVIEW:
            return f"{_CODEX_CODE_REVIEW_COMMAND}\n\n{CODEX_REVIEW_GUIDELINES}"
        if stage_kind is StageKind.SECURITY:
            return f"{_CODEX_SECURITY_REVIEW_COMMAND}\n\n{CODEX_REVIEW_GUIDELINES}"
        raise ValueError(
            f"OpenAICodexBackendRenderer does not support stage kind {stage_kind!r}; "
            f"only REVIEW and SECURITY are valid"
        )

    def _build_gate_disposition(self, stage_kind: StageKind) -> GateDispositionSpec:
        """Return the GateDispositionSpec for the stage kind.

        Both REVIEW and SECURITY use NO_OPEN_THREADS scoped to the Codex bot
        identity and the current head SHA. REVIEW uses it because Spike B found
        no per-invocation discriminator. SECURITY uses it because the
        codex-security-review:v1 marker encodes only {blockingSeverityThreshold,
        headSha, status} — there is no findings or verdict field; status=completed
        proves completion, not a clean pass.

        FindingScopeSpec.head_sha=True declares the head-bound requirement to
        the PlatformRenderer; the concrete mechanism is PlatformRenderer scope.

        Raises ValueError for any other stage kind.
        """
        if stage_kind not in (StageKind.REVIEW, StageKind.SECURITY):
            raise ValueError(
                f"OpenAICodexBackendRenderer does not support stage kind {stage_kind!r}; "
                f"only REVIEW and SECURITY are valid"
            )
        return GateDispositionSpec(
            kind=GateDispositionKind.NO_OPEN_THREADS,
            selector="",
            scope=FindingScopeSpec(
                created_by=_CODEX_BOT_IDENTITY,
                head_sha=True,
                invocation_correlation=None,
            ),
        )
