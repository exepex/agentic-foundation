"""Tests for OpenAICodexBackendRenderer invocation, identity, and basic contracts."""
from __future__ import annotations

from neutral_core_tests.codex_renderer_tests.helpers import (
    build_renderer,
    build_review_normalized_stage,
    build_security_normalized_stage,
)


def test_codex_renderer_invocation_kind_is_pr_comment() -> None:
    """render() returns an ExecutionPlan with invocation.kind == PR_COMMENT."""
    from stagr.core.enums import InvocationKind

    renderer = build_renderer()
    review_stage = build_review_normalized_stage()

    execution_plan = renderer.render(review_stage)

    assert execution_plan.invocation.kind is InvocationKind.PR_COMMENT, (
        f"Expected invocation.kind PR_COMMENT, got {execution_plan.invocation.kind!r}"
    )


def test_codex_renderer_required_secret_alias_and_no_env_name() -> None:
    """required_secrets has exactly one SecretRef with alias TRUSTED_COMMENTER_TOKEN and env_name None."""
    renderer = build_renderer()
    review_stage = build_review_normalized_stage()

    execution_plan = renderer.render(review_stage)

    assert len(execution_plan.required_secrets) == 1, (
        f"Expected exactly 1 required secret, got {len(execution_plan.required_secrets)}"
    )
    secret_ref = execution_plan.required_secrets[0]
    assert secret_ref.alias == "TRUSTED_COMMENTER_TOKEN", (
        f"Expected alias 'TRUSTED_COMMENTER_TOKEN', got {secret_ref.alias!r}"
    )
    assert secret_ref.env_name is None, (
        f"BackendRenderer must not set SecretRef.env_name; "
        f"got env_name={secret_ref.env_name!r} for alias={secret_ref.alias!r}"
    )


def test_codex_renderer_gate_disposition_kind_is_no_open_threads() -> None:
    """gate_disposition.kind == NO_OPEN_THREADS (conservative: zero Codex findings on head)."""
    from stagr.core.enums import GateDispositionKind

    renderer = build_renderer()
    review_stage = build_review_normalized_stage()

    execution_plan = renderer.render(review_stage)

    assert execution_plan.gate_disposition.kind is GateDispositionKind.NO_OPEN_THREADS, (
        f"Expected gate_disposition.kind NO_OPEN_THREADS; "
        f"got {execution_plan.gate_disposition.kind!r}"
    )


def test_codex_renderer_evidence_has_one_review_result_spec() -> None:
    """evidence contains exactly one EvidenceSpec with kind == REVIEW_RESULT."""
    from stagr.core.enums import EvidenceKind

    renderer = build_renderer()
    review_stage = build_review_normalized_stage()

    execution_plan = renderer.render(review_stage)

    assert len(execution_plan.evidence) == 1, (
        f"Expected exactly 1 EvidenceSpec, got {len(execution_plan.evidence)}"
    )
    evidence_spec = execution_plan.evidence[0]
    assert evidence_spec.kind is EvidenceKind.REVIEW_RESULT, (
        f"Expected evidence[0].kind REVIEW_RESULT, got {evidence_spec.kind!r}"
    )


def test_codex_renderer_evidence_head_sha_correlation_is_true() -> None:
    """evidence[0].correlation.head_sha is True (evidence must bind to the current head commit)."""
    renderer = build_renderer()
    review_stage = build_review_normalized_stage()

    execution_plan = renderer.render(review_stage)

    head_sha_correlation = execution_plan.evidence[0].correlation
    assert head_sha_correlation.head_sha is True, (
        f"Expected evidence[0].correlation.head_sha True, "
        f"got {head_sha_correlation.head_sha!r}"
    )


def test_codex_renderer_protocol_conformance() -> None:
    """isinstance(renderer, BackendRenderer) passes (runtime Protocol check)."""
    from stagr.core.backend_renderer import BackendRenderer

    renderer = build_renderer()

    assert isinstance(renderer, BackendRenderer), (
        f"OpenAICodexBackendRenderer must satisfy the BackendRenderer Protocol; "
        f"isinstance check failed for type {type(renderer)}"
    )


def test_codex_renderer_provider_and_backend_match_config() -> None:
    """renderer.provider == 'openai' and renderer.backend matches the BACKEND_CODEX constant."""
    from stagr.core.backend_names import BACKEND_CODEX

    renderer = build_renderer()

    assert renderer.provider == "openai", (
        f"Expected provider 'openai', got {renderer.provider!r}"
    )
    assert renderer.backend == BACKEND_CODEX, (
        f"Expected backend '{BACKEND_CODEX}' (from BACKEND_CODEX constant), "
        f"got {renderer.backend!r}"
    )


def test_codex_renderer_review_stage_posts_codex_review_command() -> None:
    """A REVIEW stage invocation.params['body'] contains '@codex review' (not the security command)."""
    renderer = build_renderer()
    review_stage = build_review_normalized_stage()

    execution_plan = renderer.render(review_stage)

    comment_body = execution_plan.invocation.params.get("body", "")
    assert "@codex review" in comment_body, (
        f"Expected invocation body to contain '@codex review', got {comment_body!r}"
    )
    assert "security" not in comment_body, (
        f"REVIEW stage must not post the security review command; got {comment_body!r}"
    )


def test_codex_renderer_security_stage_posts_security_review_command() -> None:
    """A SECURITY stage invocation.params['body'] contains '@codex security review'."""
    renderer = build_renderer()
    security_stage = build_security_normalized_stage()

    execution_plan = renderer.render(security_stage)

    comment_body = execution_plan.invocation.params.get("body", "")
    assert "@codex security review" in comment_body, (
        f"Expected invocation body to contain '@codex security review', "
        f"got {comment_body!r}"
    )


def test_codex_review_requests_carry_the_reasoning_guidelines() -> None:
    """Both review commands ask Codex for realistic findings only, after the command itself."""
    from stagr.core.renderers.openai_codex_backend_renderer import CODEX_REVIEW_GUIDELINES

    renderer = build_renderer()
    for stage, command in (
        (build_review_normalized_stage(), "@codex review"),
        (build_security_normalized_stage(), "@codex security review"),
    ):
        comment_body = renderer.render(stage).invocation.params["body"]
        assert comment_body == f"{command}\n\n{CODEX_REVIEW_GUIDELINES}", f"unexpected body {comment_body!r}"
    assert "hypothetical or highly unlikely" in CODEX_REVIEW_GUIDELINES
    assert "When unsure whether a finding is real, leave it out." in CODEX_REVIEW_GUIDELINES
