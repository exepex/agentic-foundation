"""Review-thread resolution workflow YAML generator for the GitHub platform renderer.

Generates the Phase 2c artifact: a GitHub Actions workflow that, on every new commit of a pull
request, resolves the finding threads that are done:
- a thread a later commit has made outdated, when every comment in it was written by a review
  backend of the config's stages (for example the Codex bot);
- with remediation on, also a thread the remediation agent answered with its fixed marker (a fix
  does not always touch the lines the finding points at, and the review backend cannot resolve its
  own threads).
A thread with any other comment, a human's or the agent's reasoned decline, is never touched, and a
finding that is still real is raised again by the backend's review of the new commit.

Security invariants:
- ``pull_request_target`` runs the default-branch workflow; nothing from the pull request is checked
  out or executed, and the job performs only GitHub API calls.
- Only same-repository pull requests from a trusted author association reach the token.
- The resolving token is the platform token secret (``platform.auth.token_secret``, default
  ``REMEDIATION_TOKEN``), referenced by name; the workflow token gets no permissions.
- A stale event (the head moved since it fired) resolves nothing; the newer commit's run does.
"""
from __future__ import annotations

import json

from stagr.core.models import RenderContext
from stagr.platforms.github.remediation_workflow import FINDING_FIXED_MARKER, REMEDIATION_REPLY_LOGINS

# Output file name inside ``.github/workflows/``.
THREAD_RESOLUTION_WORKFLOW_FILENAME = "resolve-outdated-threads.yml"

# GitHub appends this suffix to a GitHub App's REST login; GraphQL review-thread authors omit it.
_BOT_LOGIN_SUFFIX = "[bot]"

# Width of the indentation block for the ``run: |`` script in the generated YAML.
_YAML_SCRIPT_LINE_INDENT = "          "


def generate_thread_resolution_workflow_yaml(
    finding_authors: tuple[str, ...],
    token_secret: str,
    render_context: RenderContext,
) -> str:
    """Return the workflow YAML that resolves outdated threads written only by ``finding_authors``.

    Args:
        finding_authors: Logins of the review backends whose threads are resolved once outdated.
        token_secret: Name of the repository secret holding the token that resolves threads.
        render_context: Supplies the trusted author associations of the trust policy.
    """
    trusted_associations = json.dumps(
        sorted(role.value.upper() for role in render_context.trust_policy.trusted_roles)
    )
    thread_author_logins = json.dumps(
        sorted({author.removesuffix(_BOT_LOGIN_SUFFIX) for author in finding_authors})
    )
    fixed_reply_logins = json.dumps(
        list(REMEDIATION_REPLY_LOGINS) if render_context.remediation_policy else []
    )
    indented_script = "".join(
        f"{_YAML_SCRIPT_LINE_INDENT}{line}\n" if line.strip() else "\n"
        for line in _RESOLUTION_SCRIPT.splitlines()
    )
    return (
        'name: "Stagr resolve outdated review threads"\n'
        "\n"
        "on:\n"
        "  pull_request_target:\n"
        "    types: [opened, reopened, synchronize]\n"
        "  # The review of a fix commit arrives after the agent's fixed replies, so it resolves them.\n"
        "  pull_request_review:\n"
        "    types: [submitted]\n"
        "\n"
        "# Every API call uses the token secret below; the workflow token needs nothing.\n"
        "permissions: {}\n"
        "\n"
        "concurrency:\n"
        '  group: "stagr-resolve-threads-${{ github.event.pull_request.number }}"\n'
        "  cancel-in-progress: true\n"
        "\n"
        "jobs:\n"
        "  resolve-outdated-threads:\n"
        "    if: >-\n"
        "      github.event.pull_request.head.repo.full_name == github.repository &&\n"
        f"      contains(fromJSON('{trusted_associations}'), github.event.pull_request.author_association)\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 5\n"
        "    steps:\n"
        "      - name: Resolve finding threads that are done\n"
        "        env:\n"
        f'          GH_TOKEN: "${{{{ secrets.{token_secret} }}}}"\n'
        '          PR_NUMBER: "${{ github.event.pull_request.number }}"\n'
        '          EVENT_HEAD_SHA: "${{ github.event.pull_request.head.sha }}"\n'
        f"          THREAD_AUTHOR_LOGINS: '{thread_author_logins}'\n"
        f"          FIXED_REPLY_LOGINS: '{fixed_reply_logins}'\n"
        f"          FIXED_MARKER: {json.dumps(FINDING_FIXED_MARKER)}\n"
        "        run: |\n"
        f"{indented_script}"
    )


_RESOLUTION_SCRIPT = """\
set -euo pipefail
current_head_sha="$(gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" --jq '.head.sha')"
if [[ "${current_head_sha}" != "${EVENT_HEAD_SHA}" ]]; then
  echo "The pull request head moved since this event; the newer commit's run resolves threads."
  exit 0
fi
thread_query='query($owner:String!, $repo:String!, $number:Int!, $cursor:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      reviewThreads(first:100, after:$cursor) {
        nodes { id isResolved isOutdated comments(first:100) { nodes { author { __typename login } body } } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}'
cursor_arguments=()
while :; do
  response="$(gh api graphql -f query="${thread_query}" -f owner="${GITHUB_REPOSITORY%%/*}" \\
    -f repo="${GITHUB_REPOSITORY#*/}" -F number="${PR_NUMBER}" "${cursor_arguments[@]}")"
  # An unresolved thread is done when it is outdated or the remediation agent marked it fixed, and
  # every comment is a review backend's finding or the agent's fixed reply (never a decline).
  mapfile -t thread_ids < <(jq -r --argjson authors "${THREAD_AUTHOR_LOGINS}" \
    --argjson fixers "${FIXED_REPLY_LOGINS}" --arg marker "${FIXED_MARKER}" '
    # GraphQL drops the "[bot]" suffix, so only a Bot actor may match: a human named like a bot never does.
    def bot_in($logins): .author.__typename == "Bot" and ((.author.login // "") as $login | $logins | index($login));
    def fixed_reply: bot_in($fixers) and ((.body // "") | contains($marker));
    def finding: bot_in($authors);
    .data.repository.pullRequest.reviewThreads.nodes[]
    | select(.isResolved == false)
    | select((.comments.nodes | length) > 0)
    | select(.isOutdated or any(.comments.nodes[]; fixed_reply))
    | select(all(.comments.nodes[]; finding or fixed_reply))
    | .id' <<< "${response}")
  for thread_id in "${thread_ids[@]}"; do
    gh api graphql -f threadId="${thread_id}" \\
      -f query='mutation($threadId:ID!) { resolveReviewThread(input:{threadId:$threadId}) { thread { id } } }' \\
      >/dev/null
    echo "Resolved review thread ${thread_id}."
  done
  [[ "$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage' <<< "${response}")" == "true" ]] \\
    || break
  cursor_arguments=(-f cursor="$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor' <<< "${response}")")
done
"""
