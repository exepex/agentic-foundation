# Stagr Neutral Core — GitHub + Codex Implementation Mapping

**Status:** Design phase — not yet implemented

---

## Overview

This document maps the neutral architecture objects to the current GitHub + Codex
implementation, identifies where the current implementation deviates from the contract,
and defines what the correct implementation looks like.

> This document describes the **current state** and the **target state**. It is an
> input to the implementation work, not a specification of the neutral core itself.

---

## Neutral-to-GitHub object mapping

| Neutral concept | GitHub implementation |
|---|---|
| `StageTrigger.PR_OPENED` | `pull_request_target: [opened, reopened, ready_for_review]` |
| `StageTrigger.PR_UPDATED` | `pull_request_target: [synchronize]` |
| `InvocationKind.PR_COMMENT` | `gh pr comment <pr> --body-file <file>` via CODEX_PAT |
| `EvidenceKind.REVIEW_RESULT` | Codex bot comment containing `codex-pull-request-review-summary` |
| `EvidenceKind.COMMENT_MATCH` | PR comment from the PAT account containing the in-flight marker |
| `StageResultSignal` | Check Run created by the Stagr GitHub App with name `stagr/stage/<stageId>` (target); commit status not permitted on GitHub V1 |
| `RouteClassification` | Check Run created by the Stagr GitHub App (target); commit status with context `Publish fast review result` (current — to be migrated) |
| `TrustPolicy.trustedRoles` | `author_association` ∈ `["OWNER","MEMBER","COLLABORATOR"]` |
| `TrustPolicy.requireSameRepo` | `head.repo.full_name == GITHUB_REPOSITORY` check |
| `TrustPolicy.humanMergeLabel` | `human-merge` label |
| Foundation lane (merge when all gate conditions hold) | Auto-merge via `auto-merge-foundation-prs.yml` |
| Human-gated lane | `human-merge` label present → gate stops |

---

## Current workflow → artifact class mapping

| Current workflow file | Artifact class | Stage |
|---|---|---|
| none: the Codex App reviews every new commit by itself | Stage execution artifact | `review` |
| `request-final-security-review.yml` | Stage execution artifact | `security` |
| `fast-ai-code-review.yml` | Routing artifact | (pipeline-level) |
| `auto-merge-foundation-prs.yml` | Governance / merge artifact | (pipeline-level) |

---

## Review order: security declares its dependency on review

### What the config declares

```yaml
stages:
  - id: review
    type: review
    depends_on: []

  - id: security
    type: security
    depends_on: [review]      # starts after the code review has passed
```

The `standard` profile declares this dependency (`09-check-stages.md`, section 10), and so does
this repository's own `.agentic/config.yml`, so the renderer enforces it.

### What the current implementation does

`request-final-security-review.yml` contains a hand-written wait:

```bash
code_row="$(grep -i 'Code Review' <<<"$summary" | head -1 || true)"
grep -qi 'Completed' <<<"$code_row" \
  || { echo "code review is not yet completed; skipping."; return 0; }
code_sha="$(grep -oE '[0-9a-f]{7,40}' <<<"$code_row" | head -1 | tr -d '\`' || true)"
[[ -n "$code_sha" && "$head_sha" == "$code_sha"* ]] \
  || { echo "code review is bound to a different head; skipping."; return 0; }
```

Before the dependency was declared, this violated **Renderer Invariant R1**: a renderer must
not enforce a dependency between two stages unless it is declared in
`NormalizedStage.dependencies`. With `security.dependencies = [review]` the same ordering is
declared, and the generated stage enforces it through the dependency rule (start only when
`review` is `COMPLETED` + `PASS` for the head) instead of by reading Codex comment rows.

Whether the Codex backend truly fails on a concurrent code and security review has not been
isolated (`07-validation.md`), and OpenAI's documentation describes both reviews as
independently triggerable. The dependency is therefore a default, not a proof: a repository
that removes it gets two independent stages, which the next section describes.

---

## Target design: stage execution artifacts

Both stage execution artifacts trigger on every push to an eligible PR. With the declared
dependency, the security artifact then waits until `review` has passed for the head; a
repository that removes the dependency has two fully independent artifacts.

### Declared StageTriggers vs reconciliation events

