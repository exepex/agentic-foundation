# Doctor — Validation Design

Part of the [doctor design set](README.md). Supersedes the "Environment validation" section of
`design-docs/07-validation.md` once accepted. Constraints (C#) and facts (F#) are in
[01-constraints.md](01-constraints.md). Contexts (local, pipeline, central) are defined in
[04](04-cli-and-output.md).

## Order and status model

1. Static validation (V-S01 to V-S09, V-S11, V-S14) runs first through `load_render_inputs`, the same
   function `plan` and `apply` use. Any static error stops doctor before any environment check.
2. Environment checks run next and each prints one result.

| Status | Meaning | Fails the exit code |
|---|---|---|
| `PASS` | Checked and correct | No |
| `WARN` | Checked; risky but not broken | No |
| `ERROR` | Checked and wrong, or could not be checked where it must be | **Yes** |
| `SKIP` | Not checked. See the rule below for when it is allowed | No |

Rule: **in the pipeline and central contexts, a check that cannot run is an ERROR, never a silent SKIP.**
Those contexts are where the answer is expected, so a missing input is a setup mistake. `SKIP` is allowed
only when:

- the context is **local** (cannot verify here);
- an earlier ERROR blocks the check (for example V-E02c-d when the key secret is missing);
- an **optional probe** (V-E03, V-E04 live) in the **pipeline** context has no `STAGR_PLATFORM_TOKEN`, or
  GitHub answers it with 401, 403 or 404 ([06](06-deployment-scenarios.md));
- V-E02c-d in the **central** context, which need the App key. These are reported as `not verified`.

In the central context the token is the whole point, so any 401, 403 or 404 is an ERROR ("token lacks
access"). Only a positive finding from a probe is otherwise an ERROR or WARN.

## Two sources of truth

- **Requirements** are derived offline from the same render the pipeline uses: the secret names per stage
  (`ExecutionPlan.required_secrets`), the private-key secret name, the App permissions, the declared
  `permissions:` blocks, and `trusted_roles`. They drive the checklist and the live checks, so the two
  cannot disagree.
- **Observations** come only from the pipeline or central contexts (presence flags, secret listing, the
  App API). Doctor compares them to requirements.

## Checks

### V-E01 — Backend secrets present

- **Requirement:** every secret env name in every stage's `required_secrets` (after alias resolution).
- **Local:** `SKIP`; the names and stages go in the checklist.
- **Pipeline:** read presence flags (D4). Each missing secret is an ERROR naming the secret and the stage.
- **Central (D10):** list the target repo's secret names and the org secrets available to it with
  `STAGR_PLATFORM_TOKEN` (F8) and compare them to the requirement.
- **Scope:** repository and organization secrets only. The generated stage jobs declare no GitHub
  environment, so a secret that exists only in an environment is empty inside the stage. It does not
  satisfy V-E01 ([06](06-deployment-scenarios.md)).
- **Why flags in the pipeline, not the API:** listing secrets needs elevated access, and `GITHUB_TOKEN`
  cannot do it (F2). The `secrets` context already answers "does it exist?" for the repo and org secrets
  the job can read (F1).

### V-E02 — Publisher App

Four sub-checks, reported as separate lines:

| Sub-check | Local | Pipeline | Central |
|---|---|---|---|
| **a. App ID configured** | PASS (plan/apply already require it) | same | same |
| **b. Private-key secret present** (`secrets.app_private_key`) | `SKIP` | presence flag; ERROR names the secret | secrets API listing |
| **c. App installed** | `SKIP` | `GET /repos/{r}/installation` with an App JWT (F3); `404` is an ERROR naming the App ID | `not verified` (needs the App key) |
| **d. Permissions sufficient** | `SKIP` | compare `installation.permissions` to the required union; ERROR lists each missing or too-weak permission | `not verified` |

If b fails in the pipeline, c and d are `SKIP` (blocked). Levels order `read < write < admin`; installed
must be at least the required level.

**Required union (D5).** The platform renderer declares, per artifact, the App permissions it needs, and
doctor unions them. Today the required set exists only as a hand-written table in
`docs/CONFIGURATION.md` and the workflows mint tokens without `permission-*` narrowing (F5). The
implementation adds a declared-permissions field to each rendered artifact, fills it in the GitHub
renderer, and makes the docs table match. Doctor never hardcodes a global permission list.

**D3 (decided: option A) — how c and d authenticate.** The App's permissions can only be read with an
App JWT, which is signed with the private key (F3, F4). In the pipeline context the workflow passes the
key to doctor; doctor signs a 10-minute JWT **in memory** and never prints, logs or writes the key. This
amends the "no secret value is read" rule for this one context. Conditions: same-repo trusted triggers
only (push to the default branch, `workflow_dispatch`); never fork or untrusted PR code; the key is used
only to sign; it is never read outside the pipeline context ([07](07-credential-safety.md)).

Rejected: option B (never read the key; mint an installation token and probe read access only). It keeps
the rule but cannot verify `write` permissions without writing, so a missing `checks: write` would only
show up as a 403 on the first PR.

### V-E03 — Workflow permissions

- Generated workflows declare their own minimal `permissions:` blocks, so the repo's default token
  setting does not affect them. The remaining risk is an org policy that restricts Actions, which only an
  admin can read.
- **All contexts:** doctor lists the permissions each generated workflow declares (checklist), so the
  platform team knows what to allow.
- **Optional live probe (D7):** with `STAGR_PLATFORM_TOKEN`, doctor reads the repo's default workflow
  permissions and allowed actions (F6) and reports an ERROR for anything the generated workflows need
  that is blocked. It runs in the central context (token required) and in the pipeline context when the
  token is present.

### V-E04 — Trusted roles

- V-S14 already validates role values. Doctor adds an offline check that runs in every context:
  - `WARN` when `trusted_roles` is empty (`[]`). The schema allows it, but the pipeline then rejects
    every PR author, so nothing ever runs.
  - `WARN` when `trusted_roles` is only `owner`, because every PR from anyone else is skipped.
- **Optional live probe (D7):** with `STAGR_PLATFORM_TOKEN`, doctor lists collaborator roles (F7) and
  `WARN`s when nobody holds a role in `trusted_roles`. Same contexts as V-E03.

## Where the code lives

- Core (`stagr/core/`): the check-result type, the requirements derived from render output, and the
  pass/fail rules. No GitHub knowledge.
- GitHub (`stagr/platforms/github/`): context detection, the guarded API client, the JWT, the
  installation call, permission comparison, the optional probes, and the declared permissions, behind a
  small interface so tests inject a fake client (C8).
- CLI (`stagr/cli/`): thin `doctor` command wiring the above.
