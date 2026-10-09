#!/usr/bin/env python3
"""Deterministic validation for the agentic-foundation contract.

This is the repository's "green" check (the equivalent of a unit-test suite for a
contract/docs repo). It runs in CI (`validate.yml`). It has no network access and only reads
repository files.

Checks:
  1. stagr/config.schema.json is valid JSON Schema (2020-12).
  2. The repo's own .agentic/config.yml (dogfood) passes the neutral core's front door
     (`stagr.core.config_validation.validate_config`): schema, publisher block, profile,
     dependency references and cycles, provider resolution, and skill files.
  3. Representative minimal and publisher configs validate; a pasted key is rejected.
  4. Every skill (stagr/templates/skills/*/SKILL.md) has parseable YAML frontmatter with
     the required keys and a `verdict:` line inside a fenced code block.

Exit code 0 = all pass; non-zero = at least one failure (details on stderr).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SKILL_REQUIRED_KEYS = {"id", "name", "stage_type", "version"}
errors: list[str] = []

# Reuse the toolkit's OWN front door rather than reimplementing its checks here, so CI exercises
# the same validation `stagr plan` and `stagr apply` run.
sys.path.insert(0, str(ROOT))
from stagr.core.config_validation import describe_schema_error, validate_config  # noqa: E402
from stagr.core.models import ConfigError  # noqa: E402


def fail(msg: str) -> None:
    errors.append(msg)


def load_yaml(path: Path):
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def check_skill(skill_md: Path) -> None:
    rel = skill_md.relative_to(ROOT).as_posix()
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        fail(f"{rel}: missing YAML frontmatter")
        return
    parts = text.split("---", 2)
    if len(parts) < 3:
        fail(f"{rel}: unterminated YAML frontmatter")
        return
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        fail(f"{rel}: frontmatter is not valid YAML: {exc}")
        return
    if not isinstance(meta, dict):
        fail(f"{rel}: frontmatter must be a mapping")
        return
    missing = SKILL_REQUIRED_KEYS - set(meta)
    if missing:
        fail(f"{rel}: frontmatter missing required key(s): {', '.join(sorted(missing))}")
    # `verdict:` must live inside a fenced code block (the structured output contract),
    # not merely be mentioned in prose.
    in_fence = False
    fence_marker = ""
    verdict_in_fence = False
    for line in parts[2].splitlines():
        stripped = line.lstrip()
        # Markdown allows both ``` and ~~~ fences; a fence closes only on its own marker.
        if not in_fence and (stripped.startswith("```") or stripped.startswith("~~~")):
            in_fence, fence_marker = True, stripped[0]
            continue
        if in_fence and stripped.startswith(fence_marker * 3):
            in_fence, fence_marker = False, ""
            continue
        if in_fence and re.match(r"\s*verdict:", line):
            verdict_in_fence = True
            break
    if not verdict_in_fence:
        fail(f"{rel}: no `verdict:` line inside a fenced output block")
    if not errors or errors[-1].split(":")[0] != rel:
        print(f"OK  skill {rel}")


def main() -> int:
    schema_path = ROOT / "stagr" / "config.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        print(f"OK  schema is valid JSON Schema: {schema_path.relative_to(ROOT)}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL schema invalid: {exc}", file=sys.stderr)
        return 1  # nothing else can be checked without a schema

    validator = Draft202012Validator(schema)

    def validate(obj, label: str) -> None:
        schema_errors = sorted(validator.iter_errors(obj), key=lambda error: list(error.path))
        if schema_errors:
            for error in schema_errors:
                location = "/".join(str(part) for part in error.path) or "(root)"
                fail(f"{label}: {location}: {describe_schema_error(error)}")
        else:
            print(f"OK  {label} validates against schema")

    # 2. The repo's own config must pass the neutral core's front door.
    dogfood_path = ROOT / ".agentic" / "config.yml"
    dogfood_label = dogfood_path.relative_to(ROOT).as_posix()
    if not dogfood_path.exists():
        fail(f"{dogfood_label}: expected file is missing")
    else:
        try:
            dogfood_config = load_yaml(dogfood_path)
        except Exception as exc:  # noqa: BLE001
            fail(f"{dogfood_label}: cannot parse: {exc}")
        else:
            validate(dogfood_config, dogfood_label)
            try:
                validate_config(dogfood_config, project_root=ROOT)
                print(f"OK  {dogfood_label} passes the front door (schema, stage graph, providers, skill files)")
            except (ValueError, ConfigError) as exc:
                fail(f"{dogfood_label}: front-door validation failed: {exc}")

    # 3. Minimal config.
    validate(
        {
            "version": 2,
            "profile": "standard",
            "platform": {"type": "github"},
            "defaults": {"provider": "anthropic", "models": {"anthropic": {"default": "c"}}},
        },
        "minimal config",
    )

    # 3b. publisher and secrets example: a valid block validates; a credential-looking secret is rejected.
    publisher_example = {
        "version": 2,
        "profile": "standard",
        "platform": {"type": "github", "publisher": {"app_id": 123456}},
        "secrets": {"app_private_key": "STAGR_APP_PRIVATE_KEY"},
        "defaults": {"provider": "anthropic", "models": {"anthropic": {"default": "c"}}},
    }
    validate(publisher_example, "publisher example config")
    literal_key_example = json.loads(json.dumps(publisher_example))
    literal_key_example["secrets"]["app_private_key"] = "-----BEGIN RSA PRIVATE KEY-----"
    if validator.is_valid(literal_key_example):
        fail("secrets.app_private_key accepted a literal key value; it must be a secret NAME")
    else:
        print("OK  secrets.app_private_key rejects a literal key value")

    # 4. Skills.
    for skill_md in sorted((ROOT / "stagr" / "templates" / "skills").glob("*/SKILL.md")):
        check_skill(skill_md)

    if errors:
        print(f"\n{len(errors)} validation error(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("\nAll contract validations passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
