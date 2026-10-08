"""Pinned third-party GitHub Actions used by every generated workflow.

Each action is pinned to a full commit SHA, never a mutable tag, per the supply-chain rule in
AGENTS.md ("Threat model"). This module is the one place a pin is defined; update it here after
auditing the new release.
"""

# actions/create-github-app-token v1.11.1 (refs/tags/v1.11.1).
APP_TOKEN_ACTION_REF = (
    "actions/create-github-app-token@c1a285145b9d317df6ced56c09f525b5c2b6f755"
    "  # v1.11.1"
)

CHECKOUT_ACTION_REF = "actions/checkout@08c6903cd8c0fde910a37f88322edcfb5dd907a8  # v5.0.0"

CLAUDE_CODE_ACTION_REF = "anthropics/claude-code-action@38c80c1a32cdbc0c6f34042464a0669d89363dde  # v1.0.246"
