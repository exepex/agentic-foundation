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
        check(f"\n# {block}:\n" in template_text, f"template: offers `{block}` commented out")
    for platform_key in ("private_key_secret", "same_repo_only", "trusted_roles", "auth", "labels"):
        check(f"# {platform_key}:" in template_text, f"template: offers platform `{platform_key}` commented out")
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


CONFIG_TEMPLATE_TESTS = (
    test_template_turns_on_only_the_required_keys_and_the_profile,
    test_template_offers_every_optional_block_commented_out,
    test_every_optional_block_uncommented_still_validates,
    test_init_writes_the_template_and_profile_switch_keeps_it,
)
