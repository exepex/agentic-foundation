# Doctor — Constraints

Part of the [doctor design set](README.md). Every choice in the other docs follows from this list.

## Constraints

| # | Constraint | Consequence for doctor |
|---|---|---|
| C1 | Developers do not have repo-admin or org-admin rights. The platform team owns Apps, secrets and org settings. | Doctor must not need admin APIs. Setup work is handed to the platform team, not done by doctor. |
| C2 | The GitHub App private key is held by the platform team and stored as a repo secret. It is not on developer machines. | Nothing that needs the key can run locally. |
| C3 | A local run may have no network and no credentials. | Local mode is fully offline and must still be useful. |
| C4 | In CI, `GITHUB_TOKEN` is limited: no secrets listing, no admin settings. A workflow *can* see secrets through the `secrets` context and can use the App credentials it is given. | Live checks use only those two things. |
| C5 | Secret values are never printed, logged, or written (see `AGENTS.md`). | Presence flags, not values. The one exception is D3 (decided): in the pipeline context doctor reads the App key in memory to sign a JWT. |
| C6 | Stagr is a control plane: it declares, initializes and governs; it never executes the pipeline (`docs/CHARTER.md`). | Doctor is read-only. It creates no Apps, secrets or settings. Generated workflows never call Stagr. |
| C7 | Stagr is pre-release with zero consumers. | Change docs and rules directly. No migration paths. |
| C8 | The neutral core is platform-independent; GitHub is the only renderer today. | Core defines check results and requirements. GitHub-specific probes live in `stagr/platforms/github/`. |

## GitHub facts to verify before implementation

These shape the design. They come from our understanding of GitHub's behavior and **must be confirmed**
(docs or a sandbox repo) before code is written. If one is wrong, the affected check changes.

| # | Claim | Used by |
|---|---|---|
| F1 | `${{ secrets.NAME != '' }}` evaluates in a step `env:` and is true for repo, environment and org secrets visible to the job. GitHub docs confirm an unset secret evaluates to an empty string; the flag pattern is to be confirmed in a real run | V-E01, V-E02 (key present) |
| F2 | `GITHUB_TOKEN` cannot list repository secrets or read Actions settings | Why the API is not used (D2, D4) |
| F3 | An App JWT (signed with its private key) can call `GET /repos/{owner}/{repo}/installation`; it returns `404` if the App is not installed and includes the installation's `permissions` otherwise | V-E02 installed, permissions |
| F4 | `actions/create-github-app-token` does not report the granted permissions | Why doctor signs its own JWT (D3) |
| F5 | The generated workflows mint the App token without `permission-*` inputs, so the token carries every permission the installation has | D5: the required set is not in the artifacts today |
| F6 | With an elevated token, `GET /repos/{r}/actions/permissions/workflow` and the repo or org Actions permissions endpoints return the default workflow permissions and allowed actions. A classic token needs `repo`; the fine-grained and App permission needed is **not yet confirmed** | V-E03 optional probe |
| F8 | With an elevated token, the repo secrets list and the org secrets available to a repo are readable and return names only, never values (confirmed by GitHub docs). The minimum fine-grained or App permission is **not yet confirmed** | V-E01, V-E02b in a central run |
| F9 | The default `GITHUB_*` variables in a workflow cannot be overwritten, so a central workflow cannot point `GITHUB_REPOSITORY` at another repo | Why `--repo` exists (D10); a central run cannot rely on `GITHUB_REPOSITORY` |
| F7 | With write, maintain or admin access, `GET /repos/{r}/collaborators` returns each collaborator's highest role from any source (repo, team, org, enterprise) (GitHub docs). Minimum fine-grained or App permission not yet confirmed | V-E04 optional probe |

## Out of scope

- Creating or configuring the App, secrets, or branch protection. The platform team does that; doctor
  tells them what to do.
- Making the optional live probes (V-E03, V-E04) required. They need admin or push access, so they
  run only when a platform token is supplied (D7).
- `stagr init`. It is a separate command ([docs/CLI.md](../../docs/CLI.md)); doctor's checklist is what
  makes init's output actionable.
