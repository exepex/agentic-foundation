"""BackendRenderer Protocol interface.

Defines the contract every backend renderer must satisfy. A BackendRenderer maps a
NormalizedStage to an ExecutionPlan; the mapping must be pure (same input produces
same output, no side effects, no network calls).

Secret alias contract: BackendRenderers declare only SecretRef.alias. They must NOT
set SecretRef.env_name — alias resolution is a separate pipeline phase (see issue #193).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .enums import StageGate, StageKind
from .models import ExecutionPlan, NormalizedStage


@runtime_checkable
class BackendRenderer(Protocol):
    """Maps a NormalizedStage to an ExecutionPlan.

    Implementations must be pure: identical inputs produce identical outputs with no
    side effects and no network calls. `provider` and `backend` identify the renderer
    so the pipeline can select the correct implementation at render time.
    """

    provider: str
    backend: str
    # The stage kinds and gates this backend can render; the `stagr init` template lists them.
    supported_stage_kinds: frozenset[StageKind]
    supported_gates: frozenset[StageGate]

    def render(self, stage: NormalizedStage) -> ExecutionPlan:
        """Produce an ExecutionPlan for the given stage.

        The returned plan must have `gate_disposition` set and `required_secrets`
        containing only SecretRef instances whose `env_name` is None (alias-only).
        """
        ...
