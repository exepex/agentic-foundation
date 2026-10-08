"""Tests for `stagr plan` and `stagr apply`, which share one pipeline and must not differ."""
from __future__ import annotations

import hashlib
import os
import re
import stat
from typing import Any, Callable

import yaml

from .harness import (
    CONFIG_FILE_PATH,
    EXPECTED_DOGFOOD_WORKFLOW_PATHS,
    REPOSITORY_ROOT,
    check,
    dogfood_project,
    run_cli,
    snapshot_tree,
    starter_project,
)

ENTRY_LINE_PATTERN = re.compile(r"^\s+(new|changed|unchanged|remove)\s+(\S+)\s+(\d+) bytes\s+sha256:([0-9a-f]+)$")


def parse_entries(stdout: str) -> dict[str, tuple[str, int, str]]:
    """{path: (status, size, short hash)} from the lines `plan` and `apply` print."""
    entries = {}
    for line in stdout.splitlines():
        match = ENTRY_LINE_PATTERN.match(line)
        if match:
            entries[match.group(2)] = (match.group(1), int(match.group(3)), match.group(4))
    return entries


def drop_publisher(config: dict[str, Any]) -> None:
    del config["platform"]["publisher"]


def set_bad_version(config: dict[str, Any]) -> None:
    config["version"] = 1


def add_unknown_key(config: dict[str, Any]) -> None:
    config["platform"]["not_a_real_key"] = True


def add_unknown_dependency(config: dict[str, Any]) -> None:
    config["stages"][0]["depends_on"] = ["missing-stage"]


def point_at_missing_skill(config: dict[str, Any]) -> None:
    config["stages"][0]["skill"] = "no-such-skill"


def use_platform_without_renderer(config: dict[str, Any]) -> None:
    config["platform"]["type"] = "gitlab"


def disable_every_stage(config: dict[str, Any]) -> None:
    for stage in config["stages"]:
        stage["enabled"] = False


def disable_security_stage(config: dict[str, Any]) -> None:
    config["stages"][1]["enabled"] = False


def keep_dormant_routing_keys(config: dict[str, Any]) -> None:
    config["routing"]["fast_path"]["globs"] = ["docs/**"]


INVALID_CONFIG_CASES: tuple[tuple[str, Callable[[dict[str, Any]], None], str], ...] = (
    ("no publisher block", drop_publisher, "platform.publisher is not configured"),
    ("unsupported version", set_bad_version, "unsupported config version"),
    ("unknown config key", add_unknown_key, "does not conform to schema"),
    ("unknown dependency (V-S05)", add_unknown_dependency, "missing-stage"),
    ("missing skill file (V-S06)", point_at_missing_skill, "no-such-skill"),
    ("platform without a renderer", use_platform_without_renderer, "platform/type"),
    ("no enabled stage", disable_every_stage, "no enabled stage"),
)


def test_plan_lists_the_files_and_writes_nothing() -> None:
    with dogfood_project() as project_root:
        tree_before = snapshot_tree(project_root)
        exit_code, stdout, stderr = run_cli(["plan", "--root", str(project_root)])
        check(exit_code == 0 and stderr == "", "plan: a valid config exits 0 without errors")
        check(
            tuple(parse_entries(stdout)) == EXPECTED_DOGFOOD_WORKFLOW_PATHS,
            "plan: lists one file per enabled stage, then routing, governance and thread resolution",
        )
        check(snapshot_tree(project_root) == tree_before, "plan: no file is created or changed")
        check(not (project_root / ".github").exists(), "plan: the output directory is not even created")


def test_apply_writes_the_expected_files_as_valid_yaml() -> None:
    with dogfood_project() as project_root:
        exit_code, stdout, stderr = run_cli(["apply", "--root", str(project_root)])
        check(exit_code == 0 and stderr == "", "apply: the dogfood config exits 0")
        for workflow_path in EXPECTED_DOGFOOD_WORKFLOW_PATHS:
            workflow_file = project_root / workflow_path
            check(workflow_file.is_file(), f"apply: writes {workflow_path}")
            parsed_workflow = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
            check(isinstance(parsed_workflow, dict) and "jobs" in parsed_workflow,
                  f"apply: {workflow_path} is valid YAML with jobs")
        check(len(parse_entries(stdout)) == len(EXPECTED_DOGFOOD_WORKFLOW_PATHS), "apply: prints one line per file")


def test_apply_twice_changes_nothing() -> None:
    with dogfood_project() as project_root:
        run_cli(["apply", "--root", str(project_root)])
        tree_after_first_apply = snapshot_tree(project_root)
        exit_code, stdout, _ = run_cli(["apply", "--root", str(project_root)])
        second_statuses = {status for status, _, _ in parse_entries(stdout).values()}
        check(exit_code == 0 and second_statuses == {"unchanged"}, "apply: a second run reports every file unchanged")
        check(snapshot_tree(project_root) == tree_after_first_apply, "apply: a second run leaves sizes and mtimes untouched")


