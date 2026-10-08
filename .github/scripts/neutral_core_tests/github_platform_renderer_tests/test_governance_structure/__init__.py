"""Governance workflow structure test sub-package.

Re-exports all test functions for use by the parent package and the top-level
test runner.
"""
from __future__ import annotations

from neutral_core_tests.github_platform_renderer_tests.test_governance_structure.test_token_and_publisher import (
    test_governance_artifact_has_correct_path,
    test_governance_workflow_references_private_key_secret,
    test_governance_workflow_references_publisher_app_id_in_token_step,
    test_governance_workflow_app_token_step_uses_pinned_sha,
    test_governance_workflow_has_app_token_step_with_id,
    test_governance_workflow_embeds_stagr_app_id_as_literal_for_verification,
    test_governance_workflow_has_publisher_identity_rejection_logic,
)
from neutral_core_tests.github_platform_renderer_tests.test_governance_structure.test_signal_evaluation import (
    test_governance_workflow_has_blocked_conclusion_findings_message,
    test_governance_workflow_has_failed_conclusion_did_not_complete_message,
    test_governance_workflow_has_duplicate_check_run_detection,
    test_governance_workflow_validates_schema_version,
    test_governance_workflow_uses_schema_version_one,
    test_governance_workflow_reads_signal_from_output_summary,
    test_governance_workflow_reads_state_from_payload,
    test_governance_workflow_reads_conclusion_from_payload,
    test_governance_workflow_has_head_sha_binding_check,
    test_governance_workflow_has_merge_eligible_message,
    test_governance_workflow_handles_missing_check_run,
)
from neutral_core_tests.github_platform_renderer_tests.test_governance_structure.test_stage_routing import (
    test_governance_workflow_contains_stage_signal_selector,
    test_governance_workflow_contains_multiple_stage_selectors,
    test_governance_workflow_marks_blocking_stage_as_blocking,
    test_governance_workflow_marks_non_blocking_stage_as_non_blocking,
    test_governance_workflow_non_blocking_stage_missing_check_run_does_not_gate_merge,
    test_governance_workflow_fast_path_only_evaluates_fast_route_stages,
    test_governance_route_publisher_authentication_rejects_forged_app,
    test_non_blocking_stage_call_has_or_true_suffix,
    test_unrouted_stage_absent_from_generated_script,
    test_stage_check_run_query_uses_filter_all,
)
from neutral_core_tests.github_platform_renderer_tests.test_governance_structure.test_verdict_publishing import (
    test_governance_publishes_verdict_check_run_on_pull_request_head,
    test_governance_verdict_fails_closed,
    test_governance_verdict_unchanged_is_not_republished,
    test_governance_job_skips_check_suites_without_pull_request,
)

__all__ = [
    "test_governance_artifact_has_correct_path",
    "test_governance_workflow_references_private_key_secret",
    "test_governance_workflow_references_publisher_app_id_in_token_step",
    "test_governance_workflow_app_token_step_uses_pinned_sha",
    "test_governance_workflow_has_app_token_step_with_id",
    "test_governance_workflow_embeds_stagr_app_id_as_literal_for_verification",
    "test_governance_workflow_has_publisher_identity_rejection_logic",
    "test_governance_workflow_has_blocked_conclusion_findings_message",
    "test_governance_workflow_has_failed_conclusion_did_not_complete_message",
    "test_governance_workflow_has_duplicate_check_run_detection",
    "test_governance_workflow_validates_schema_version",
    "test_governance_workflow_uses_schema_version_one",
    "test_governance_workflow_reads_signal_from_output_summary",
    "test_governance_workflow_reads_state_from_payload",
    "test_governance_workflow_reads_conclusion_from_payload",
    "test_governance_workflow_has_head_sha_binding_check",
    "test_governance_workflow_has_merge_eligible_message",
    "test_governance_workflow_handles_missing_check_run",
    "test_governance_workflow_contains_stage_signal_selector",
    "test_governance_workflow_contains_multiple_stage_selectors",
    "test_governance_workflow_marks_blocking_stage_as_blocking",
    "test_governance_workflow_marks_non_blocking_stage_as_non_blocking",
    "test_governance_workflow_non_blocking_stage_missing_check_run_does_not_gate_merge",
    "test_governance_workflow_fast_path_only_evaluates_fast_route_stages",
    "test_governance_route_publisher_authentication_rejects_forged_app",
    "test_non_blocking_stage_call_has_or_true_suffix",
    "test_unrouted_stage_absent_from_generated_script",
    "test_stage_check_run_query_uses_filter_all",
    "test_governance_publishes_verdict_check_run_on_pull_request_head",
    "test_governance_verdict_fails_closed",
    "test_governance_verdict_unchanged_is_not_republished",
    "test_governance_job_skips_check_suites_without_pull_request",
]
