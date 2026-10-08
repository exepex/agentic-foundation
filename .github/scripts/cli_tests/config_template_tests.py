"""Tests for the commented config template that `stagr init` writes."""
from __future__ import annotations

import yaml

from stagr.cli.config_template import build_config_template

from .harness import CONFIG_FILE_PATH, check, run_cli, starter_project
from .plan_apply_tests import parse_entries

PUBLISHER_APP_ID = "5239405"
OPTIONAL_BLOCKS = ("providers", "stages", "routing", "remediation")


def test_template_turns_on_only_the_required_keys_and_the_profile() -> None:
    for profile in ("minimal", "standard"):
        parsed = yaml.safe_load(build_config_template(profile, PUBLISHER_APP_ID))
        check(
            parsed == {
                "version": 2,
                "profile": profile,
                "platform": {"type": "github", "publisher": {"app_id": int(PUBLISHER_APP_ID)}},
            },
            f"template ({profile}): only version, profile and the publisher App are active",
        )


def test_template_offers_every_optional_block_commented_out() -> None:
    template_text = build_config_template("standard", PUBLISHER_APP_ID)
    for block in OPTIONAL_BLOCKS:
        check(f"\n#{block}:\n" in template_text, f"template: offers `{block}` commented out")
    for platform_key in ("private_key_secret", "app_slug", "same_repo_only", "trusted_roles", "auth", "labels"):
        check(f"  #{platform_key}:" in template_text, f"template: offers platform `{platform_key}` commented out")
    check("#   security  on in: standard" in template_text, "template: the stage catalog names each stage's profiles")


def test_every_optional_block_uncommented_still_validates() -> None:
    # Uncommenting the stage catalog adds every built-in stage, so both profiles plan two stage
    # workflows, routing, governance, the thread resolver and remediation.
    for profile, expected_file_count in (("minimal", 6), ("standard", 6)):
        enabled_text = build_config_template(profile, PUBLISHER_APP_ID, optional_blocks_enabled=True)
        enabled = yaml.safe_load(enabled_text)
        check(
            set(OPTIONAL_BLOCKS) <= set(enabled),
            f"template ({profile}): the enabled variant really turns every optional block on",
        )
        check(
            enabled["routing"]["fast_path"]["stages"]["normal"] == ["review", "security"],
            f"template ({profile}): the fast-path `normal` list names every built-in stage (V-S17)",
        )
        with starter_project(enabled_text) as project_root:
            exit_code, stdout, stderr = run_cli(["plan", "--root", str(project_root)])
            check(
                exit_code == 0 and len(parse_entries(stdout)) == expected_file_count,
                f"template ({profile}): every block uncommented plans {expected_file_count} files ({stderr.strip()})",
            )


def test_init_writes_the_template_and_profile_switch_keeps_it() -> None:
    with starter_project("") as project_root:
        (project_root / CONFIG_FILE_PATH).unlink()
        exit_code, _, _ = run_cli(["init", "--root", str(project_root), "--app-id", PUBLISHER_APP_ID])
        written_text = (project_root / CONFIG_FILE_PATH).read_text(encoding="utf-8")
        check(
            exit_code == 0 and written_text == build_config_template("standard", PUBLISHER_APP_ID),
            "init: writes the full template for the default standard profile",
        )
        switch_exit_code, _, _ = run_cli(["profile", "minimal", "--root", str(project_root)])
        switched_text = (project_root / CONFIG_FILE_PATH).read_text(encoding="utf-8")
        check(
            switch_exit_code == 0
            and switched_text == written_text.replace("\nprofile: standard\n", "\nprofile: minimal\n"),
            "profile: switching changes only the profile line of the template",
        )


def _uncomment_settings(template_text: str, enabled_text: str, removed_prefix: str) -> str:
    """Uncomment every setting line (a line that differs in the enabled variant) the way a user would.

    ``removed_prefix`` is what the user deletes after the indentation: "#" by hand, or "# " when an
    editor's toggle-comment also takes the space that follows the `#`.
    """
    uncommented_lines = []
    for template_line, enabled_line in zip(template_text.splitlines(), enabled_text.splitlines()):
        if template_line != enabled_line:
            indentation = template_line[: len(template_line) - len(template_line.lstrip())]
            body = template_line.lstrip()
            prefix = removed_prefix if body.startswith(removed_prefix) else "#"
            template_line = indentation + body[len(prefix):]
        uncommented_lines.append(template_line)
    return "\n".join(uncommented_lines) + "\n"


