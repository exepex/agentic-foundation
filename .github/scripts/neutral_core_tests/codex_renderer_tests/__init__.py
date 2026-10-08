"""Codex renderer test sub-package.

Re-exports all test functions for use by the top-level test runner, and
exposes CODEX_RENDERER_TESTS as the ordered list the runner appends to
_TESTS.
"""
from __future__ import annotations

from neutral_core_tests.codex_renderer_tests.test_invocation import (
    test_codex_renderer_invocation_kind_is_pr_comment,
    test_codex_renderer_required_secret_alias_and_no_env_name,
    test_codex_renderer_gate_disposition_kind_is_no_open_threads,
    test_codex_renderer_evidence_has_one_review_result_spec,
    test_codex_renderer_evidence_head_sha_correlation_is_true,
    test_codex_renderer_protocol_conformance,
    test_codex_renderer_provider_and_backend_match_config,
    test_codex_renderer_review_stage_posts_codex_review_command,
    test_codex_renderer_security_stage_posts_security_review_command,
    test_codex_review_requests_carry_the_reasoning_guidelines,
)
from neutral_core_tests.codex_renderer_tests.test_evidence_and_rejection import (
    test_codex_renderer_gate_disposition_scope_is_codex_bot_head_bound,
    test_codex_renderer_security_stage_gate_disposition_is_no_open_threads,
    test_codex_renderer_review_stage_evidence_success_condition_is_completed,
    test_codex_renderer_security_stage_evidence_is_comment_match,
    test_codex_renderer_security_stage_evidence_success_condition_is_match_found,
    test_codex_renderer_security_stage_evidence_selector_is_verified_marker,
    test_codex_renderer_security_stage_evidence_sha_field_is_head_sha,
    test_codex_renderer_unsupported_stage_kind_raises_value_error,
    test_codex_renderer_non_blocking_stage_raises_value_error,
    test_codex_renderer_evidence_specs_declare_the_codex_bot_as_producer,
)

CODEX_RENDERER_TESTS = [
    test_codex_renderer_invocation_kind_is_pr_comment,
    test_codex_renderer_required_secret_alias_and_no_env_name,
    test_codex_renderer_gate_disposition_kind_is_no_open_threads,
    test_codex_renderer_evidence_has_one_review_result_spec,
    test_codex_renderer_evidence_head_sha_correlation_is_true,
    test_codex_renderer_protocol_conformance,
    test_codex_renderer_provider_and_backend_match_config,
    test_codex_renderer_review_stage_posts_codex_review_command,
    test_codex_renderer_security_stage_posts_security_review_command,
    test_codex_review_requests_carry_the_reasoning_guidelines,
    test_codex_renderer_gate_disposition_scope_is_codex_bot_head_bound,
    test_codex_renderer_security_stage_gate_disposition_is_no_open_threads,
    test_codex_renderer_review_stage_evidence_success_condition_is_completed,
    test_codex_renderer_security_stage_evidence_is_comment_match,
    test_codex_renderer_security_stage_evidence_success_condition_is_match_found,
    test_codex_renderer_security_stage_evidence_selector_is_verified_marker,
    test_codex_renderer_security_stage_evidence_sha_field_is_head_sha,
    test_codex_renderer_unsupported_stage_kind_raises_value_error,
    test_codex_renderer_non_blocking_stage_raises_value_error,
    test_codex_renderer_evidence_specs_declare_the_codex_bot_as_producer,
]

__all__ = [
    "CODEX_RENDERER_TESTS",
    "test_codex_renderer_invocation_kind_is_pr_comment",
    "test_codex_renderer_required_secret_alias_and_no_env_name",
    "test_codex_renderer_gate_disposition_kind_is_no_open_threads",
    "test_codex_renderer_evidence_has_one_review_result_spec",
    "test_codex_renderer_evidence_head_sha_correlation_is_true",
    "test_codex_renderer_protocol_conformance",
    "test_codex_renderer_provider_and_backend_match_config",
    "test_codex_renderer_review_stage_posts_codex_review_command",
    "test_codex_renderer_security_stage_posts_security_review_command",
    "test_codex_review_requests_carry_the_reasoning_guidelines",
    "test_codex_renderer_gate_disposition_scope_is_codex_bot_head_bound",
    "test_codex_renderer_security_stage_gate_disposition_is_no_open_threads",
    "test_codex_renderer_review_stage_evidence_success_condition_is_completed",
    "test_codex_renderer_security_stage_evidence_is_comment_match",
    "test_codex_renderer_security_stage_evidence_success_condition_is_match_found",
    "test_codex_renderer_security_stage_evidence_selector_is_verified_marker",
    "test_codex_renderer_security_stage_evidence_sha_field_is_head_sha",
    "test_codex_renderer_unsupported_stage_kind_raises_value_error",
    "test_codex_renderer_non_blocking_stage_raises_value_error",
    "test_codex_renderer_evidence_specs_declare_the_codex_bot_as_producer",
]
