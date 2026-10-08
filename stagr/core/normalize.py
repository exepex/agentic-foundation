"""Pre-normalization pipeline for stage config entries.

This module implements the first two steps of the normalization pipeline:

1. **Profile expansion** (``expand_profile_defaults``): merges profile-defined
   stage defaults with the operator's explicit stage list.
2. **Disabled-stage removal** (``filter_disabled_stages``): removes stages with
   ``enabled: false`` after profile expansion and operator override merging.

Both functions run before backend/model resolution, dependency graph construction,
policy derivation, and ``NormalizedStage[]`` production.

**Vocabulary layer:** Both functions operate in the raw M1 config vocabulary as
validated by ``stagr/config.schema.json``.  Key raw-vocab field names:

- Dependencies: ``depends_on`` (a list of stage ids; translated to canonical
  ``dependencies`` in a later normalization step).
- Gate values: ``"blocking"`` / ``"advisory"`` (raw schema literals; translated
  to ``StageGate.BLOCKING`` / ``StageGate.NON_BLOCKING`` later).

Consumers downstream (``#181``–``#183``) are responsible for translating raw
field names to canonical neutral-core vocabulary before producing
``NormalizedStage[]`` objects.

Design source: design-docs/02-canonical-stage-model.md
"""
from __future__ import annotations

import copy
from typing import Any