def test_plan_matches_what_apply_writes() -> None:
    with dogfood_project() as project_root:
        _, plan_stdout, _ = run_cli(["plan", "--root", str(project_root)])
        run_cli(["apply", "--root", str(project_root)])
        planned_entries = parse_entries(plan_stdout)
        for workflow_path, (_, planned_size, planned_hash) in planned_entries.items():
            written_bytes = (project_root / workflow_path).read_bytes()
            check(
                len(written_bytes) == planned_size
                and hashlib.sha256(written_bytes).hexdigest().startswith(planned_hash),
                f"plan: size and hash equal the bytes apply wrote to {workflow_path}",
            )
        _, plan_after_apply, _ = run_cli(["plan", "--root", str(project_root)])
        check(
            {status for status, _, _ in parse_entries(plan_after_apply).values()} == {"unchanged"},
            "plan: after apply, every file is unchanged",
        )


def test_plan_and_apply_reject_invalid_configs_identically() -> None:
    for case_name, mutate_config, expected_fragment in INVALID_CONFIG_CASES:
        with dogfood_project(mutate_config) as project_root:
            tree_before = snapshot_tree(project_root)
            plan_result = run_cli(["plan", "--root", str(project_root)])
            apply_result = run_cli(["apply", "--root", str(project_root)])
            check(plan_result[0] == 1 and apply_result[0] == 1, f"invalid ({case_name}): plan and apply exit 1")
            check(plan_result[2] == apply_result[2], f"invalid ({case_name}): plan and apply print the same error")
            check(expected_fragment in plan_result[2], f"invalid ({case_name}): the error says '{expected_fragment}'")
            check(plan_result[1] == "" and apply_result[1] == "", f"invalid ({case_name}): nothing is listed as written")
            check(snapshot_tree(project_root) == tree_before, f"invalid ({case_name}): apply writes no file")


def test_missing_and_unparseable_config_files_are_rejected() -> None:
    with dogfood_project() as project_root:
        (project_root / CONFIG_FILE_PATH).write_text("version: [unclosed", encoding="utf-8")
        for command in ("plan", "apply"):
            exit_code, _, stderr = run_cli([command, "--root", str(project_root)])
            check(exit_code == 1 and stderr.startswith("error: "), f"{command}: invalid YAML exits 1 with an error")
        (project_root / CONFIG_FILE_PATH).unlink()
        for command in ("plan", "apply"):
            exit_code, _, stderr = run_cli([command, "--root", str(project_root)])
            check(exit_code == 1 and "config file not found" in stderr, f"{command}: a missing config exits 1 and says so")


def test_yaml_errors_never_echo_config_text() -> None:
    pasted_secret_yaml_cases = (
        "platform:\n  auth:\n    token_secret: sk-secret-abc123: oops\n",
        "platform:\n  auth:\n    token_secret: !sk-secret-abc123 value\n",
    )
    for yaml_text in pasted_secret_yaml_cases:
        with dogfood_project() as project_root:
            (project_root / CONFIG_FILE_PATH).write_text("version: 2\n" + yaml_text, encoding="utf-8")
            for command in ("plan", "apply"):
                exit_code, stdout, stderr = run_cli([command, "--root", str(project_root)])
                check(exit_code == 1 and "line 4" in stderr, f"{command}: a YAML syntax error exits 1 and names the line")
                check("sk-secret-abc123" not in stdout + stderr, f"{command}: a pasted secret is never echoed from a YAML error")


def test_written_files_have_the_mode_of_an_ordinary_new_file() -> None:
    with dogfood_project() as project_root:
        run_cli(["apply", "--root", str(project_root)])
        ordinary_file = project_root / "ordinary.txt"
        ordinary_file.write_text("x", encoding="utf-8")
        ordinary_mode = stat.S_IMODE(ordinary_file.stat().st_mode)
        for workflow_path in EXPECTED_DOGFOOD_WORKFLOW_PATHS:
            written_mode = stat.S_IMODE((project_root / workflow_path).stat().st_mode)
            check(written_mode == ordinary_mode, f"apply: {workflow_path} has the normal new-file mode, not a private temp-file mode")
        leftovers = [path.name for path in (project_root / ".github" / "workflows").iterdir() if path.name.endswith(".tmp")]
        check(leftovers == [], "apply: no temporary file is left behind")


def test_disabled_stage_gets_no_workflow() -> None:
    with dogfood_project(disable_security_stage) as project_root:
        run_cli(["apply", "--root", str(project_root)])
        workflow_names = sorted(path.name for path in (project_root / ".github" / "workflows").iterdir())
        check(
            workflow_names == ["governance.yml", "resolve-outdated-threads.yml", "routing.yml", "stage-review.yml"],
            "apply: only enabled stages get a stage workflow",
        )


