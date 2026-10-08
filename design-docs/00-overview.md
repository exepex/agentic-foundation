# Stagr Neutral Core — Overview

**Status:** Design phase — not yet implemented  
**Scope:** Neutral core architecture for the Stagr toolkit  
**Audience:** Implementors, reviewers, future renderer authors

---

## What Stagr is

Stagr is a **platform-neutral control plane** for SDLC pipelines. It translates a
declarative configuration file (`.agentic/config.yml`) into native CI/SCM wiring for
a target platform (e.g., GitHub Actions workflows). Once rendered, the pipeline runs
entirely inside the target platform. Stagr does not run during pipeline execution.

The three-layer model:

| Layer | Owner | When |
|---|---|---|
| **Contract / Policy** | Operator (config) | Authoring time |
| **Render** | Stagr CLI | `stagr apply` |
| **Execution** | CI platform | Run time |

Stagr operates only at the **Render** layer. It writes the wiring; the platform runs
the work.

The Stagr commands that exist today are listed in [docs/CLI.md](../docs/CLI.md); this set also
describes the planned ones as they will work. The renderers return the
artifacts that `plan` lists and `apply` writes.

---

## Control plane litmus test

> "Does this require Stagr to be running while the pipeline executes?"

If yes, it belongs in the platform's generated artifacts, not in Stagr. Stagr must
never be a runtime dependency of the pipelines it generates.

---

## Three-layer separation

```
Operator writes:        .agentic/config.yml
                              │
                    stagr apply (render time)
                              │
                              ▼
Stagr writes:         .github/workflows/*.yml
                         (or equivalent)
                              │
                    GitHub Actions / CI platform
                              │
                              ▼
Platform executes:    PR events → review requests → merge decisions
```

Each layer knows nothing about the layer above it at run time. The generated workflows
do not call back into Stagr.

---

## Scope of this specification

These documents specify the **neutral core**: the concepts, objects, and rules that are
platform- and provider-independent. They do not specify any particular renderer
implementation. Renderer implementations may add platform-specific details; they may
never contradict the neutral core.

### Documents in this set

| Document | Topic |
|---|---|
| `00-overview.md` | This file — scope and principles |
| `01-neutral-config-contract.md` | What belongs in `.agentic/config.yml` |
| `02-canonical-stage-model.md` | Enumerations, NormalizedStage, dependency semantics |
| `03-provider-backend-model.md` | Provider, backend, model separation; secret resolution |
| `04-render-time-architecture.md` | Render pipeline, object model, paths, artifact classes, invariants |
| `05-governance-and-trust.md` | TrustPolicy, RoutingPolicy, MergePolicy, human-gated lane |
| `06-runtime-boundary.md` | EvidenceSpec, StageResultSignal, RouteClassification, idempotency |
| `07-validation.md` | Static and environment validation checklists |
| `08-github-codex-mapping.md` | How the neutral model maps to the current GitHub+Codex implementation |
| `09-check-stages.md` | Build, test and other CI-result stages: executors, results, trust, ordering, work items |
| `doctor/` | Proposed design for `stagr doctor` (environment checks, roles, CI mode) |