def test_uncommenting_a_block_by_hand_or_by_editor_gives_valid_yaml() -> None:
    template_text = build_config_template("minimal", PUBLISHER_APP_ID)
    enabled_text = build_config_template("minimal", PUBLISHER_APP_ID, optional_blocks_enabled=True)
    for removed_prefix, method in (("#", "deleting the `#`"), ("# ", "an editor's toggle-comment")):
        try:
            uncommented = yaml.safe_load(_uncomment_settings(template_text, enabled_text, removed_prefix))
        except yaml.YAMLError as parse_error:
            uncommented = f"invalid YAML: {str(parse_error).splitlines()[0]}"
        check(
            uncommented == yaml.safe_load(enabled_text),
            f"template: uncommenting every block by {method} gives the enabled config ({str(uncommented)[:80]})",
        )


CONFIG_TEMPLATE_TESTS = (
    test_template_turns_on_only_the_required_keys_and_the_profile,
    test_template_offers_every_optional_block_commented_out,
    test_every_optional_block_uncommented_still_validates,
    test_uncommenting_a_block_by_hand_or_by_editor_gives_valid_yaml,
    test_init_writes_the_template_and_profile_switch_keeps_it,
)


DEMO_CONFIG = (
    "version: 2\nprofile: minimal\nplatform:\n  type: github\n  publisher:\n    app_id: 5239405\n"
    "remediation:\n  provider: anthropic\n"
)


def test_init_force_regenerates_keeping_profile_and_app_id() -> None:
    with starter_project(DEMO_CONFIG) as project_root:
        exit_code, stdout, stderr = run_cli(["init", "--force", "--root", str(project_root)])
        regenerated_text = (project_root / CONFIG_FILE_PATH).read_text(encoding="utf-8")
        check(
            exit_code == 0 and regenerated_text == build_config_template("minimal", PUBLISHER_APP_ID),
            f"init --force: regenerates the template with the existing profile and App ID ({stderr.strip()})",
        )
        check(
            "regenerated" in stdout and "now commented out in the template: remediation" in stdout,
            "init --force: names every active setting it turned off",
        )


def test_init_without_force_still_refuses_and_points_to_force() -> None:
    with starter_project(DEMO_CONFIG) as project_root:
        exit_code, _, stderr = run_cli(["init", "--root", str(project_root), "--app-id", PUBLISHER_APP_ID])
        kept_text = (project_root / CONFIG_FILE_PATH).read_text(encoding="utf-8")
        check(
            exit_code == 1 and "stagr init --force" in stderr and kept_text == DEMO_CONFIG,
            "init: an existing config is kept and the error points to --force",
        )


def test_init_force_flags_override_the_existing_values() -> None:
    with starter_project(DEMO_CONFIG) as project_root:
        exit_code, _, _ = run_cli(["init", "--force", "--root", str(project_root), "--profile", "standard", "--app-id", "42"])
        check(
            exit_code == 0
            and (project_root / CONFIG_FILE_PATH).read_text(encoding="utf-8") == build_config_template("standard", "42"),
            "init --force: --profile and --app-id win over the existing config",
        )


def test_init_force_refuses_a_custom_config_without_a_profile_flag() -> None:
    custom_config = DEMO_CONFIG.replace("profile: minimal", "profile: custom") + (
        "stages:\n  - id: review\n    type: review\n    provider: openai\n    gate: blocking\n"
    )
    with starter_project(custom_config) as project_root:
        exit_code, _, stderr = run_cli(["init", "--force", "--root", str(project_root)])
        kept_text = (project_root / CONFIG_FILE_PATH).read_text(encoding="utf-8")
        check(
            exit_code == 1 and "pass --profile" in stderr and kept_text == custom_config,
            "init --force: a custom config is kept unless --profile names a starter profile",
        )


def _run_init_force_interrupted_by(failure: BaseException, original_bytes: bytes) -> tuple[int, bytes]:
    """Run `init --force` on a config holding ``original_bytes`` with the plan check raising ``failure``."""
    from stagr.cli import init_command

    def raise_failure(_project_root):
        raise failure

    original_validate = init_command.validate_and_render
    init_command.validate_and_render = raise_failure
    try:
        with starter_project("") as project_root:
            (project_root / CONFIG_FILE_PATH).write_bytes(original_bytes)
            exit_code, _, _ = run_cli(["init", "--force", "--root", str(project_root)])
            return exit_code, (project_root / CONFIG_FILE_PATH).read_bytes()
    finally:
        init_command.validate_and_render = original_validate


