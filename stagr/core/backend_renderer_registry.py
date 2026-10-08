"""BackendRenderer registry.

Stores BackendRenderer instances keyed by (provider, backend). The module exports a single
shared instance (``registry``) for use across a full render pass.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .backend_renderer import BackendRenderer


class BackendRendererNotFoundError(Exception):
    """Raised when no BackendRenderer is registered for a (provider, backend) pair."""


class BackendRendererRegistry:
    """Stores and retrieves BackendRenderer instances by (provider, backend)."""

    def __init__(self) -> None:
        self._renderers: dict[tuple[str, str], "BackendRenderer"] = {}

    def register(self, renderer: "BackendRenderer") -> None:
        """Add a renderer to the registry under its (provider, backend) key.

        Overwrites any previously registered renderer for the same key.
        """
        self._renderers[(renderer.provider, renderer.backend)] = renderer

    def get(self, provider: str, backend: str) -> "BackendRenderer":
        """Return the renderer registered under (provider, backend).

        Raises BackendRendererNotFoundError when no renderer is registered
        for the given pair.
        """
        renderer = self._renderers.get((provider, backend))
        if renderer is None:
            raise BackendRendererNotFoundError(
                f"No BackendRenderer registered for provider={provider!r}, backend={backend!r}"
            )
        return renderer

    def list_renderers(self) -> tuple["BackendRenderer", ...]:
        """Return every registered renderer, in registration order."""
        return tuple(self._renderers.values())

    def has(self, provider: str, backend: str) -> bool:
        """Return True when a renderer is registered for (provider, backend)."""
        return (provider, backend) in self._renderers


registry: BackendRendererRegistry = BackendRendererRegistry()
