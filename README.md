# agentic-foundation

A reusable toolkit that drops a configurable **graph of SDLC/STLC agent stages** — review,
security, build, test, and more — into *any* repository, on *any* SCM platform, in *any* language,
with *any* provider/model per stage. It is the generic engineering core extracted from the
`permission-api` project, with everything product-specific (Azure deploy, the runtime app, the
Permission-API domain) removed.

After a small, one-file configuration step, a target repository gets an agentic pipeline: each stage
is an AI agent bound to the provider, model, and backend you choose; stages gate on CI; and a pull/
merge request is opened for human review.

## What it is (and is not)

- **Is:** a platform-neutral **config contract** + per-platform renderers + pluggable agent
  backends + a CLI ([commands](docs/CLI.md)) + a Claude Code skill front door (planned). Provider-, model-, platform-, and language-agnostic.
- **Is not:** an agent (it *composes* mature OSS agents), a deployment system, a runtime, or anything
  tied to one language, one AI vendor, or one Git host.

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the design and
**[docs/LANDSCAPE.md](docs/LANDSCAPE.md)** for how it differs from existing tools.

## Flexible by design

**Stages are agents; anything plugs in.** A pipeline is an ordered, extensible graph of stages. Each
stage binds a **role/type** ([stage types](docs/CONFIGURATION.md#stages-optional--the-agent-graph))
to a **provider + model**; the coding tool is derived from the provider — `openai` runs Codex:

| Stage | Provider | Model | Tool (derived) |
|---|---|---|---|
| review | openai | app-supplied | Codex |
| security | openai | app-supplied | Codex |

Today the toolkit has one backend renderer, **OpenAI (Codex)**; more providers/tools are roadmap and
slot in through the same provider→tool map without forking the contract. What the GitHub renderer
can render today is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), section 8.

**Models are configurable and layered.** You need not specify a model at all; how a stage's model is
chosen is set out in [Model resolution](docs/CONFIGURATION.md#3a-model-resolution).

**Any platform.** `platform.type` selects a renderer that maps the same contract to that system
(PR↔MR, roles, required checks). Which platforms render today is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), section 8.

**Compose, don't reinvent.** New tools plug in through one seam: a stage's optional `backend` override
wraps a mature OSS agent (OpenHands, PR-Agent, SWE-agent) or a custom adapter — roadmap today, added
without touching the rest. Normally you omit it and let the provider choose the tool.

**Skills.** Reusable **skills** (methodology: checklist, rubric, output format —
provider/backend/language-agnostic) are the content; a stage points at one with `skill:`. Ships with
`code-review` and `security-review` skills. See
[skills](docs/ARCHITECTURE.md#3a-skills--content-vs-wiring).

**Simple by default, advanced when you want it.** A runnable config is a few lines; the minimal one is
in [docs/CONFIGURATION.md](docs/CONFIGURATION.md), section 3. Define `stages` only for finer control.

**Secrets stay secret.** The toolkit never logs, prints, or exposes any credential (API key, token,
username, or password), never stores them, and keeps them out of `.agentic/config.yml` — see
[Secret handling](docs/CONFIGURATION.md#3b-secret-handling-non-negotiable).

**Nothing is hardcoded.** The config names everything that varies, so an org, team, or individual
can bend the toolkit to how they deploy and host:

- **Profiles** (`minimal`/`standard`/`custom`) — expand to a default stage graph; override any part.
- **Providers and secrets** — any provider id, and the *name* of every secret it uses.
- **Fast-path routing** — send docs-only or other trivial changes to a lighter set of stages, or
  turn it off so every PR gets every stage.

**Language-agnostic.** Stages never assume a language. How a repo declares what "green" means
(build and test commands) is designed in
[design-docs/09-check-stages.md](design-docs/09-check-stages.md); the config does not read it yet.

## Quickstart

1. **Install the CLI** (needs only Python 3.10+; see [docs/CLI.md](docs/CLI.md) for options). `stagr`
   is not on PyPI yet, so install it from the repository's source archive — pip/pipx download and
   build it with no `git` required:
   ```bash
   pipx install "https://github.com/exepex/agentic-foundation/archive/refs/heads/main.tar.gz"
   # once published this becomes: pipx install stagr
   ```
   For a reproducible, auditable install, pin the URL to a commit SHA (or a release tag) instead of
   `main` — see [docs/CLI.md](docs/CLI.md).
2. Run `stagr help` to see the available commands.
3. In your target repo, run `stagr init` ([docs/CLI.md](docs/CLI.md)). The starter skills ship with
   Stagr, so no other file is needed. The full field reference
   (including how to override a skill), provider→secret mapping, and troubleshooting are in
   **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.
4. Run `stagr plan` in the repo root to validate the config and list the changes it would make under
   `.github/workflows/` (nothing is written), then `stagr apply` to make exactly those changes.

The commands that exist today, and the planned ones, are listed in [docs/CLI.md](docs/CLI.md).

## Layout

```
stagr/                                 the installable package
stagr/cli/                             the `stagr` command
stagr/core/                            the neutral core: config validation, normalization, stage graph,
                                       backend renderers (renderers/)
stagr/platforms/github/                the GitHub renderer
stagr/config.schema.json               the config contract
stagr/templates/skills/<id>/           reusable skill methodologies (code-review, security-review)
pyproject.toml                         packaging for the `stagr` command
docs/                                  ARCHITECTURE.md, CONFIGURATION.md, CLI.md, CHARTER.md, LANDSCAPE.md
design-docs/                           the design of the neutral core and renderers
.github/workflows/                     this repo's own hand-written automation
.github/scripts/                       validate_config.py + tests
.agentic/config.yml                    this repo's own agentic contract (dogfood)

```

## Dogfooding

This repository runs the pattern on itself. `.agentic/config.yml` is its declarative source of truth
(a Codex review stage and a Codex security stage), and `.github/workflows/` are the hand-written
**reference implementation** the GitHub renderer (`stagr/platforms/github/`) is modelled on:

- **Codex reviews** the code of every new commit by itself (a Codex App setting), and the final
  security review is requested once the code review is clean (`request-final-security-review.yml`);
  the deterministic router (`fast-ai-code-review.yml`) routes every PR to Codex. The review process
  is set in `AGENTS.md`, "Git and pull-request rules".
- **`Validate`** (`validate.yml`) is the CI gate. Review threads that a later commit made outdated,
  with Codex-only comments, **auto-resolve** (`resolve-fixed-codex-review-threads.yml`); who resolves
  every other thread is set in `AGENTS.md`, "Review threads". The **fail-closed foundation gate**
  (`auto-merge-foundation-prs.yml`) merges provably-ready PRs; the conditions and the `human-merge`
  stop are set in `AGENTS.md`, "Merge lanes". All of these workflows are hand-written for this
  repository. Thread resolution is also a Stagr feature for the repositories it sets up: see
  "Files written" in [docs/CLI.md](docs/CLI.md).

> **Status:** what exists today and what comes next is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
> section 8.

## License

stagr is **source-available** under the [Business Source License 1.1](LICENSE) — not a
traditional open-source license. In short: you may read, modify, redistribute, and use it free of
charge for internal and non-production work, and in production within a single organization on
repositories you control. Other production use — for example offering stagr to third parties as a
hosted or managed service, or embedding it in a product or service you provide to others — requires a
commercial license. Each released version converts to Apache 2.0 four years after its publication.

For commercial licensing, contact contact.exepex@gmail.com.