def test_init_force_restores_the_exact_bytes_on_cancel_and_failure() -> None:
    from stagr.cli.plan_apply import PIPELINE_FAILURES

    crlf_bytes = DEMO_CONFIG.replace("\n", "\r\n").encode("utf-8")
    for failure in (KeyboardInterrupt(), EOFError(), PIPELINE_FAILURES[0]("plan check failed")):
        exit_code, kept_bytes = _run_init_force_interrupted_by(failure, crlf_bytes)
        check(
            exit_code == 1 and kept_bytes == crlf_bytes,
            f"init --force: {type(failure).__name__} restores the replaced config byte for byte",
        )


def test_init_force_regenerates_a_malformed_platform_section() -> None:
    for malformed_platform in ("platform: github\n", "platform:\n  type: github\n  publisher: 5239405\n"):
        with starter_project(f"version: 2\nprofile: minimal\n{malformed_platform}") as project_root:
            exit_code, _, stderr = run_cli(["init", "--force", "--root", str(project_root), "--app-id", "42"])
            check(
                exit_code == 0
                and (project_root / CONFIG_FILE_PATH).read_text(encoding="utf-8") == build_config_template("minimal", "42"),
                f"init --force: a malformed platform section is regenerated, not a crash ({stderr.strip()})",
            )


def test_init_force_reports_removed_settings_apart_from_commented_ones() -> None:
    config_with_unoffered_setting = DEMO_CONFIG + "merge:\n  discussions:\n    require_resolved: true\n"
    with starter_project(config_with_unoffered_setting) as project_root:
        exit_code, stdout, _ = run_cli(["init", "--force", "--root", str(project_root)])
        check(
            exit_code == 0
            and "now commented out in the template: remediation;" in stdout
            and "removed, not offered by the template: merge;" in stdout,
            "init --force: a setting the template does not offer is reported as removed, not commented out",
        )


CONFIG_TEMPLATE_TESTS = CONFIG_TEMPLATE_TESTS + (
    test_init_force_restores_the_exact_bytes_on_cancel_and_failure,
    test_init_force_regenerates_a_malformed_platform_section,
    test_init_force_reports_removed_settings_apart_from_commented_ones,
    test_init_force_regenerates_keeping_profile_and_app_id,
    test_init_without_force_still_refuses_and_points_to_force,
    test_init_force_flags_override_the_existing_values,
    test_init_force_refuses_a_custom_config_without_a_profile_flag,
)


def _hinted_values(hint: str) -> list[str]:
    return [value.split(" (")[0] for value in hint.split(": ", 1)[1].split(", ")]


def test_template_hints_name_the_allowed_values() -> None:
    template_text = build_config_template("minimal", PUBLISHER_APP_ID)
    for expected_hint in (
        "# supported today: review, security",
        "# supported today: openai (backend: codex by default, or codex-api)",
        "# supported today: blocking",
        "# any of: pr_opened, pr_updated, manual, issue_labeled",
        "# any of: owner, member, collaborator, contributor",
        "# supported: anthropic (Claude Code Action)",
        "# 1 to 10",
    ):
        check(expected_hint in template_text, f"template: shows the allowed values `{expected_hint}`")
    enabled = yaml.safe_load(build_config_template("minimal", PUBLISHER_APP_ID, optional_blocks_enabled=True))
    check(
        enabled["platform"]["trusted_roles"] == ["owner", "member", "collaborator"]
        and enabled["stages"][0]["triggers"] == ["pr_opened", "pr_updated"]
        and enabled["remediation"]["max_rounds"] == 5,
        "template: uncommented, every hint is a YAML comment, not part of the value",
    )


def test_every_hinted_stage_value_passes_plan() -> None:
    from stagr.cli.config_value_hints import build_stage_hints

    stage_hints = build_stage_hints()
    stage_cases = [
        {"type": stage_type, "gate": gate, "triggers": trigger}
        for stage_type in _hinted_values(stage_hints["type"])
        for gate in _hinted_values(stage_hints["gate"])
        for trigger in _hinted_values(stage_hints["triggers"])
    ]
    provider = _hinted_values(stage_hints["provider"])[0]
    for case in stage_cases:
        config_text = (
            f"version: 2\nprofile: custom\nplatform: {{type: github, publisher: {{app_id: {PUBLISHER_APP_ID}}}}}\n"
            f"stages:\n  - {{id: check, type: {case['type']}, provider: {provider}, gate: {case['gate']}, "
            f"triggers: [{case['triggers']}]}}\n"
        )
        with starter_project(config_text) as project_root:
            exit_code, _, stderr = run_cli(["plan", "--root", str(project_root)])
            check(exit_code == 0, f"template: the hinted stage values {case} pass `stagr plan` ({stderr.strip()})")


CONFIG_TEMPLATE_TESTS = CONFIG_TEMPLATE_TESTS + (
    test_template_hints_name_the_allowed_values,
    test_every_hinted_stage_value_passes_plan,
)