def test_dormant_routing_keys_warn_on_both_commands() -> None:
    with dogfood_project(keep_dormant_routing_keys) as project_root:
        plan_result = run_cli(["plan", "--root", str(project_root)])
        apply_result = run_cli(["apply", "--root", str(project_root)])
        check(plan_result[0] == 0 and apply_result[0] == 0, "V-S11: dormant routing keys do not fail either command")
        check("V-S11" in plan_result[2] and plan_result[2] == apply_result[2], "V-S11: both print the same warning")


def test_changed_file_is_reported_and_rewritten() -> None:
    with dogfood_project() as project_root:
        run_cli(["apply", "--root", str(project_root)])
        routing_file = project_root / ".github" / "workflows" / "routing.yml"
        generated_header = routing_file.read_text(encoding="utf-8").split("\n", 1)[0]
        edited_text = f"{generated_header}\nedited by hand\n"
        routing_file.write_text(edited_text, encoding="utf-8")
        hand_written_file = project_root / ".github" / "workflows" / "hand-written.yml"
        hand_written_file.write_text("name: hand written\n", encoding="utf-8")
        _, plan_stdout, _ = run_cli(["plan", "--root", str(project_root)])
        check(parse_entries(plan_stdout)[".github/workflows/routing.yml"][0] == "changed", "plan: reports a hand-edited file as changed")
        check(routing_file.read_text(encoding="utf-8") == edited_text, "plan: does not rewrite it")
        run_cli(["apply", "--root", str(project_root)])
        check(routing_file.read_text(encoding="utf-8") != edited_text, "apply: rewrites the changed file")
        check(hand_written_file.read_text(encoding="utf-8") == "name: hand written\n", "apply: never touches other workflows")


def test_symlinked_and_directory_targets_are_refused() -> None:
    with dogfood_project() as project_root:
        outside_directory = project_root / "outside"
        outside_directory.mkdir()
        (project_root / ".github").mkdir()
        os.symlink(outside_directory, project_root / ".github" / "workflows")
        for command in ("plan", "apply"):
            exit_code, _, stderr = run_cli([command, "--root", str(project_root)])
            check(exit_code == 1 and "symlink" in stderr, f"{command}: a symlinked workflows directory is refused")
        check(list(outside_directory.iterdir()) == [], "apply: nothing was written through the symlink")
    with dogfood_project() as project_root:
        (project_root / ".github" / "workflows" / "routing.yml").mkdir(parents=True)
        for command in ("plan", "apply"):
            exit_code, _, stderr = run_cli([command, "--root", str(project_root)])
            check(exit_code == 1 and "is a directory" in stderr, f"{command}: a directory at a target path is refused")
        check(not (project_root / ".github" / "workflows" / "stage-review.yml").exists(),
              "apply: a refused target stops the run before any file is written")


def test_the_documented_minimal_config_plans_cleanly() -> None:
    """The starter config in docs/CONFIGURATION.md plans cleanly in an empty repo (shipped skills)."""
    guide_text = (REPOSITORY_ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
    example_start = guide_text.index("# Minimal config")
    minimal_config_text = guide_text[example_start:guide_text.index("```", example_start)]
    with starter_project(minimal_config_text) as project_root:
        exit_code, stdout, stderr = run_cli(["plan", "--root", str(project_root)])
        check(exit_code == 0 and stderr == "", "docs: the documented minimal config plans cleanly")
        check(len(parse_entries(stdout)) == 5, "docs: the standard profile plans two stage workflows, routing, governance and thread resolution")


def test_the_repository_config_plans_cleanly() -> None:
    exit_code, stdout, stderr = run_cli(["plan", "--root", str(REPOSITORY_ROOT)])
    check(exit_code == 0 and stderr == "", "plan: this repository's own .agentic/config.yml plans cleanly")
    check(tuple(parse_entries(stdout)) == EXPECTED_DOGFOOD_WORKFLOW_PATHS, "plan: the repository's plan lists the expected files")


PLAN_APPLY_TESTS = (
    test_plan_lists_the_files_and_writes_nothing,
    test_apply_writes_the_expected_files_as_valid_yaml,
    test_apply_twice_changes_nothing,
    test_plan_matches_what_apply_writes,
    test_plan_and_apply_reject_invalid_configs_identically,
    test_missing_and_unparseable_config_files_are_rejected,
    test_yaml_errors_never_echo_config_text,
    test_written_files_have_the_mode_of_an_ordinary_new_file,
    test_disabled_stage_gets_no_workflow,
    test_dormant_routing_keys_warn_on_both_commands,
    test_changed_file_is_reported_and_rewritten,
    test_symlinked_and_directory_targets_are_refused,
    test_the_documented_minimal_config_plans_cleanly,
    test_the_repository_config_plans_cleanly,
)
