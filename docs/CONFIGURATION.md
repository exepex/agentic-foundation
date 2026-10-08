# Configuring agentic-foundation

This is the setup reference: what the toolkit needs, the exact secret names, which credentials must be
a **service account** vs a **personal access token (PAT)**, and every field of `.agentic/config.yml`.

---

## 1. Prerequisites

- A repository you can add workflows and secrets to, on a platform that has a renderer (see
  [ARCHITECTURE.md](ARCHITECTURE.md), section 8).
- Provider access for each provider your stages use (today only OpenAI Codex).

The toolkit never creates credentials.

---

## 2. Credentials — names, type, and scope

Set these as **repository (or environment) secrets** unless noted otherwise. Names are the defaults;
you can rename them in the config (see below).

| Purpose | Name | Type | Required when | Scope / notes |
|---|---|---|---|---|
| Codex comment-trigger, PR publication, resolving outdated review threads | `REMEDIATION_TOKEN` | **Fine-grained PAT (real user)** | a stage uses Codex's `@codex` comment flow | Least scope: **Contents: R/W** + **Pull requests: R/W**. **No** admin/merge. Must be a real, attributable user — bot/App tokens do not reliably trigger `@codex`. |
| Remediation agent API key | `ANTHROPIC_API_KEY` | Anthropic API key | the config has a [`remediation`](#remediation-optional--automated-fixes) section | Also install the **Claude GitHub App** on the repository: the agent pushes its fixes as that App, which starts the next review. |
| Stagr GitHub App private key | `STAGR_APP_PRIVATE_KEY` | GitHub App private key | you configure `platform.publisher` | See [Publisher](#publisher-stagr-github-app). |
| GitHub API (PR reads) | `GITHUB_TOKEN` | Provided by Actions | always | No action needed; each generated workflow sets its own least-privilege permissions. |

No model API key is needed today: Codex is app-backed and supplies its own model.

**Why a real-user PAT:**
- **`REMEDIATION_TOKEN`** must be a **real-user PAT** because Codex acts on `@codex` commands
  only from an attributable user. Grant it the minimum (Contents + Pull requests, R/W) — it needs no
  permission to merge or administer.

> Secret names are configurable. The names above are defaults; override them per provider with
> `providers.<provider>.secrets.<alias>` and per platform with `platform.auth.token_secret`.

---

## 3. `.agentic/config.yml` — field reference

Start from `stagr init` ([CLI.md](CLI.md)), then edit the file and keep it valid against
[`stagr/config.schema.json`](https://raw.githubusercontent.com/exepex/agentic-foundation/main/stagr/config.schema.json).
The schema lists exactly the keys the toolkit reads; a key that is not listed does nothing.

- An **unknown key inside a Stagr key** (`platform`, `defaults`, `stages`, ...) is an error.
- An **unknown top-level key** is ignored, not rejected, so you can keep your own tooling settings in
  the same file.

**Simple by default, advanced when you want it.** A runnable config needs a `version` (always `2`), a
`profile` (default `standard`, which expands to a stage graph), a `platform` (defaults to GitHub), and
`platform.publisher.app_id`, the ID of your Stagr GitHub App, which `stagr plan` and `stagr apply`
require (see [Publisher](#publisher-stagr-github-app)). That is a few lines; add `stages` and other
blocks only to take finer control. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the design.

```yaml
# Minimal config — the profile expands to a stage graph.
version: 2
profile: standard
platform: { type: github, publisher: { app_id: 123456 } }
```

### `profile`
| Field | Meaning |
|---|---|
| `profile` | Onboarding shortcut that expands to a default stage graph: `minimal` (a code `review` stage), `standard` (`review` + `security`, where `security` waits for `review`), or `custom` (no stages — you define them all under `stages`). Default `standard`. Stages you list under `stages` are merged on top (same id overrides). List or switch profiles with `stagr profile` ([CLI.md](CLI.md)). |

### `platform`
| Field | Meaning |
|---|---|
| `type` | `github`. Selects the renderer. |
| `same_repo_only` | `true` = ignore fork PR/MR heads. Keep `true` unless you accept fork contributions (widens the threat model). Default `true`. |
| `trusted_roles` | Normalized permission levels allowed to drive agentic changes (`owner`, `member`, `collaborator`, `contributor`); the renderer maps them to the platform's own roles. Default `owner`, `member`, `collaborator`. |
| `auth.token_secret` | **Name** of the secret holding the platform API token. Never the value. |
| `publisher.app_id` | The numeric ID of the **Stagr GitHub App** that publishes Stagr's own Check Runs. A positive whole number (quoted digits also work). It is **not a secret**: it is written as-is into the generated workflows. No default. The schema accepts a config without it, but **`stagr plan` and `stagr apply` require it**: the generated workflows publish their check runs as this App. |
| `publisher.private_key_secret` | Optional, used with `publisher`. The **name** of the repository secret that holds the App's private key. Default `STAGR_APP_PRIVATE_KEY`. Never the key itself. |
| `labels.human_merge` | A change-request with this label is **never** merged automatically (a human keeps merge authority). Default `human-merge`. |

A secret name is letters, digits and underscores, not starting with a digit, and not starting with
`GITHUB_` (GitHub reserves that prefix). The same rule applies to every secret-name field below.

### `defaults` (optional — fallbacks for stages)
| Field | Meaning |
|---|---|
| `provider` | Default provider id for stages that omit one. If a stage has no provider and there is no default, validation fails. |
| `models.<provider>.default` | Default model ID for that provider when a stage does not set its own. `<provider>` is any provider id. |

### `providers` (optional — secret names per provider)
| Field | Meaning |
|---|---|
| `providers.<provider>.secrets.<alias>` | Maps a semantic alias used by the backend (e.g. `TRUSTED_COMMENTER_TOKEN`) to the actual repository secret name. Never the secret value. See **Secret alias resolution** below. |

#### Secret alias resolution

When the toolkit writes `env:` entries into generated workflow YAML it resolves each backend-declared
alias to a concrete CI secret name using the following precedence (first match wins):

1. **Explicit map** — `providers.<provider>.secrets.<alias>` in your config.
2. **Established default** — `TRUSTED_COMMENTER_TOKEN` → `platform.auth.token_secret` if set,
   otherwise `REMEDIATION_TOKEN`.
3. **Convention** — any other alias is used as the secret name directly (alias == secret name).

Example — use a custom PAT for every provider, or only for Codex:

```yaml
platform:
  auth:
    token_secret: MY_GITHUB_PAT              # overrides the REMEDIATION_TOKEN default
providers:
  openai:
    secrets:
      TRUSTED_COMMENTER_TOKEN: MY_CODEX_PAT  # wins over platform.auth.token_secret for openai
```

#### Publisher (Stagr GitHub App)
Stagr publishes its own Check Runs as a GitHub App, not with a personal token. You create the App
yourself and store its private key as a repository secret; the config only names them:

```yaml
platform:
  publisher:
    app_id: 123456                          # the App's numeric ID (not a secret)
    private_key_secret: STAGR_APP_PRIVATE_KEY   # NAME of the secret holding the App private key
```

Steps for the operator: create the GitHub App, install it on the repository, save its private key as a
repository secret with the name you put in `private_key_secret`, and set `app_id`.

Give the App these repository permissions (without them the generated workflows fail with
authorization errors):

| Permission | Access | Why |
|---|---|---|
| Checks | Read and write | Create and update the stage Check Runs; the merge gate reads them and publishes its verdict (setup step 5, section 4) |
| Pull requests | Read | Read pull requests, changed files and review threads |
| Issues | Read | Read pull request comments, where review backends post their results |
| Metadata | Read | Granted automatically |

A value that is not a valid secret name (for example a pasted key) is rejected when the config is
validated, and the rejected value is never echoed. The private key gives access wherever the App is
installed, so guard it and rotate it if it leaks.

### `stages` (optional — the agent graph)
Omit to use the profile's stages. Anything you list is **merged onto** the profile (a stage with the
same `id` overrides). Each stage is one agent. One provider has a default backend today: `openai`
(Codex).

| Field | Meaning |
|---|---|
| `id` | **Required.** Unique stage id (`^[a-z0-9][a-z0-9-_]*$`), e.g. `review`, `security`. |
| `type` | **Required.** `review` \| `security` \| `build` \| `test` \| `custom`. |
| `enabled` | `false` to keep a stage defined but off. Default `true`. |
| `provider` | **The primary knob.** `openai` runs Codex. Omit to inherit `defaults.provider`. |
| `backend` | Optional. The tool that performs the stage, as a plain string. Omit it: the default follows the provider (`openai` → `codex`). |
| `model.default` | Optional model ID for this stage; overrides `defaults.models.<provider>.default`. |
| `skill` | Skill id: the stage's methodology. Stagr uses your repo's `.agentic/skills/<id>/SKILL.md` when it exists (copy a shipped skill there and edit it to override it), otherwise the skill Stagr ships under [`stagr/templates/skills/`](https://github.com/exepex/agentic-foundation/tree/main/stagr/templates/skills): `code-review` and `security-review`. Validation (V-S06) fails if neither exists. |
| `triggers` | Any of `pr_opened`, `pr_updated`, `manual`, `issue_labeled`. |
| `gate` | `advisory` (reported, never blocks merge) or `blocking` (the merge gate requires the stage to pass). Omit for `advisory`. |
| `depends_on` | Ids of stages that must pass before this one starts (defines the graph). Unknown ids and cycles are rejected. |

> **Which stages render.** A stage whose backend the platform renderer cannot start is rejected by
> validation (V-S08). What renders today is in [ARCHITECTURE.md](ARCHITECTURE.md), section 8.

See **Model resolution** below for how a stage's model is chosen.

### `routing.fast_path`
| Field | Meaning |
|---|---|
| `enabled` | `false` disables the fast path so every PR (docs included) is routed to every stage. Default `true`. |
| `globs` | A PR takes the fast path only when **every** changed file matches at least one of these globs. |
| `stages.fast` | Stage ids that run when a PR qualifies for the fast path. |
| `stages.normal` | Stage ids that run for all other PRs. |

Each of `stages.fast` and `stages.normal` must be **dependency-closed** (validation V-S09): if a listed
stage has a `depends_on` entry, that entry must be in the same list.

Set `enabled: false` when every change must go through review — e.g. a shared toolkit whose
documentation other people depend on. This repository does exactly that.

### `merge`
| Field | Meaning |
|---|---|
| `discussions.require_resolved` | When `true`, all open review discussions must be resolved before the merge gate passes. Default `false` (discussion state is not checked). |

### `remediation` (optional — automated fixes)
Omit it to fix review findings by hand. With it, `stagr apply` writes `remediation.yml`: after each
review from a stage's review backend (Codex) or a trusted human reviewer, an agent judges every
finding, fixes the ones that are real and declines the rest with its reasons.

```yaml
remediation:
  provider: anthropic        # Claude Code Action, the only provider today
  max_rounds: 5              # optional, 1 to 10
  api_key_secret: ANTHROPIC_API_KEY   # optional, NAME of the secret
```

| Field | Meaning |
|---|---|
| `provider` | The fixing agent. `anthropic`: Claude Code Action. Required. |
| `max_rounds` | Automated fix rounds per pull request. Default `5`. |
| `api_key_secret` | NAME of the secret holding the provider API key. Default `ANTHROPIC_API_KEY`. |

How a round works:

- **Judging.** The agent accepts a finding only when it describes a real problem in the change: a
  realistic input or caller that reaches it and the wrong result, a plausible exploit path, a broken
  contract, or a missing test for changed behavior. Hypothetical or highly unlikely cases, misuse the
  contract already rules out, style, and fixes that cost more than the risk are declined. When in
  doubt, it declines. Codex is asked for the same standard with every review request.
- **Fixed finding.** The agent changes the code and pushes one commit starting `fix(review):`; only
  after the push succeeds does it reply on the thread with what it changed. That push starts the next
  review, and the resolution workflow resolves the thread (see "Files written" in [CLI.md](CLI.md)).
- **Declined finding.** The agent replies with its evidence and leaves the thread open. An open
  finding thread on the current commit keeps the merge gate blocked, so a human decides: resolve the
  thread to accept the decline, or answer it with a review comment, which the agent picks up as a new
  review.
- **Round limit.** When a pull request already has `max_rounds` fix commits and a new review still
  has findings, the pull request gets the human-merge label (`platform.labels.human_merge`) and one
  comment. While the label is set, no review is requested and no fix is attempted. To hand the pull
  request back to automation, remove the label, then push a commit.
- **Who drives it.** Only pull requests from a branch of this repository whose author has a trusted
  role (`platform.trusted_roles`), reviewed by a stage's review backend or a reviewer with a trusted
  role, about the pull request's current commit. Only the reviewer's and the review backends'
  comments reach the agent, and review text is data for it, never instructions. Its tool permissions
  forbid editing `.github/` and `.agentic/`, and it never resolves a thread.

---

## 3a. Model resolution

A stage's model is resolved once, **most specific wins**:

1. `stages[].model.default`
2. `defaults.models.<provider>.default`, where `<provider>` is the stage's provider (or
   `defaults.provider` when the stage names none).

If neither is set, no model is bound and the backend decides. `openai` (Codex) is app-backed and
supplies its own model, so a Codex stage normally needs no model at all. A model value is a literal
model ID; the toolkit does not rewrite it.

---

## 3b. Secret handling (non-negotiable)

The toolkit treats every credential as write-only and invisible:

- **Never logged, never printed, never echoed.** No secret — API key, token, username, or password —
  is written to workflow logs, step output, PR/issue comments, review text, error messages, or any
  artifact. Commands that could surface a secret are masked or avoided.
- **Passed only to the step that needs it,** via GitHub Actions secrets / `env`, scoped to the
  minimal job — never interpolated into a shell string that gets logged, and never persisted to disk.
- **The toolkit never creates or stores credentials.** The config names a secret; it never holds the
  value.
- **No secret in config.** `.agentic/config.yml` holds only non-sensitive settings and secret
  **names**; credentials live in repo/environment secrets (section 2). Do not put tokens in the config
  file.

If you ever see a secret value in a log or comment, treat it as compromised and rotate it.

---

## 4. Setup steps

> **Prerequisite — the Codex GitHub App (only for `openai` stages).** A stage with `provider: openai`
> runs **Codex**, which requires the **Codex GitHub App** to be installed on the repo/org and the
> repository to be connected to Codex code review, so that Codex acts on the `@codex review` and
> `@codex security review` comments the generated workflows post. The generated workflows request
> every review themselves, in the order the stage graph sets, so Codex's own **automatic** code and
> security review are not needed; if they are on, Codex reviews the same commit twice and its
> security review can start together with the code review. Install the App **before** relying on the
> pipeline and confirm on a test PR that the reviews run.

1. Create the Stagr GitHub App and its private-key secret, and note the App's ID
   ([Publisher](#publisher-stagr-github-app)).
2. Create the secrets your providers need (section 2) in your CI/SCM secret store.
3. Run `stagr init` ([CLI.md](CLI.md)) to start `.agentic/config.yml` (section 3). Add `stages` only
   for finer control. The shipped skills need no copy;
   see the `skill` field in section 3 for how a skill is found and overridden.
4. Run `stagr plan` to validate the config and list the workflow files it would write or remove (it
   writes nothing), then `stagr apply` to make those changes in `.github/workflows/`. Commit the
   result. See [CLI.md](CLI.md).
5. Make the merge gate required: in a branch ruleset (or branch protection) for the default branch,
   require the status check **`stagr/governance`** and set its source to your Stagr GitHub App. The
   governance workflow publishes that check on each pull request's head commit, failing until every
   blocking stage has passed on that commit. Do not require the workflow's own job: a run that starts
   when a stage finishes executes on the default branch, so the job's result never reaches the pull
   request.
6. Optional: add a [`remediation`](#remediation-optional--automated-fixes) section, install the
   Claude GitHub App on the repository, and create its API key secret (section 2). Run `stagr apply`
   again.

---

## 5. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Reviewer never runs on Codex | `REMEDIATION_TOKEN` missing or not a real-user PAT, or the Codex GitHub App is not installed. |
| Pull request stays blocked after every stage passed | The default branch's ruleset does not require the merge-gate check as set out in setup step 5 (section 4). |
| Fast path never triggers | A changed file matches none of `routing.fast_path.globs`. |
| Config rejected with a secret-name error | A `*_secret` field holds something that is not a valid secret name (for example a pasted token). Put the value in a CI secret and use its name. |