There is an important distinction in the GitHub implementation between two categories of
workflow triggers:

| Category | GitHub event | What it means |
|---|---|---|
| **Declared StageTrigger** | `pull_request_target: [synchronize]` | Maps to `StageTrigger.PR_UPDATED`; starts a new invocation |
| **Reconciliation event** | `issue_comment: [created, edited]` | Wakes the workflow to check if a pending invocation has completed |
| **Reconciliation event** | `check_suite: [completed]` | Wakes the workflow to check if a pending invocation has completed |

`issue_comment` and `check_suite` events do **not** map to any `StageTrigger` value.
They are renderer-internal wakeups used by the reconciliation loop to observe backend
completion after an invocation has been posted. They never cause a new invocation to be
posted on their own. See `06-runtime-boundary.md` for the reconciliation model.

### review stage execution artifact

```
Declared triggers (StageTrigger.PR_OPENED + StageTrigger.PR_UPDATED):
  pull_request_target [opened, reopened, ready_for_review]  ← PR_OPENED
  pull_request_target [synchronize]                          ← PR_UPDATED
    → Resolve PR, enforce TrustPolicy, check idempotency, check routing
    → If all pass: post @codex review with in-flight marker
    → Emit StageResultSignal (state=RUNNING, conclusion=UNKNOWN)

Reconciliation events (implementation detail, not StageTrigger):
  issue_comment [created, edited] OR check_suite [completed]
    → Resolve PR, check EvidenceSpec for headSha
    → If evidence found: evaluate GateDispositionSpec, emit updated StageResultSignal
    → (state=COMPLETED, conclusion=PASS|BLOCKED)
```

### security stage execution artifact

```
Same structure as review.
Declared triggers: pull_request_target [opened, reopened, ready_for_review, synchronize]
  (PR_OPENED → opened/reopened/ready_for_review; PR_UPDATED → synchronize)
Reconciliation events: issue_comment [created, edited], check_suite [completed]
```

Both artifacts fire on the same declared StageTrigger events (`PR_OPENED` and
`PR_UPDATED`). With `depends_on: [review]` the security artifact's eligibility check
(below) waits until `review` has passed; without it neither waits for the other.

### Codex Evidence path

The current Codex `@codex security review` invocation via PR comment may not reliably
update the Security Review row in the Codex summary comment — this was the reason the
current implementation uses the comment-ordering fallback. The EvidenceSpec for the
security stage must describe an evidence path that actually works for this invocation
method. **Until this is verified empirically, the EvidenceSpec for the security stage
should not assume the Codex summary row is reliably updated by a PR-comment–triggered
security review.**

Options to investigate:
1. Does `@codex security review` posted as a PR comment update the Codex summary row?
   If yes, use `REVIEW_RESULT` evidence.
2. If not, does the Codex bot post a separate completion comment? Use `COMMENT_MATCH`
   evidence targeting that comment's format.
3. Is there a native Codex Security Review configuration (not comment-triggered) that
   produces reliable summary row updates? If so, use that invocation path and update
   the EvidenceSpec accordingly.

The EvidenceSpec for the security stage must be determined empirically before the stage
execution artifact for security is implemented.

---

## StageResultSignal: what needs to be added

The current implementation does not emit normalized `StageResultSignal` values. The
auto-merge gate (`auto-merge-foundation-prs.yml`) directly reads Codex summary comment
rows instead of normalized signals.

To conform to the architecture:

1. Each stage execution artifact must emit a `StageResultSignal` as a **Check Run**
   (not a commit status) after evaluating its EvidenceSpec. The Check Run carries the
   Stagr GitHub App's publisher identity; the governance artifact verifies the App ID
   matches `StageResultSpec.provenance.publisherIdentity` before trusting the result.
2. The governance artifact (`auto-merge-foundation-prs.yml`) must read these Check Runs
   (verifying publisher identity) instead of Codex comment rows.
3. This decouples the governance artifact from Codex-specific output formats and makes
   it work correctly with any future backend.

---

## Reconciliation and result signaling (GitHub renderer)

This is how the generated `stage-<id>.yml` implements the reconciliation model in
`06-runtime-boundary.md`.

