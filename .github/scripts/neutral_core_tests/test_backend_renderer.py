"""Tests for the BackendRenderer Protocol interface (issue #188).

Covers: Protocol conformance, secret alias contract, gate_disposition requirement,
and absence of platform-specific fields in ExecutionPlan.
"""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from stagr.core.enums import StageGate, StageKind

if TYPE_CHECKING:
    from stagr.core.models import ExecutionPlan, NormalizedStage


# ---------------------------------------------------------------------------
# Shared stub used across tests
# ---------------------------------------------------------------------------

def _build_minimal_execution_plan():
    """Return a valid ExecutionPlan with alias-only required_secrets and gate_disposition set."""
    from stagr.core.models import (
        ExecutionPlan,
        GateDispositionSpec,
        Invocation,
        SecretRef,
    )
    from stagr.core.enums import GateDispositionKind, InvocationKind

    invocation = Invocation(kind=InvocationKind.CI_COMPONENT)
    gate_disposition = GateDispositionSpec(
        kind=GateDispositionKind.ALWAYS_PASS,
        selector="always",
    )
    secret = SecretRef(alias="PROVIDER_API_KEY")
    return ExecutionPlan(
        stage_id="test-stage",
        invocation=invocation,
        gate_disposition=gate_disposition,
        required_secrets=(secret,),
    )


def _build_minimal_normalized_stage():
    """Return a valid NormalizedStage for use in stub render() calls."""
    from stagr.core.models import NormalizedStage
    from stagr.core.enums import StageKind, StageGate, StageTrigger

    return NormalizedStage(
        id="test-stage",
        kind=StageKind.REVIEW,
        provider="anthropic",
        backend="claude",
        skill=None,
        gate=StageGate.BLOCKING,
        triggers=(StageTrigger.PR_OPENED,),
        dependencies=(),
    )


class _StubBackendRenderer:
    """Minimal BackendRenderer implementation for conformance testing.

    Satisfies the Protocol with the smallest possible boilerplate: the identity
    attributes, the supported stage kinds and a render() method that returns a
    fixed ExecutionPlan.
    """

    provider: str = "anthropic"
    backend: str = "claude"
    supported_stage_kinds = frozenset({StageKind.REVIEW})
    supported_gates = frozenset({StageGate.BLOCKING})

    def render(self, stage: "NormalizedStage") -> "ExecutionPlan":
        return _build_minimal_execution_plan()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_backend_renderer_protocol_conformance() -> None:
    """A minimal stub satisfies the BackendRenderer Protocol (runtime isinstance check)."""
    from stagr.core.backend_renderer import BackendRenderer

    stub = _StubBackendRenderer()

    assert isinstance(stub, BackendRenderer), (
        f"_StubBackendRenderer must be recognised as a BackendRenderer by isinstance(); "
        f"got type {type(stub)}"
    )


def test_backend_renderer_secret_alias_only() -> None:
    """render() result has required_secrets with alias set and env_name not set (None)."""
    stub = _StubBackendRenderer()
    stage = _build_minimal_normalized_stage()

    execution_plan = stub.render(stage)

    for secret_ref in execution_plan.required_secrets:
        assert secret_ref.alias, (
            f"SecretRef.alias must be set, got alias={secret_ref.alias!r}"
        )
        assert secret_ref.env_name is None, (
            f"BackendRenderer must not set SecretRef.env_name; "
            f"got env_name={secret_ref.env_name!r} for alias={secret_ref.alias!r}"
        )


def test_backend_renderer_gate_disposition_set() -> None:
    """render() result has gate_disposition set (not None)."""
    stub = _StubBackendRenderer()
    stage = _build_minimal_normalized_stage()

    execution_plan = stub.render(stage)

    assert execution_plan.gate_disposition is not None, (
        f"ExecutionPlan.gate_disposition must be set; got None for stage "
        f"'{execution_plan.stage_id}'"
    )


def test_backend_renderer_no_platform_fields() -> None:
    """ExecutionPlan returned by render() contains no platform-specific fields.

    The set of allowed fields is defined by the neutral-core contract in models.py.
    Any field outside that set is a platform-specific leak.
    """
    stub = _StubBackendRenderer()
    stage = _build_minimal_normalized_stage()

    execution_plan = stub.render(stage)

    allowed_field_names = {
        data_field.name for data_field in dataclasses.fields(execution_plan)
    }
    expected_field_names = {"stage_id", "invocation", "gate_disposition", "required_secrets", "evidence"}

    unexpected_field_names = allowed_field_names - expected_field_names
    assert not unexpected_field_names, (
        f"ExecutionPlan contains unexpected (potentially platform-specific) fields: "
        f"{unexpected_field_names}. Allowed fields: {expected_field_names}"
    )
