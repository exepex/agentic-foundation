# agentic-foundation — architecture

This is the design of the toolkit: the mental model, the layers, and how it stays
**easy for newcomers** yet **granular for experts**, across **any provider/model**,
**any SCM platform**, and **any language**.

---

## 1. Mental model: a pipeline is a graph of stages

A repository's agentic pipeline is an **ordered, extensible graph of stages**. Each
**stage is one agent** in the SDLC/STLC, of one of the
[stage types](CONFIGURATION.md#stages-optional--the-agent-graph), bound to:

- a **provider + model** (the knob — `openai` today; mix per stage as providers are added),
- a **backend** (the executor/tool; derived from the provider, overridable),
- **triggers** (issue label, PR/MR opened or updated, manual),
- a **gate** (advisory = comment only; blocking = emits a required status check),
- **dependencies** (`depends_on`) that define the graph edges.

`review` and `security` are just two built-in stage *types*; they are not special.
The same contract expresses a two-stage pipeline or a full SDLC of a dozen stages.

```
PR/MR ──▶ [build] ──▶ [review] ──▶ [security] ──▶ gate ──▶ human
```

---

## 2. Layers (separation of concerns)

The toolkit is deliberately split so each concern can change without disturbing the others.

| Layer | Responsibility | Configured by |
|---|---|---|
| **1. Contract** | Declarative, platform-neutral description of the pipeline. | `.agentic/config.yml` (this schema) |
| **2. Provider adapters** | Talk to a model vendor (Claude / OpenAI / Gemini / local / gateway). Give true provider-agnosticism. | `stages[].provider`, `defaults.models`, `secrets` |
| **3. Agent tools** | Execute a stage. The tool is derived from the provider (`openai` → Codex); roadmap adapters wrap other OSS agents. | `stages[].provider` (or `stages[].backend` to pin) |
| **4. Platform/SCM adapters** | Render the neutral pipeline into a concrete CI system and normalize concepts (PR↔MR, roles, checks). | `platform` |
| **5. CLI** | The `stagr` command; see [CLI.md](CLI.md). | — |

The **contract never names a language, a vendor SDK, or a CI system directly** — those
live in layers 2–4, so a repo swaps any of them by editing config, not workflows.

---

## 3. Agent tools — compose, don't reinvent

A landscape scan (see `docs/LANDSCAPE.md`) shows mature OSS agents already do the hard
parts well, but none bundle a configurable implementer **and** reviewer as one
provider-agnostic, drop-in toolkit. So agentic-foundation is an **orchestration /
contract layer**: a stage names a **provider**, and the toolkit derives the coding tool
(the *backend*) and wires it in. Normally a stage sets only `provider`; an explicit
`backend` pins a tool or adopts a roadmap adapter.

| Backend (tool) | Wraps | Derived from | Status |
|---|---|---|---|
| `codex` | OpenAI Codex | `openai` | shipped (what renders: section 8) |
| `claude-code-cli` | Anthropic's Claude Code (CLI runner) | — (override only) | roadmap |
| `openhands` | OpenHands issue resolver | — | roadmap |
| `swe-agent` | SWE-agent | — | roadmap |
| `pr-agent` | Qodo/PR-Agent | — | roadmap |

`backend` is a plain string. The tool follows the provider; an explicit `backend` override lets you
pin one or adopt a roadmap adapter later without touching the rest of the pipeline.

What the GitHub renderer can render today is in section 8 below.

---

## 3a. Skills — content vs. wiring

Two distinct concepts, cleanly layered so the domain knowledge is reusable and portable:

| Concept | Is | Lives in | Referenced by |
|---|---|---|---|
| **Skill** | The reusable *methodology/content* for a task — checklist, rubric, output format. Provider/backend/language-agnostic. | `stagr/templates/skills/<id>/SKILL.md` (shipped); a repo overrides one with its own `.agentic/skills/<id>/SKILL.md` | `stages[].skill` |
| **Stage** | An agent *placed in the pipeline graph* (with `depends_on`, overrides). | `.agentic/config.yml` `stages[]` | the pipeline |

Why the split:

- **Skills are the crown jewel** — the portable domain knowledge. A backend adapter maps a skill to
  its own prompt/rule format, so the *same* skill drives any backend.
- **Profiles expand into working stages** that already name the right skill, so a repo adopts a
  ready stage without writing one.
- **No vendor/model assumptions** in a skill, and **no secrets** — skills are templates.

Starter skills: `code-review`, `security-review`. How a stage's skill is found and overridden is set
out in the `skill` field of [CONFIGURATION.md](CONFIGURATION.md). The catalog may grow (`planning`, `execution-plan`,
`unit-test-authoring`, `integration-test`, `docs`, `release-notes`).

## 4. Provider/model resolution

Each stage resolves its own model, so "same provider, different models" or "mix providers" is
expressed independently per stage. The precedence is set out in
[Model resolution](CONFIGURATION.md#3a-model-resolution).

---

## 5. Platform neutrality

The contract is written once and rendered per platform. `platform.type` selects the
renderer (the values are listed under `platform` in [CONFIGURATION.md](CONFIGURATION.md); which
render today is in section 8). The renderer normalizes platform concepts:

| Neutral concept | GitHub | GitLab | Azure DevOps |
|---|---|---|---|
| change request | Pull Request | Merge Request | Pull Request |
| trusted roles | `author_association` | project roles | security groups |
| required gate | status check | pipeline job / approval rule | branch policy |
| CI unit | workflow | pipeline | pipeline |

The same `.agentic/config.yml` therefore drives any of them; only layer 4 differs.

---

## 6. Easy vs. granular

- **Newcomer:** set `profile` + `platform`. The profile expands to a default stage graph.
- **Expert:** define `stages` explicitly — per-stage provider/model/backend/triggers/gate/
  dependencies.

Profiles and explicit stages compose, so you can accept the standard graph and tweak just one
stage. What each profile expands to, and how listed stages merge onto it, is set out under `profile`
and `stages` in [CONFIGURATION.md](CONFIGURATION.md).

---

## 7. Cross-cutting invariants

- **Secrets** are referenced by **name** only; see
  [Secret handling](CONFIGURATION.md#3b-secret-handling-non-negotiable).
- **Language-agnostic**: the contract never names a language. How a repo declares what "green"
  means is designed in `design-docs/09-check-stages.md`; the config does not read it yet.

---

## 8. Status & roadmap

- **What renders today:** GitHub is the only platform, and only the Codex `review` and `security`
  stages render, on either Codex backend: started by a pull-request comment (`codex`) or run in the
  stage workflow on an API key (`codex-api`). Other stage types (build, test, custom) are declared and
  validated but not rendered yet.

- **M1 — contract layer and neutral core (current):** schema, config validation, profiles,
  provider/backend/model resolution, and the stage graph.
- **M2 — GitHub renderer (current):** per-stage, routing, and governance (merge-gate) workflows built
  from the graph. The renderer returns artifacts and never writes files.
- **M3 — CLI (in progress):** see [CLI.md](CLI.md) for the commands that exist and the planned ones.
- **M4 — more backends & platforms:** OpenHands/SWE-agent/PR-Agent adapters; `claude-code-cli`
  backend; GitLab and Azure DevOps renderers.