- **Jobs.** One file per stage. `execute` runs on the stage's declared triggers and, only for a
  stage that declares dependencies, on the `check_run` / `check_suite` wake-ups described under
  "Dependency wake-ups" below. `reconcile` runs on `issue_comment` events for a pull request, and
  only when the comment author is a declared evidence producer. `sweep` runs on a schedule (every 5
  minutes) and runs the same routine for every open pull request. Every stage has `reconcile`
  and `sweep`, because every plan must declare evidence (see below). Each job has an explicit `github.event_name` condition, so
  a wakeup never re-runs the backend, and `synchronize` is never a wakeup. `check_suite` is not
  used to observe evidence in V1: that serves check-based evidence, which V1 rejects (see below).
- **State is observed, not remembered.** Every run re-reads the pull request, its comments, its
  review threads and the stage's Check Run for the current head. Missed, repeated or reordered
  events therefore cannot produce a wrong signal; the sweep is only a backstop.
- **One Check Run per stage and head.** Only the `execute` job creates it. `reconcile` and
  `sweep` update it in place. Governance rejects duplicates and cannot repair them, so creation
  has a single owner that is already serialized by the stage's concurrency group.
- **Terminal states.** `completed` + `pass` and `failed` are final for wakeups and the sweep. A
  `failed` signal is retried only by re-running the stage's `execute` job. `blocked` is
  re-evaluated on every wakeup, so resolving threads turns it into `pass` without a new push. A
  write happens only when the signal changed.
- **Evidence is authenticated.** Only comments written by `EvidenceSpec.produced_by` count. A
  login ending in `[bot]` matches only a Bot account, never a person with a similar name.
  Evidence must also be bound to the current head commit.
- **Findings.** For `NO_OPEN_THREADS`, an unresolved thread counts when its first comment is by
  `FindingScopeSpec.created_by` and, if the scope is head-bound, when its review commit is the
  current head. A thread whose review commit is unknown counts as open (fail closed).
- **Rejected at render time** (`stagr apply` fails; nothing weaker is generated): evidence kinds
  other than `REVIEW_RESULT` and `COMMENT_MATCH`; evidence that is not head-bound or has no
  `produced_by`; `invocation_correlation`; any invocation kind other than `PR_COMMENT`; and plans
  with no evidence, because a `PR_COMMENT` invocation finishes asynchronously and nothing else
  could prove it finished.
- **Events without a pull request** (`workflow_dispatch`, `issues`) publish no signal.
- **Invocation and idempotency (`PR_COMMENT` backends).** The `execute` job has one step,
  "Invoke backend (idempotent)", that runs the same runtime in `invoke` mode. In this order it
  (1) skips if the pull request is not eligible or the event's head is stale; (2) skips if the
  `EvidenceSpec` already holds for the current head (completion guard); (3) skips if a still-valid
  in-flight marker exists for this stage and this exact head; (4) otherwise posts the backend
  comment (`Invocation.params["body"]`) with the marker
  `<!-- stagr:stage:<stageId>:<headSha>:expires:<UTC ISO8601> -->` appended. A skipped step exits
  successfully, so the "Publish result signal" step still runs and reports `running` or the
  completed result. This step runs inside the stage's concurrency group.
- **The in-flight marker is authenticated.** Comments on a public repository are written by
  anyone, so a forged far-future marker could otherwise stop an invocation for ever. A marker
  counts only if the comment's author is the account that owns the invoke token (its id, login
  and type come from `GET /user` at run time and must all match, so a look-alike name or a Bot
  twin never matches) and the comment's `author_association` is one of the `TrustPolicy` roles.
  The marker must also name this exact stage id and the full 40-character head SHA. Markers
  without an expiry, with an unparseable expiry, or for another stage or head are ignored, and an
  expiry that is not in the future counts as expired. Consequently the account behind
  `TRUSTED_COMMENTER_TOKEN` must have a role listed in `TrustPolicy.trusted_roles`; otherwise its
  markers are never believed and each run posts again.
- **Lease length** is `Invocation.params["lease_minutes"]` (backend-defined; 30 when absent),
  checked at render time: an integer from 1 to 1440, anything else fails `stagr apply`. `body`
  must be non-empty text and the plan must declare a resolved `TRUSTED_COMMENTER_TOKEN` secret.
