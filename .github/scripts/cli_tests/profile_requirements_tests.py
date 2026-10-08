"""Tests for V-S16: a built-in profile's stages are the required minimum of the pipeline."""
from __future__ import annotations

from .harness import check, run_cli, starter_project
from .plan_apply_tests import parse_entries

SECURITY_STAGE = (
    "  - id: security\n    type: security\n    provider: openai\n    skill: security-review\n"
    "    gate: blocking\n    triggers: [pr_opened, pr_updated]\n    depends_on: [review]\n"
)


def build_config(profile: str, extra: str = "") -> str:
    return (
        f"version: 2\nprofile: {profile}\nplatform:\n  type: github\n  publisher:\n    app_id: 5239405\n{extra}"
    )


def plan(config_text: str) -> tuple[int, str, str]:
    with starter_project(config_text) as project_root:
        return run_cli(["plan", "--root", str(project_root)])


def test_minimal_with_remediation_is_valid() -> None:
    exit_code, stdout, _ = plan(build_config("minimal", "remediation:\n  provider: anthropic\n"))
    check(exit_code == 0 and len(parse_entries(stdout)) == 5, "V-S16: minimal + remediation is valid")


def test_minimal_may_add_a_stage() -> None:
    exit_code, stdout, _ = plan(build_config("minimal", "stages:\n" + SECURITY_STAGE))
    check(exit_code == 0 and len(parse_entries(stdout)) == 5, "V-S16: minimal may add the security stage")


def test_standard_with_remediation_is_valid() -> None:
    exit_code, stdout, _ = plan(build_config("standard", "remediation:\n  provider: anthropic\n"))
    check(exit_code == 0 and len(parse_entries(stdout)) == 6, "V-S16: standard + remediation is valid")


def test_disabling_a_required_stage_fails() -> None:
    exit_code, _, stderr = plan(build_config("standard", "stages:\n  - id: security\n    type: security\n    enabled: false\n"))
    check(
        exit_code == 1 and "V-S16" in stderr and "stage 'security' is disabled" in stderr,
        "V-S16: standard with security disabled fails",
    )
    exit_code, _, stderr = plan(build_config("minimal", "stages:\n  - id: review\n    type: review\n    enabled: false\n"))
    check(exit_code == 1 and "stage 'review' is disabled" in stderr, "V-S16: minimal with review disabled fails")


def test_a_required_stage_must_stay_blocking() -> None:
    exit_code, _, stderr = plan(build_config("standard", "stages:\n  - id: security\n    type: security\n    gate: advisory\n"))
    check(
        exit_code == 1 and "stage 'security' is not blocking (gate: advisory)" in stderr,
        "V-S16: standard with an advisory security stage fails",
    )


def test_other_settings_of_a_required_stage_may_change() -> None:
    exit_code, _, stderr = plan(build_config("standard", "stages:\n  - id: security\n    type: security\n    triggers: [pr_opened]\n"))
    check(exit_code == 0, f"V-S16: a required stage may change its triggers ({stderr.strip()})")


def test_custom_requires_no_stage() -> None:
    only_security = "stages:\n" + SECURITY_STAGE.replace("    depends_on: [review]\n", "")
    exit_code, stdout, stderr = plan(build_config("custom", only_security))
    check(
        exit_code == 0 and len(parse_entries(stdout)) == 4,
        f"V-S16: custom requires no particular stage (security alone is valid) ({stderr.strip()})",
    )


def test_profile_switch_is_refused_when_it_would_break_the_new_profile() -> None:
    advisory_security = "stages:\n" + SECURITY_STAGE.replace("gate: blocking", "gate: advisory")
    with starter_project(build_config("minimal", advisory_security)) as project_root:
        exit_code, _, stderr = run_cli(["profile", "standard", "--root", str(project_root)])
        kept_text = (project_root / ".agentic" / "config.yml").read_text(encoding="utf-8")
        check(
            exit_code == 1 and "V-S16" in stderr and "profile: minimal" in kept_text,
            "V-S16: switching to standard with an advisory security stage is refused and restored",
        )


PROFILE_REQUIREMENTS_TESTS = (
    test_minimal_with_remediation_is_valid,
    test_minimal_may_add_a_stage,
    test_standard_with_remediation_is_valid,
    test_disabling_a_required_stage_fails,
    test_a_required_stage_must_stay_blocking,
    test_other_settings_of_a_required_stage_may_change,
    test_custom_requires_no_stage,
    test_profile_switch_is_refused_when_it_would_break_the_new_profile,
)
