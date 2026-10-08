"""The commented `.agentic/config.yml` template that `stagr init` writes.

The template shows every option that changes what Stagr generates. The required keys and the
chosen profile are active; every optional block is commented out under a short explanation of what
it does and how it helps, so the team reads the whole configuration before running `stagr plan` and
turns on only what it wants. Profile and stage text is built from the profile definitions, so it
cannot drift from what a profile expands to. ``optional_blocks_enabled`` renders the optional
blocks uncommented; tests use it to prove that uncommenting everything still validates.
"""
from __future__ import annotations

from typing import Any

import yaml

from stagr.core.normalize import expand_profile_defaults, list_built_in_stages

from .profile_command import build_profile_guide

# The guide every comment points to. Named, not linked: the template must not publish where the
# Stagr source lives, and a relative path would point into the user's own repository.
CONFIGURATION_GUIDE = "the Stagr configuration guide (CONFIGURATION.md)"


def build_config_template(profile: str, app_id: str, optional_blocks_enabled: bool = False) -> str:
    """Return the full config text for ``profile`` and the publisher App ``app_id``."""
    setting_prefix = "" if optional_blocks_enabled else "# "
    sections = [
        _build_header(),
        "version: 2\n",
        f"{build_profile_guide()}profile: {profile}\n",
        _build_platform_section(app_id, setting_prefix),
        _build_providers_section(setting_prefix),
        _build_stages_section(setting_prefix),
        _build_routing_section(profile, setting_prefix),
        _build_remediation_section(setting_prefix),
    ]
    return "\n".join(sections)


def _build_header() -> str:
    return (
        "# Stagr pipeline for this repository: what runs on every pull request and what must pass\n"
        "# before it can merge. Edit this file, then run `stagr plan` to preview the workflow files\n"
        "# and `stagr apply` to write them. Each setting is explained, under its name, in\n"
        f"# {CONFIGURATION_GUIDE}.\n"
        "# Blocks starting with `#` are off: uncomment one (remove the `# ` before each line) to use it.\n"
    )


def _comment(lines: list[str], indent: str = "") -> str:
    return "".join(f"{indent}# {line}\n" if line else f"{indent}#\n" for line in lines)


def _settings(lines: list[str], setting_prefix: str, indent: str = "") -> str:
    return "".join(f"{indent}{setting_prefix}{line}\n" for line in lines)


def _build_platform_section(app_id: str, setting_prefix: str) -> str:
    return (
        "# Where the pipeline runs and who may drive it.\n"
        "platform:\n"
        "  type: github\n"
        "  # The Stagr GitHub App that publishes each stage's result and the merge verdict.\n"
        "  publisher:\n"
        f"    app_id: {app_id}\n"
        + _comment(["Name of the secret holding the App's private key."], "    ")
        + _settings(["private_key_secret: STAGR_APP_PRIVATE_KEY"], setting_prefix, "    ")
        + _comment(
            [
                "Pull requests from forks never start a stage (true), so outside code never runs",
                "with this repository's secrets.",
            ],
            "  ",
        )
        + _settings(["same_repo_only: true"], setting_prefix, "  ")
        + _comment(["Pull request authors whose changes Stagr reviews and fixes automatically."], "  ")
        + _settings(["trusted_roles: [owner, member, collaborator]"], setting_prefix, "  ")
        + _comment(
            [
                "Name of the secret holding a real user's token. Stagr posts the review requests",
                "(and resolves finished review threads) as that user.",
            ],
            "  ",
        )
        + _settings(["auth:", "  token_secret: REMEDIATION_TOKEN"], setting_prefix, "  ")
        + _comment(
            ["Label that hands a pull request to a human when the automated fix rounds run out."], "  "
        )
        + _settings(["labels:", "  human_merge: human-merge"], setting_prefix, "  ")
    )


def _build_providers_section(setting_prefix: str) -> str:
    return _comment(
        [
            "Secret names per provider, when a provider must use a different secret than the",
            "platform token above.",
        ]
    ) + _settings(
        ["providers:", "  openai:", "    secrets:", "      TRUSTED_COMMENTER_TOKEN: REMEDIATION_TOKEN"],
        setting_prefix,
    )


def _build_stages_section(setting_prefix: str) -> str:
    built_in_stages = list_built_in_stages()
    catalog_lines = [
        "Stages: the agents that review every pull request. The profile above turns stages on:",
    ]
    for stage, profile_names in built_in_stages:
        catalog_lines.append(f"  {stage['id']:<9} on in: {', '.join(profile_names)}")
    catalog_lines += [
        "Your profile's stages are already on and required (validation V-S16). Uncomment a stage",
        "below to add one your profile lacks or to change a stage's settings; see `stages`.",
    ]
    stage_lines = ["stages:"]
    for stage, _ in built_in_stages:
        stage_lines += _render_stage(stage)
    return _comment(catalog_lines) + _settings(stage_lines, setting_prefix)


def _render_stage(stage: dict[str, Any]) -> list[str]:
    rendered = yaml.safe_dump([stage], sort_keys=False, default_flow_style=None).splitlines()
    return [f"  {line}" for line in rendered]


def _build_routing_section(profile: str, setting_prefix: str) -> str:
    profile_stage_ids = [stage["id"] for stage in expand_profile_defaults(profile, [])]
    return _comment(
        [
            "Fast path: a pull request that changes only files matching `globs` (for example",
            "documentation) runs only the `fast` stages, so small changes get a faster, cheaper",
            "review; every other pull request runs the `normal` stages. See `routing.fast_path`;",
            "after switching profile, update both lists.",
        ]
    ) + _settings(
        [
            "routing:",
            "  fast_path:",
            "    enabled: true",
            '    globs: ["docs/**", "**/*.md"]',
            "    stages:",
            "      fast: [review]",
            f"      normal: [{', '.join(profile_stage_ids)}]",
        ],
        setting_prefix,
    )


def _build_remediation_section(setting_prefix: str) -> str:
    return _comment(
        [
            "Automated fixes: an agent answers each review, fixing the findings it judges real and",
            "declining the rest with its reasons, so reviews turn into fixes without waiting on a",
            "person. Optional under every profile; setup and limits: see `remediation`.",
        ]
    ) + _settings(
        ["remediation:", "  provider: anthropic", "  max_rounds: 5", "  api_key_secret: ANTHROPIC_API_KEY"],
        setting_prefix,
    )
