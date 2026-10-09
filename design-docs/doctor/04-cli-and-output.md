# Doctor — CLI and Output

Part of the [doctor design set](README.md). Checks are defined in [03](03-validation-design.md);
credential rules in [07](07-credential-safety.md).

## One command, two optional flags

```
stagr doctor [--root PATH] [--repo OWNER/NAME]
```

- `--root` matches `plan` and `apply`.
- `--repo` is used only by the platform team to check a repository other than the one doctor runs in.
- There is no mode flag. Doctor detects its context. There is no `--strict` or `--json`; add them only
  when a real need appears.

## Contexts (detected, not chosen)

| Context | Detected by | Network | Purpose |
|---|---|---|---|
| **local** | Not in GitHub Actions, no `--repo` | none | Validate, print the provisioning checklist and CI snippet. Live checks `SKIP` |
| **pipeline** | `GITHUB_ACTIONS=true`, no `--repo` | yes | The authoritative check for this repo, using the inputs the snippet supplies |
| **central** | `--repo` given (laptop or Actions) | yes | Platform team checks a repo's secrets, settings and roles using `STAGR_PLATFORM_TOKEN` |

`--repo` is the only switch into the central context, so doctor never changes behavior silently because an
input is missing. If `STAGR_PLATFORM_TOKEN` is set in the local context, doctor stays offline, ignores it,
and says: `platform token ignored; pass --repo OWNER/NAME to run central checks`.

Detection of the context belongs in the GitHub platform module, so other platforms add their own.

## Inputs by context

Users do not memorize these. The CI snippet doctor prints already contains the pipeline ones.

| Input | Context | Source | Purpose |
|---|---|---|---|
| `STAGR_HAS_<SECRET_NAME>` = `true`/`false` | pipeline | `${{ secrets.NAME != '' }}` | V-E01, V-E02b. Flag only, never the value |
| `STAGR_DOCTOR_APP_KEY` | pipeline only | `${{ secrets.<app_private_key name> }}` | V-E02c-d JWT (D3). Never read in local or central contexts |
| `STAGR_PLATFORM_TOKEN` | central (required), pipeline (optional) | secret or operator's shell | Central: secrets, V-E03, V-E04. Pipeline: only V-E03, V-E04 |
| `GITHUB_REPOSITORY`, `GITHUB_API_URL` | pipeline | provided by Actions | Which repo and GitHub instance. `--repo` replaces the repo in a central run (F9) |

In the pipeline context, a missing flag or key is an ERROR naming it. In the central context, a missing
`STAGR_PLATFORM_TOKEN` is an ERROR.

## Output

One line per check: `[STATUS] V-Exx: short title`, then an indented reason and a fix line naming the role.
Every V-E01 and V-E02b line says its source (`flags` or `API`). Every run ends with a summary and one
**next step** line for the context.

Local:

```
stagr doctor (local: live checks are SKIPPED)
[PASS] static validation
[SKIP] V-E01: backend secrets        cannot verify locally; see checklist
[PASS] V-E02a: App ID configured     123456
[SKIP] V-E02b-d: App credentials     cannot verify locally; see checklist
[SKIP] V-E03: workflow permissions   cannot verify locally; see checklist
[WARN] V-E04: trusted roles          only `owner`: PRs from everyone else are skipped
next: hand the checklist to your platform team, then paste the CI snippet into your pipeline
```

Pipeline failure:

```
[ERROR] V-E01 (flags): backend secrets
        missing REMEDIATION_TOKEN (needed by stage `review`)
        fix: platform team adds repository secret REMEDIATION_TOKEN
[ERROR] V-E02c: App installed
        App 123456 is not installed on acme/widgets
        fix: platform team installs the App on this repository
```

Central, for example from a platform engineer's laptop:

```
$ STAGR_PLATFORM_TOKEN=<token> stagr doctor --repo acme/widgets
central: using platform token (read-only checks against acme/widgets)
[ERROR] V-E01 (API): backend secrets
        missing REMEDIATION_TOKEN (needed by stage `review`); checked repo and org secrets available to acme/widgets
        fix: platform team adds the secret, or grants the repo access to the org secret
[WARN]  V-E04: nobody holds a trusted role
not verified: V-E02c, V-E02d (they need the App key; run `stagr doctor` in the repo pipeline)
next: fix the errors above, then confirm with `stagr doctor` in the repo pipeline
```

Exit code is `0` with no ERROR, `1` otherwise. Output labels itself as environment-dependent: a local
`SKIP` is expected, and only the pipeline context is a full check.

## Provisioning checklist (printed in local context)

Plain text the author can paste into a ticket, derived from the requirements:

```
Provisioning checklist for acme/widgets  (hand to your platform team)
Secrets (names only):      ANTHROPIC_API_KEY (stage review), STAGR_APP_PRIVATE_KEY (App key)
Stagr GitHub App:          ID 123456, installed on this repository
App permissions:           checks: write, pull_requests: read, issues: read
Workflow permissions:      <per workflow, as declared>
Trusted roles:             owner, member, collaborator
```

Followed by a **CI step snippet** for this exact config: a job with the `STAGR_HAS_*` flags and the key
env already filled in, on a trusted trigger. The snippet is generated from the same requirements, so it
cannot drift.

## Docs that teach this

`docs/CLI.md` gets a setup runbook organized by role (02), and its "No network" rule becomes "no network
except `doctor` in the pipeline and central contexts". Both ship with the implementation. The exact CI
snippet is **not** duplicated in the docs (D8): the docs explain the contexts and point to `stagr doctor`,
so there is one source of truth.
