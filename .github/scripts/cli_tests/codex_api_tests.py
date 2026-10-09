"""Tests for the `codex-api` backend (Codex on an OpenAI API key) through `stagr plan` and `apply`."""
from __future__ import annotations

import yaml

from .harness import check, run_cli, starter_project
from .plan_apply_tests import parse_entries


def build_codex_api_config(app_slug_line: str = "    app_slug: stagr-demo\n") -> str:
    """The standard profile with both of its stages switched to the API-key backend."""
    return (
        "version: 2\n"
        "profile: standard\n"
        "platform:\n"
        "  type: github\n"
        "  publisher:\n"
        "    app_id: 5239405\n"
        f"{app_slug_line}"
        "stages:\n"
        "  - id: review\n"
        "    type: review\n"
        "    backend: codex-api\n"
        "  - id: security\n"
        "    type: security\n"
        "    backend: codex-api\n"
        "remediation:\n"
        "  provider: anthropic\n"
    )


def test_codex_api_stages_plan_and_apply() -> None:
    with starter_project(build_codex_api_config()) as project_root:
        exit_code, stdout, stderr = run_cli(["plan", "--root", str(project_root)])
        check(
            exit_code == 0 and len(parse_entries(stdout)) == 6,
            f"codex-api: the standard profile on the API-key backend plans six files ({stderr.strip()})",
        )
        run_cli(["apply", "--root", str(project_root)])
        workflows = project_root / ".github" / "workflows"
        review_jobs = yaml.safe_load((workflows / "stage-review.yml").read_text(encoding="utf-8"))["jobs"]
        codex_step = next(step for step in review_jobs["review"]["steps"] if "codex-action" in step.get("uses", ""))
        check(
            codex_step["with"]["openai-api-key"] == "${{ secrets.OPENAI_API_KEY }}"
            and codex_step["with"]["allow-bot-users"] == "stagr-demo,claude",
            "codex-api: Codex runs on the OPENAI_API_KEY secret and admits the App and the fixing agent",
        )
        remediation_text = (workflows / "remediation.yml").read_text(encoding="utf-8")
        check(
            '"stagr-demo[bot]"' in remediation_text,
            "codex-api: the fix agent answers the reviews the App posts",
        )


def test_codex_api_without_the_app_slug_names_the_missing_setting() -> None:
    with starter_project(build_codex_api_config(app_slug_line="")) as project_root:
        exit_code, _, stderr = run_cli(["plan", "--root", str(project_root)])
        check(
            exit_code == 1 and "platform.publisher.app_slug" in stderr,
            "codex-api: without platform.publisher.app_slug, plan stops and names the setting",
        )


def test_codex_api_key_secret_can_be_renamed() -> None:
    renamed = build_codex_api_config().replace(
        "remediation:\n", "providers:\n  openai:\n    secrets:\n      OPENAI_API_KEY: TEAM_OPENAI_KEY\nremediation:\n"
    )
    with starter_project(renamed) as project_root:
        run_cli(["apply", "--root", str(project_root)])
        workflow_text = (project_root / ".github" / "workflows" / "stage-review.yml").read_text(encoding="utf-8")
        check(
            "${{ secrets.TEAM_OPENAI_KEY }}" in workflow_text and "secrets.OPENAI_API_KEY" not in workflow_text,
            "codex-api: providers.openai.secrets.OPENAI_API_KEY renames the key's secret",
        )


CODEX_API_TESTS = (
    test_codex_api_stages_plan_and_apply,
    test_codex_api_without_the_app_slug_names_the_missing_setting,
    test_codex_api_key_secret_can_be_renamed,
)
