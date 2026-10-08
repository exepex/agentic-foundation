"""The pipeline `stagr plan` and `stagr apply` share: load, validate, render.

``load_render_inputs`` reads ``<project_root>/.agentic/config.yml`` and runs every static check
(V-S01 to V-S09, plus the V-S11 warning); it returns everything the renderers need.
``render_artifacts`` turns those inputs into the files the config produces. Neither function writes
anything; ``plan_apply.run_pipeline`` adds the stale generated files to remove, so ``plan`` lists
the changes and ``apply`` makes the same changes: the two commands cannot differ.

This module sits above ``stagr.core`` and ``stagr.platforms`` because it is the one place that
wires the neutral core to a platform renderer.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stagr.core.backend_renderer_registry import BackendRendererRegistry
from stagr.core.config_parser import parse_config
from stagr.core.config_validation import validate_config
from stagr.core.models import ConfigError, RenderContext, RenderedArtifact, StaticValidationError
from stagr.core.pipeline import normalize_config
from stagr.core.policy import derive_merge_policy, derive_routing_policy, derive_trust_policy
from stagr.core.publisher import PublisherConfig, derive_publisher_config
from stagr.core.render_loop import resolve_platform_token_secret, run_phase1
from stagr.core.renderers.openai_codex_backend_renderer import OpenAICodexBackendRenderer
from stagr.core.static_validator import (
    validate_backend_renderer_availability,
    validate_platform_invocation_compatibility,
    validate_route_dependency_closure,
)
from stagr.platforms.github.renderer import GitHubPlatformRenderer

CONFIG_RELATIVE_PATH = Path(".agentic") / "config.yml"

# One entry per supported platform: a new platform renderer adds a line here and its value to
# the schema's platform.type enum.
PLATFORM_RENDERER_CLASSES = {"github": GitHubPlatformRenderer}


@dataclass(frozen=True)
class RenderInputs:
    """A config that passed every static check, ready to render.

    ``raw_config`` is the parsed YAML (Phase 1 reads provider secret names and the platform token
    name from it). ``warnings`` are static findings that do not stop the run (V-S11).
    """

    raw_config: dict[str, Any]
    render_context: RenderContext
    publisher: PublisherConfig
    warnings: tuple[str, ...]


def build_backend_registry() -> BackendRendererRegistry:
    """Return a registry holding every backend renderer this toolkit ships."""
    backend_registry = BackendRendererRegistry()
    backend_registry.register(OpenAICodexBackendRenderer())
    return backend_registry


def find_dormant_routing_warnings(raw_config: dict[str, Any]) -> tuple[str, ...]:
    """V-S11: fast-path is disabled but routing keys are present. A warning, never an error."""
    fast_path_config = (raw_config.get("routing") or {}).get("fast_path") or {}
    is_disabled = fast_path_config.get("enabled", True) is False
    has_routing_keys = "globs" in fast_path_config or "stages" in fast_path_config
    warnings: list[str] = []
    if is_disabled and has_routing_keys:
        warnings.append(
            "V-S11: dormant route configuration: routing.fast_path is disabled but 'globs' or "
            "'stages' are present; they will not be evaluated"
        )
    return tuple(warnings)


def load_render_inputs(project_root: Path) -> RenderInputs:
    """Read ``<project_root>/.agentic/config.yml`` and run every static check.

    Raises:
        ConfigError: the config file is missing, the publisher block is missing or invalid, or a
            stage's provider or backend cannot be resolved.
        ConfigSyntaxError, ConfigVersionError, ConfigSchemaError, StaticValidationError,
            ValueError: from the front door (YAML syntax, V-S01 to V-S06) and the renderer checks
            (V-S07 to V-S09).
    """
    config_path = project_root / CONFIG_RELATIVE_PATH
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")
    raw_config = parse_config(config_path)
    validate_config(raw_config, project_root)

    publisher = derive_publisher_config(raw_config)
    platform_type = (raw_config.get("platform") or {}).get("type", "github")
    platform_renderer_class = PLATFORM_RENDERER_CLASSES[platform_type]
    normalized_stages = normalize_config(raw_config)
    if not normalized_stages:
        raise StaticValidationError("the config has no enabled stage; there is nothing to render")

    backend_registry = build_backend_registry()
    validate_backend_renderer_availability(normalized_stages, backend_registry)
    validate_platform_invocation_compatibility(
        normalized_stages, backend_registry, platform_renderer_class.SUPPORTED_INVOCATION_KINDS
    )
    routing_policy = derive_routing_policy(raw_config)
    validate_route_dependency_closure(routing_policy, normalized_stages)

    trust_policy = derive_trust_policy(raw_config)
    render_context = RenderContext(
        stages=normalized_stages,
        routing_policy=routing_policy,
        merge_policy=derive_merge_policy(raw_config, normalized_stages, trust_policy),
        trust_policy=trust_policy,
        platform=platform_type,
    )
    return RenderInputs(
        raw_config=raw_config,
        render_context=render_context,
        publisher=publisher,
        warnings=find_dormant_routing_warnings(raw_config),
    )


def render_artifacts(render_inputs: RenderInputs) -> tuple[RenderedArtifact, ...]:
    """Run Phase 1 (per stage), then Phase 2 (routing, governance, thread resolution).

    The order is stable: stage workflows in dependency order, then routing, then governance, then
    the outdated-thread resolution workflow when a stage reads review threads.
    """
    render_context = render_inputs.render_context
    platform_renderer_class = PLATFORM_RENDERER_CLASSES[render_context.platform]
    platform_renderer = platform_renderer_class(
        render_inputs.publisher.app_id, render_inputs.publisher.private_key_secret
    )

    stage_renders = run_phase1(
        render_context, build_backend_registry(), platform_renderer, render_inputs.raw_config
    )
    result_specs = tuple(stage_render.result_spec for stage_render in stage_renders)
    thread_resolution_artifact = platform_renderer.render_thread_resolution(
        result_specs, render_context, resolve_platform_token_secret(render_inputs.raw_config)
    )
    artifacts = (
        *(stage_render.artifact for stage_render in stage_renders),
        platform_renderer.render_routing(render_context),
        platform_renderer.render_governance(result_specs, render_context),
        *((thread_resolution_artifact,) if thread_resolution_artifact else ()),
    )
    _assert_artifact_paths_are_unique(artifacts)
    return artifacts


def _assert_artifact_paths_are_unique(artifacts: tuple[RenderedArtifact, ...]) -> None:
    seen_paths: set[str] = set()
    for artifact in artifacts:
        if artifact.path in seen_paths:
            raise ConfigError(f"two artifacts would be written to the same path: {artifact.path}")
        seen_paths.add(artifact.path)
