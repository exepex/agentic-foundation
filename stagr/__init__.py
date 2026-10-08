"""stagr — the agentic-foundation control plane CLI.

stagr turns one small declarative file (`.agentic/config.yml`) into a governed,
multi-stage agentic SDLC pipeline on a repository's existing CI/SCM. It declares,
initializes, and governs the pipeline — it never executes the agents itself.

The public entry point is the `stagr` command (see `stagr.cli:main`).
"""

__version__ = "0.5.4"