def filter_disabled_stages(merged_stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only the enabled stages from a post-expansion stage list.

    A stage is included when its ``enabled`` field is absent (defaults to
    ``True``) or explicitly set to a truthy value.  A stage with
    ``enabled: false`` is excluded entirely — it must never appear in the
    normalization pipeline, in ``NormalizedStage[]``, in ``blockingStageIds``,
    or as a valid dependency target.

    The function is pure: it does not mutate the input list or any of its
    entries.  It returns a new list containing references to the original dicts
    (shallow copies are unnecessary because the dicts themselves are not
    modified).

    This step runs after profile expansion and operator override merging, but
    before backend/model resolution, dependency graph construction, policy
    derivation, and ``NormalizedStage[]`` production.  See the pipeline order in
    design-docs/02-canonical-stage-model.md.

    Args:
        merged_stages: Stage dicts after profile expansion and operator override
            merge.  Each entry must be a dict; callers are responsible for
            validating that invariant before calling here.

    Returns:
        A new list containing only those dicts whose ``enabled`` field is
        absent or truthy.
    """
    return [stage for stage in merged_stages if stage.get("enabled", True)]


# ---------------------------------------------------------------------------
# Profile stage defaults — declarative data, not code logic.
#
# Each profile maps to a list of stage dicts supplying ALL default field values
# for that profile's stages.  The operator's own stage fields always override
# profile defaults (operator wins).  ``custom`` is the identity profile.
#
# Field values use the M1 config vocabulary (lower-case string literals).
# V1 built-in profiles: minimal, standard, custom.
# ---------------------------------------------------------------------------
# The profile a config gets when it omits ``profile``; matches the schema default.
DEFAULT_PROFILE_NAME = "standard"

_PROFILE_STAGE_DEFAULTS: dict[str, list[dict[str, Any]]] = {
    "minimal": [
        {
            "id": "review",
            "type": "review",
            "provider": "openai",
            "skill": "code-review",
            "gate": "blocking",
            "triggers": ["pr_opened", "pr_updated"],
            "depends_on": [],
        },
    ],
    "standard": [
        {
            "id": "review",
            "type": "review",
            "provider": "openai",
            "skill": "code-review",
            "gate": "blocking",
            "triggers": ["pr_opened", "pr_updated"],
            "depends_on": [],
        },
        {
            "id": "security",
            "type": "security",
            "provider": "openai",
            "skill": "security-review",
            "gate": "blocking",
            "triggers": ["pr_opened", "pr_updated"],
            "depends_on": ["review"],
        },
    ],
    "custom": [],
}

PROFILE_NAMES: tuple[str, ...] = tuple(_PROFILE_STAGE_DEFAULTS)


def describe_profile(profile_name: str) -> str:
    """Return a one-line summary of the stages ``profile_name`` expands to, built from its data.

    Example: ``"stages: review, security (after review)"``. Because the text is derived from
    ``_PROFILE_STAGE_DEFAULTS``, it cannot drift from what the profile really does.
    """
    profile_stages = _PROFILE_STAGE_DEFAULTS[profile_name]
    if not profile_stages:
        return "no built-in stages; you list every stage under `stages`"
    stage_descriptions = [
        stage["id"] + (f" (after {', '.join(stage['depends_on'])})" if stage["depends_on"] else "")
        for stage in profile_stages
    ]
    return "stages: " + ", ".join(stage_descriptions)


def list_built_in_stages() -> list[tuple[dict[str, Any], tuple[str, ...]]]:
    """Return every built-in stage definition once, with the profiles that turn it on.

    Built from ``_PROFILE_STAGE_DEFAULTS``, so the stage catalog ``stagr init`` writes cannot drift
    from what the profiles really expand to.
    """
    stages_by_id: dict[str, dict[str, Any]] = {}
    profiles_by_id: dict[str, list[str]] = {}
    for profile_name, profile_stages in _PROFILE_STAGE_DEFAULTS.items():
        for stage in profile_stages:
            stages_by_id.setdefault(stage["id"], stage)
            profiles_by_id.setdefault(stage["id"], []).append(profile_name)
    return [(stages_by_id[stage_id], tuple(profiles_by_id[stage_id])) for stage_id in stages_by_id]


def expand_profile_defaults(
    profile_name: str,
    explicit_stages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge profile stage defaults with the operator's explicitly declared stages.

    Implements the profile expansion step of the normalization pipeline
    (design-docs/02-canonical-stage-model.md, "Profile expansion").  The
    returned list is the input to ``filter_disabled_stages`` and subsequently
    backend/model default resolution and further normalization steps.

    Expansion rules:

    - ``custom`` is the identity profile.  ``explicit_stages`` are returned
      as new dicts with no injection — operators must declare every required
      field themselves.
    - For all other built-in profiles, the profile supplies a predefined set
      of stage defaults including ``skill``, ``gate``, ``triggers``, and
      ``depends_on`` (raw M1 vocabulary; see module docstring).  Each profile
      stage is included in the output.  When
      the operator declares a stage with the same ``id`` as a profile stage,
      the operator's fields are merged *on top of* the profile defaults (the
      operator always wins).  Operator stages whose ``id`` is absent from the
      profile are appended in declaration order.
    - An unrecognised profile name raises ``ValueError`` with error code
      ``V-S01`` (static validation error, caught before any stage processing).
    - A stage entry missing an ``"id"`` key raises ``ValueError``.
    - Duplicate ``id`` values within ``explicit_stages`` raise ``ValueError``.

    This function is pure: ``explicit_stages`` and its entries are never
    mutated.  Applying the function twice with the same arguments produces the
    same result (idempotent).

    Args:
        profile_name: The value of the ``profile`` key from the M1 config.
            When the config omits ``profile``, the pipeline passes
            ``DEFAULT_PROFILE_NAME`` (``"standard"``, the schema default).
        explicit_stages: The stage list from the M1 config.  Each entry must
            be a dict containing at minimum an ``"id"`` key; duplicate ids
            are a validation error.

    Returns:
        A new list of new dicts with profile defaults applied.  The caller
        must not rely on identity equality with entries from ``explicit_stages``.

    Raises:
        ValueError: When ``profile_name`` is not a recognised built-in profile
            (V-S01), when a stage entry is missing an ``"id"`` key, or when
            ``explicit_stages`` contains duplicate stage ids.
    """
    if profile_name not in _PROFILE_STAGE_DEFAULTS:
        valid_profile_names = sorted(_PROFILE_STAGE_DEFAULTS)
        raise ValueError(
            f"V-S01: unrecognised profile '{profile_name}' — "
            f"valid profiles are: {valid_profile_names}"
        )

    validated_explicit_stages = _validate_and_copy_explicit_stages(explicit_stages)

    if profile_name == "custom":
        return validated_explicit_stages

    profile_stage_list = _PROFILE_STAGE_DEFAULTS[profile_name]

    stage_map: dict[str, dict[str, Any]] = {
        stage_def["id"]: copy.deepcopy(stage_def) for stage_def in profile_stage_list
    }
    output_ordering: list[str] = [stage_def["id"] for stage_def in profile_stage_list]

    for stage in validated_explicit_stages:
        stage_id = stage["id"]
        if stage_id in stage_map:
            stage_map[stage_id] = {**stage_map[stage_id], **stage}
        else:
            stage_map[stage_id] = stage
            output_ordering.append(stage_id)

    return [stage_map[stage_id] for stage_id in output_ordering]


def _validate_and_copy_explicit_stages(
    explicit_stages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return shallow copies of explicit_stages after validating id presence and uniqueness."""
    seen_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, stage in enumerate(explicit_stages):
        try:
            stage_id = stage["id"]
        except KeyError as error:
            raise ValueError(
                f"stage at index {index} is missing required 'id' field"
            ) from error
        if stage_id in seen_ids:
            raise ValueError(
                f"duplicate stage id '{stage_id}' in explicit_stages — "
                "each stage must have a unique id"
            )
        seen_ids.add(stage_id)
        result.append(copy.deepcopy(stage))
    return result