- **Credentials.** Only the invoke step holds the backend secret. It reaches the runtime as
  `TRUSTED_COMMENTER_TOKEN`, and the runtime hands it to `gh` as `GH_TOKEN` for that step only.
  The App installation token is never present in the invoke step; the eligibility step,
  `reconcile` and `sweep` hold only the App token (`reconcile` and `sweep` with
  `permissions: {}`).
- **Eligibility.** The first step after the token is "Check eligibility". It runs the runtime in
  `eligibility` mode and writes `proceed=true` or `proceed=false` to the step output; the invoke
  step runs only when it is `true`, and a failed eligibility step also stops it. The same eligibility code runs in `publish`,
  `reconcile` and `sweep`, so no mode can act on a pull request another mode refused. In order, the
  first failing check decides: (1) the pull request is open, not a draft, written by a trusted
  role, not a fork the fork policy refuses (`ForkPolicy.DENY`, or a privileged stage), and the
  event's head is still the pull request's current head; (2) route applicability; (3) for a
  wake-up, the stage's own signal is not already final; (4) dependencies. An ineligible run
  invokes nothing and writes no signal. The "Publish result signal" step still runs and repeats
  the same checks, so an ineligible run publishes nothing. The invoke step keeps its own
  pull-request checks (it has no App token, so it cannot read Check Runs); route and dependencies
  are decided once by the eligibility step just before it.
- **Route applicability.** When `RoutingPolicy.fast_path` is configured, the rendered
  configuration carries the stage ids of the FAST and NORMAL routes, and the runtime reads the
  `stagr/route-classification` Check Run for the current head. It is trusted only if the Stagr
  App wrote it, it is bound to the head, it is completed, and its title is exactly
  `RouteClassification=FAST` or `RouteClassification=NORMAL`; two Stagr runs are an error, and
  a run from another app is ignored. A stage that is not listed for the route does not run. The
  routing workflow starts at the same moment as the stage workflow, so a classification that is
  still missing is waited for (up to 3 minutes, only in the eligibility step); after that the stage
  fails closed and starts on its next execute run.
- **Dependencies.** For each stage in `NormalizedStage.dependencies` the runtime reads that
  stage's Check Run (`stagr/stage/<id>`) for the current head. It counts only if it is the single
  Check Run of that name written by the Stagr App, and the JSON in `output.summary` has
  `schemaVersion` 1 and states the same stage id and head SHA. `state` and `conclusion` come from
  that payload, never from the native Check Run fields. The stage starts only when every
  dependency is `completed` + `pass`. A dependency that is missing, unreadable, for another head,
  running, blocked or `failed` means "not yet": nothing is invoked and nothing is written, and the
  stage starts when the dependency later passes (`02-canonical-stage-model.md`). Two Stagr runs for
  one dependency are an error and nothing is written.
- **Dependency wake-ups.** A stage with dependencies also subscribes to `check_run: completed`
  and `check_suite: completed`, so it starts when the upstream signal first passes, without a new
  push. These events fire for every check in the repository, so the `execute` job's `if:` lets
  through only a `check_run` written by the Stagr App for one of the upstream stage Check Runs,
  or a `check_suite` written by the Stagr App, and only when the payload names a pull request of
  this repository. The stage's own Check Run (`stagr/stage/<its id>`) is not upstream, so writing
  its own signal cannot wake it through `check_run`. A Stagr `check_suite` does follow every Stagr
  write, but a wake-up that changes nothing writes nothing, so it ends there. A wake-up run
  differs from a pull request run in one way: a stage whose own signal is already `pass` or
  `failed` is left alone. `failed` is terminal for wake-ups and the sweep; only a re-run of the
  execute job (a `pull_request_target` event or a manual re-run) retries, otherwise unrelated
  Check Run events could re-run a failed paid backend in a loop. The pull request number and head
  come from the event payload (`pull_requests[0]`, `head_sha`) and are checked against the API
  like any other event, so a wake-up about a superseded head does nothing.
- **Concurrency of wake-ups.** A relevant wake-up uses the same concurrency group as the pull
  request's other events (`stagr-<id>-<pull request number>`), so the execute job stays the only,
  serialized creator of the Check Run. An irrelevant `check_run` / `check_suite` event gets a group
  of its own (the run id): with `cancel-in-progress: false` GitHub keeps one pending run per group
  and replaces it with the next one, so an unrelated event in the shared group could push out a
  real wake-up. Two relevant events can still replace one another; that is harmless because every
  run reads the current state instead of trusting its event. Every check in the repository still
  starts a run of each dependent stage's workflow, which is skipped by the `if:` conditions.
