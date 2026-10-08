"""Tests for the `remediation` config section through `stagr plan` and `stagr apply`."""
from __future__ import annotations

import yaml

from .harness import check, run_cli, starter_project
from .plan_apply_tests import parse_entries

REMEDIATION_WORKFLOW = ".github/workflows/remediation.yml"


def build_minimal_config(remediation_block: str = "") -> str:
    return (
        "version: 2\n"
        "profile: minimal\n"
        "platform:\n"
        "  type: github\n"
        "  publisher:\n"
        "    app_id: 5239405\n"
        f"{remediation_block}"
    )


def test_minimal_profile_with_remediation_plans_the_fix_workflow() -> None:
    with starter_project(build_minimal_config("remediation:\n  provider: anthropic\n")) as project_root:
        exit_code, stdout, stderr = run_cli(["plan", "--root", str(project_root)])
        entries = parse_entries(stdout)
        check(exit_code == 0 and stderr == "", "remediation: the minimal profile with remediation plans cleanly")
        check(
            sorted(path.rsplit("/", 1)[1] for path in entries)
            == ["governance.yml", "remediation.yml", "resolve-outdated-threads.yml", "routing.yml", "stage-review.yml"],
            "remediation: plans the fix workflow next to the review, routing, governance and resolver",
        )
        run_cli(["apply", "--root", str(project_root)])
        workflow = yaml.safe_load((project_root / REMEDIATION_WORKFLOW).read_text(encoding="utf-8"))
        limit_environment = workflow["jobs"]["remediate"]["steps"][0]["env"]
        check(limit_environment["MAX_ROUNDS"] == "5", "remediation: the fix-round limit defaults to 5")


def test_without_remediation_no_fix_workflow_is_planned() -> None:
    with starter_project(build_minimal_config()) as project_root:
        _, stdout, _ = run_cli(["plan", "--root", str(project_root)])
        check(REMEDIATION_WORKFLOW not in parse_entries(stdout), "remediation: omitted means no fix workflow")


def test_invalid_remediation_settings_are_rejected() -> None:
    for remediation_block, description in (
        ("remediation:\n  provider: anthropic\n  max_rounds: 0\n", "a round limit below 1"),
        ("remediation:\n  provider: anthropic\n  max_rounds: 11\n", "a round limit above 10"),
        ("remediation:\n  provider: openai\n", "an unknown provider"),
        ("remediation:\n  max_rounds: 5\n", "a missing provider"),
        ("remediation:\n  provider: anthropic\n  api_key_secret: sk-ant-not-a-name\n", "a key pasted as the secret name"),
    ):
        with starter_project(build_minimal_config(remediation_block)) as project_root:
            exit_code, _, stderr = run_cli(["plan", "--root", str(project_root)])
            check(exit_code == 1 and "sk-ant" not in stderr, f"remediation: rejects {description}")


REMEDIATION_TESTS = (
    test_minimal_profile_with_remediation_plans_the_fix_workflow,
    test_without_remediation_no_fix_workflow_is_planned,
    test_invalid_remediation_settings_are_rejected,
)
