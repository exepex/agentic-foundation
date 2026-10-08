# Changelog

What each tagged Stagr release contains. Stagr is pre-release (see `AGENTS.md`, "Pre-release: zero
consumers"): any release may change the config contract or the commands. Install a release by its tag,
as described in [docs/CLI.md](docs/CLI.md).

## 0.2.0 — 2026-10-08

The first tagged release. It onboards a GitHub repository and generates a governed AI review pipeline
from one config file.

### Commands ([docs/CLI.md](docs/CLI.md))

- `stagr init` writes the starter `.agentic/config.yml`. It asks for the publisher App ID or takes
  `--app-id`, and offers the `minimal` and `standard` profiles.
- `stagr plan` validates the config and lists the files it would write, without writing anything.
- `stagr apply` writes exactly those files. Running it again changes nothing.
- `stagr help` and `stagr help <command>` describe the commands.

### Config contract ([docs/CONFIGURATION.md](docs/CONFIGURATION.md))

- Config `version: 2`, checked against `stagr/config.schema.json`.
- Profiles `minimal`, `standard` and `custom`. Stages are merged on top of the profile.
- Per-stage provider, model, skill, triggers, gate and dependencies.
- Optional fast-path routing.
- Secrets are referenced by name only. A value pasted where a secret name belongs is rejected and never
  printed.

### Validation ([design-docs/07-validation.md](design-docs/07-validation.md))

- The static checks listed as implemented there run in `init`, `plan` and `apply`. They cover schema
  validity, the stage graph (cycles and unknown or disabled dependencies), skill resolution, backend
  and platform compatibility, and route closure.

### What gets generated ([docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), section 8)

- **Platform:** GitHub Actions.
- **Stage backend:** OpenAI Codex, for the `review` and `security` stages. Each stage asks Codex for its
  review and publishes a result bound to the head commit, as a Check Run of the Stagr publisher GitHub App.
- **Routing workflow:** classifies each pull request for the fast path.
- **Governance workflow:** a fail-closed merge gate that passes only when every blocking stage passed on
  the current commit.
- **Workflow hardening:** every external action is pinned to a commit SHA, each workflow requests minimal
  permissions, and fork pull requests are refused by default.

### Shipped skills

- `code-review` and `security-review`, under `stagr/templates/skills/`.

### Not in this release

The roadmap is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), section 8. Not included:

- `stagr doctor`
- build, test and custom stage rendering
- platforms other than GitHub
- backends other than Codex