- **Known limitation: the sweep cannot start a dependent stage.** The sweep re-reads the upstream
  signals of every open pull request, so it sees an upstream `blocked` -> `pass` flip (which may
  raise no `check_run` event). It uses them to complete an existing signal and to leave it alone
  while an upstream has not passed. It cannot start a stage
  that has not started, because starting needs the backend secret and creating the Check Run,
  and the sweep holds neither. Such a stage starts the next time its execute job runs: a wake-up
  event of an upstream signal, a reopen, `ready_for_review`, or a manual re-run of the workflow. The
  sweep logs "dependencies have passed but the stage has not started" for it.
- **Known limitation: fork pull requests and wake-ups.** The Check Run payload names no pull
  request for a fork, so a fork pull request (allowed only for a non-privileged stage under
  `ForkPolicy.ALLOW_UNPRIVILEGED`) is not woken by upstream signals. Its dependent stage starts
  only if the upstream had already passed when a pull request event or a manual re-run arrived.
- **Known limitation: the sweep cannot re-invoke.** The sweep job must not hold the backend
  secret, so it never posts an invocation. If a backend drops an invocation and the lease expires,
  the pull request stays `running` until the `execute` job next runs for that same head (a
  reopen, `ready_for_review`, or a manual re-run of the workflow). A new push starts a new head
  and is invoked normally. This narrows the recovery rule in `06-runtime-boundary.md`, which
  allows the sweep to re-post.
- **`CI_COMPONENT` invocations.** The GitHub renderer also renders a `CI_COMPONENT`, limited to the
  components it knows (today `openai-codex-review`, the `codex-api` backend); any other kind or
  component is rejected by V-S08 (`07-validation.md`) or by the renderer. Such a stage's `execute` is
  split so that no job holds both the App token and the backend credential: `execute` (App token)
  runs eligibility and then `ci_gate`, which allows one run per eligible head and none after the
  hand-off label; `review` (backend credential, checkout without credentials, no App token) runs the
  component; `publish` (App token) posts the findings as one review and a completion marker
  (`post_review`), then publishes the signal. The marker is written to satisfy the stage's own
  `COMMENT_MATCH` evidence rule, and the review and marker are authored by the App
  (`platform.publisher.app_slug`), so findings and evidence come from an account no person can
  impersonate. The CI-component code lives in `runtime/ci_component_runtime.py`, embedded only in
  such stages, which runs the shared runtime script as a module rather than copying it.
- **Stagr App permissions** used at run time: see `docs/CONFIGURATION.md`, "Publisher" (a
  `CI_COMPONENT` stage also posts its review and marker as the App).

---

## Where the review order lives

The order of the two reviews is declared in the neutral config
(`security` with `depends_on: [review]`, from the `standard` profile) and enforced by the rendered
stage through the dependency rule. Nothing in the rendered artifacts orders the reviews on its
own.

---

## Summary of changes needed in the implementation

| Item | Change required |
|---|---|
| `request-final-security-review.yml` | Replace the hand-written code-review-completion wait with the declared dependency (`security` starts once `review` is `PASS` for the head). Add `pull_request_target: [opened, reopened, ready_for_review, synchronize]` triggers (PR_OPENED + PR_UPDATED). |
| `request-final-security-review.yml` | Add in-flight idempotency marker with lease (`<!-- stagr:stage:<id>:<sha>:expires:<time> -->`). Emit `StageResultSignal` as Check Run (not commit status); verify publisher App identity in governance. |
| `auto-merge-foundation-prs.yml` | Read `StageResultSignal` Check Runs (verify publisher identity) instead of Codex summary comment rows. Routing signal also migrated to Check Run. |
| New: provider configuration | Add secret alias → platform secret name mapping (TRUSTED_COMMENTER_TOKEN → REMEDIATION_TOKEN) to provider config. |
| Verify empirically | Test whether `@codex security review` PR comment reliably updates the Codex summary Security Review row before implementing the EvidenceSpec. |
