# agentic-foundation — Charter: Scope, Non-Goals & Design Principles

> This document is the guardrail against scope creep. Every feature request, config key, and PR is
> measured against it. If a change fails the litmus test (§4) or lands in the Non-Goals (§5), it does
> not belong in the toolkit — it belongs in a **backend**, a **referenced skill**, or the user's own
> **CI/SCM**.

## 1. What agentic-foundation is (one sentence)

A **platform-neutral contract + renderer** that drops into any repository and turns one small
declarative file (`.agentic/config.yml`) into a governed, multi-stage agentic SDLC pipeline on that
repo's existing CI/SCM — with minimal configuration. This is the design goal; see the Status note
below for what renders today.

Its value is **integration, governance, and portability** — never agent capability.

> **Status (today):** this one-sentence identity and Sections 2–6 describe the target, not
> everything that renders today. What exists today is in [ARCHITECTURE.md](ARCHITECTURE.md),
> section 8; the in-scope roadmap is §7 below.

## 2. The line we never cross: declare · initialize · govern — never execute

agentic-foundation is a **control plane**, not a **runtime**.

| agentic-foundation DOES (in scope) | Backends / CI / platform DO (out of scope) |
|---|---|
| **Declare** the stage graph + gates (the contract) | Run the agents (Codex, Claude Code, …) |
| **Initialize / render** it into native pipelines (GitHub Actions, GitLab CI, …) | Execute the model calls, tests, scans |
| **Govern**: merge lanes, required-stage gates, review-clean checks | Store memory / embeddings / RAG |
| **Resolve** models & secrets by *name* | Host dashboards / build UIs |
| **Audit / emit** run + cost + decision events to the org's stack | Provide the compute / runners |

Rule of thumb: **the toolkit writes the wiring; the platform runs the work.** The moment the toolkit
itself calls a model, runs a test, or hosts a long-running service, it has become the framework we
refuse to be.

*Edge case — provider adapters.* When a provider has no first-class integration (GitHub Codex app,
`claude-code-action`, a platform-native equivalent, …), the toolkit may **render a thin adapter step
that runs in the user's CI runner** — never inside a toolkit-hosted process or service. Execution
always stays on the user's side of the line.

## 3. North-star scenario (why it exists)

The canonical flow, generalized from the permission-api origin:

> A PR is opened → the configured stages fire automatically → an AI reviewer (e.g. Codex) reviews →
> an AI implementer (e.g. Claude) addresses the findings, pushes, and the reviewer re-reviews → the
> deterministic gates (CI, **integration tests, security review, and any org-specific stages**)
> must all go green → unresolved findings block → finally it rests, **provably ready**, awaiting human
> approval.

The toolkit's job is to let a repo declare **its own "definition of ready-to-approve"** — which in a
large org is **not** just CI, but an arbitrary, ordered graph of gates (build, unit,
integration, security review, code review, license/compliance, custom) — and to **orchestrate and
gate** that graph on its platform (today GitHub; more platforms in §7). It orchestrates and governs
those stages; it never *is* any of them. (This is the target; what renders today is in
[ARCHITECTURE.md](ARCHITECTURE.md), section 8, and §7 is the roadmap.)

## 4. The litmus test (apply to every feature and every config key)

**"Is this WIRING/GOVERNANCE, or DOING-THE-WORK?"**

- **Wiring/governance → in.** How stages connect, gate, resolve, render, and get audited.
- **Doing-the-work → out.** It belongs to a backend or a referenced skill.

A config key must also pass: *does the toolkit own this, or does a backend/CI/SCM already own it?*
If the latter, **reference it by name — don't re-declare it.**

## 5. Non-goals (what keeps us a tool, not a framework)

- ❌ **Not an agent runtime / execution engine** — delegate to Claude Code, Codex, etc.
- ❌ **Not a skills/prompt library or marketplace** — ship a few reference skills; orgs bring their own by id.
- ❌ **Not a model gateway/proxy** — never route model calls through the toolkit. Reference provider *credentials* by secret name.
- ❌ **Not memory / RAG / knowledge base** — a backend concern.
- ❌ **Not a GUI / dashboard / control-plane service** — stay CLI + config + CI; emit to the user's tools.
- ❌ **Not a general workflow engine** (Airflow/Temporal) — it is an opinionated SDLC stage graph; the opinionation is the feature.
- ❌ **Not vendor lock-in** — a new platform is a *renderer*, never a rewrite.

## 6. Configuration minimalism (a hard budget, not a preference)

"Configurable" must never mean "overwhelming." **Advanced ≠ unlimited.**

1. **Minimal by default.** The common path is a handful of lines — a `profile` and a `platform`. A
   newcomer never sees the advanced surface.
2. **Progressive disclosure.** Advanced blocks are opt-in; deleting any one falls back to a sensible
   default. Fail loud only where silence would be unsafe (e.g. a stage with no provider).
3. **Curated, capped advanced surface.** Advanced options are *selected and limited*, not exhaustive.
   Even a power user should never face 100+ knobs. If a feature needs many keys, it is probably
   doing-the-work (§4) — reject it.
4. **No key the platform already owns.** If CI, the SCM, or a backend already exposes it, reference it
   by name; don't mirror it into the contract.
5. **Every key earns its place** against §4, and profiles/presets exist so most users override
   *nothing*.

**Design target:** the 90% repo is productive with ~10 lines; the 100% repo never needs more than a
small, readable file.

## 7. In-scope roadmap (all wiring/governance)

Ordered by fit to the identity; each is control-plane, not runtime:

1. **Zero-config onboarding** — detect language/build/platform → propose a default `.agentic/config.yml`
   (detection in `stagr init`, still planned, + front-door skill), so the toolkit "just works" when
   dropped into a repo.
2. **Multi-stage "definition of ready"** — the code and security reviews are gated stages in the graph
   (how each profile orders them is under `profile` in [CONFIGURATION.md](CONFIGURATION.md)). Still
   roadmap: first-class build / test / custom gate stages in the graph and the merge gate, beyond the
   code and security reviews.
3. **More platform renderers** — GitLab, Bitbucket, Azure DevOps (contract → native pipeline mapping
   only; the agents and runners stay the platform's).
4. **Budget & guardrail enforcement** — cost and loop limits; the config gets keys for them only once enforcement exists.
5. **Gate/trust hardening** — base-controlled validation, unforgeable routing/gate signals.
6. **Observability as an emit-adapter** — run / cost / decision events to the org's existing stack.

Anything not reducible to one of these — or to a *referenced* backend/skill — is out of scope.
