"""gh CLI subprocess wrapper — the only module that touches subprocess."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

from pr_owl.exceptions import (
    GhAuthError,
    GhCommandError,
    GhNotFoundError,
    GhRateLimitError,
    PrNotFoundError,
    PrOwlError,
)

logger = logging.getLogger(__name__)


def ensure_gh() -> str:
    """Find gh binary on PATH or raise GhNotFoundError."""
    path = shutil.which("gh")
    if path is None:
        raise GhNotFoundError("gh CLI not found on PATH. Install from https://cli.github.com/")
    return path


def check_auth() -> None:
    """Verify gh is authenticated or raise GhAuthError."""
    result = _run(["gh", "auth", "status"])
    if result.returncode != 0:
        raise GhAuthError(f"gh is not authenticated. Run 'gh auth login' first.\n{result.stderr}")


_SUBPROCESS_TIMEOUT = 30  # seconds


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command and return the CompletedProcess. Centralizes subprocess usage."""
    logger.debug("Running: %s", " ".join(cmd))
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise GhCommandError(cmd, -1, f"Command timed out after {_SUBPROCESS_TIMEOUT}s") from exc


def _check_errors(cmd: list[str], result: subprocess.CompletedProcess[str]) -> None:
    """Check for common gh errors and raise appropriate exceptions."""
    if result.returncode == 0:
        return
    stderr = result.stderr.strip()
    if "rate limit" in stderr.lower():
        raise GhRateLimitError(f"GitHub API rate limit exceeded.\n{stderr}")
    if "could not resolve" in stderr.lower() or "not found" in stderr.lower():
        raise PrNotFoundError(f"PR or repository not found.\n{stderr}")
    raise GhCommandError(cmd, result.returncode, stderr)


# gh search prs fields
_SEARCH_FIELDS = "number,title,url,repository,isDraft,createdAt,updatedAt"


def search_prs(
    author: str = "@me",
    state: str = "open",
    repo: str = "",
    org: str = "",
    limit: int = 100,
) -> list[dict]:
    """Search for PRs authored by the given user."""
    cmd = ["gh", "search", "prs", "--author", author, "--state", state, "--limit", str(limit), "--json", _SEARCH_FIELDS]
    if repo:
        cmd.extend(["--repo", repo])
    if org:
        cmd.extend(["--owner", org])

    result = _run(cmd)
    _check_errors(cmd, result)

    try:
        data = json.loads(result.stdout) if result.stdout.strip() else []
    except json.JSONDecodeError as exc:
        raise PrOwlError(f"Malformed JSON from 'gh search prs': {exc}") from exc

    if len(data) == limit:
        logger.warning("Search returned exactly %d results — results may be truncated.", limit)

    return data


# gh search prs --state closed fields
# Must include updatedAt for PRInfo compatibility. `state` gives disposition
# ("merged" or "closed") without needing mergedAt (which gh search doesn't have).
_CLOSED_SEARCH_FIELDS = "number,title,url,closedAt,createdAt,updatedAt,repository,state,isDraft,commentsCount"


def search_closed_prs(
    author: str = "@me",
    since: str = "",
    repo: str = "",
    org: str = "",
    limit: int = 100,
) -> list[dict]:
    """Search for closed PRs authored by the given user, optionally since a date.

    The ``since`` parameter is a YYYY-MM-DD date string (already validated by the
    caller). It is passed as a ``closed:>=DATE`` search qualifier. GitHub search
    uses day granularity, so the caller must client-side filter for precision.
    """
    cmd = [
        "gh",
        "search",
        "prs",
        "--author",
        author,
        "--state",
        "closed",
        "--limit",
        str(limit),
        "--json",
        _CLOSED_SEARCH_FIELDS,
    ]
    if since:
        cmd.append(f"closed:>={since}")
    if repo:
        cmd.extend(["--repo", repo])
    if org:
        cmd.extend(["--owner", org])

    result = _run(cmd)
    _check_errors(cmd, result)

    try:
        data = json.loads(result.stdout) if result.stdout.strip() else []
    except json.JSONDecodeError as exc:
        raise PrOwlError(f"Malformed JSON from 'gh search prs --state closed': {exc}") from exc

    if len(data) == limit:
        logger.debug("Closed PR search returned exactly %d results — may be truncated.", limit)

    return data


# GraphQL query for view_pr. Single round-trip replaces `gh pr view --json` so
# we can fetch totalCommentsCount (the canonical count GitHub uses in /pulls)
# alongside everything else. Paginated edges use `first: 100`, which is more
# than any real PR will hit — if a PR exceeds this we'll see truncation in
# tests and can paginate properly later.
_VIEW_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number
      title
      url
      isDraft
      mergeable
      mergeStateStatus
      reviewDecision
      headRefName
      baseRefName
      headRepository { name }
      headRepositoryOwner { login }
      totalCommentsCount
      reviews(first: 100) {
        totalCount
        nodes { state author { login } body submittedAt }
      }
      commits(last: 1) {
        nodes {
          commit {
            statusCheckRollup {
              state
              contexts(first: 100) {
                nodes {
                  __typename
                  ... on CheckRun { name conclusion status detailsUrl }
                  ... on StatusContext { context state targetUrl }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def _split_repo(repo: str) -> tuple[str, str]:
    """Split 'owner/name' into ('owner', 'name'). Raises on malformed input."""
    parts = repo.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise PrOwlError(f"Malformed repo identifier '{repo}'; expected 'owner/name'.")
    return parts[0], parts[1]


def _coerce_int(value: object, default: int = 0) -> int:
    """Coerce a GraphQL scalar to int, defaulting on null/wrong-type."""
    if isinstance(value, bool):  # bool is a subclass of int — exclude.
        return default
    if isinstance(value, int):
        return value
    return default


def view_pr(number: int, repo: str) -> dict:
    """Get detailed PR info via `gh api graphql`.

    Returns a dict in the same shape `gh pr view --json` previously returned,
    so callers (checker.py, cli._enrich_closed_prs) work unchanged. Notably
    `reviews` is a list[dict] (not totalCount), preserving `len()` compat.

    Failure modes:
    - Subprocess error or non-zero exit → `_check_errors` raises GhCommandError/etc.
    - GraphQL `errors` envelope (200 OK with errors array) → GhCommandError.
    - Missing `data.repository.pullRequest` → PrNotFoundError.
    - Null/missing scalars → coerced to defaults (0 / "" / []).
    - Other shape problems → PrOwlError.
    """
    owner, name = _split_repo(repo)
    cmd = [
        "gh",
        "api",
        "graphql",
        # `-f` forces string type — required for owner/name because the GraphQL
        # query declares them as String!, and a numeric repo name (e.g. user/42)
        # under `-F` would auto-detect as Int and trigger a GraphQL type error.
        "-f",
        f"owner={owner}",
        "-f",
        f"name={name}",
        # `-F` typed flag — needed for number because GraphQL declares Int!.
        "-F",
        f"number={number}",
        "-f",
        f"query={_VIEW_QUERY}",
    ]

    result = _run(cmd)
    _check_errors(cmd, result)

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PrOwlError(f"Malformed JSON from gh api graphql: {exc}") from exc

    if not isinstance(envelope, dict):
        raise PrOwlError(f"Unexpected GraphQL envelope shape: {type(envelope).__name__}")

    if envelope.get("errors"):
        raise GhCommandError(cmd, 0, f"GraphQL errors: {envelope['errors']}")

    try:
        pr = envelope["data"]["repository"]["pullRequest"]
    except (KeyError, TypeError) as exc:
        raise PrOwlError(f"Unexpected GraphQL response shape: {exc}") from exc

    if pr is None:
        raise PrNotFoundError(f"PR {repo}#{number} not found or not accessible.")

    head_repo = pr.get("headRepository") or {}
    head_owner = pr.get("headRepositoryOwner") or {}

    reviews_block = pr.get("reviews") or {}
    review_nodes = reviews_block.get("nodes") if isinstance(reviews_block, dict) else None
    reviews_list: list[dict] = list(review_nodes) if isinstance(review_nodes, list) else []

    # statusCheckRollup is nested under commits.nodes[0].commit. We flatten
    # contexts.nodes into the top-level "statusCheckRollup" key as a list of
    # dicts to match what `gh pr view --json statusCheckRollup` produced.
    rollup_contexts: list[dict] = []
    commits_block = pr.get("commits") or {}
    commit_nodes = commits_block.get("nodes") if isinstance(commits_block, dict) else None
    if isinstance(commit_nodes, list) and commit_nodes:
        first_commit = commit_nodes[0] or {}
        commit_obj = first_commit.get("commit") or {}
        rollup = commit_obj.get("statusCheckRollup") or {}
        contexts_block = rollup.get("contexts") or {}
        ctx_nodes = contexts_block.get("nodes") if isinstance(contexts_block, dict) else None
        if isinstance(ctx_nodes, list):
            rollup_contexts = [n for n in ctx_nodes if isinstance(n, dict)]

    return {
        "number": _coerce_int(pr.get("number")),
        "title": pr.get("title") or "",
        "url": pr.get("url") or "",
        "isDraft": bool(pr.get("isDraft")),
        "mergeable": pr.get("mergeable") or "",
        "mergeStateStatus": pr.get("mergeStateStatus") or "",
        "reviewDecision": pr.get("reviewDecision") or "",
        "headRefName": pr.get("headRefName") or "",
        "baseRefName": pr.get("baseRefName") or "",
        "headRepository": {"name": head_repo.get("name") or ""},
        "headRepositoryOwner": {"login": head_owner.get("login") or ""},
        "totalCommentsCount": _coerce_int(pr.get("totalCommentsCount")),
        "reviews": reviews_list,
        "statusCheckRollup": rollup_contexts,
    }


def compare_refs(repo: str, base: str, head: str) -> dict:
    """Get commit distance between two refs via the compare API.

    Returns {"behind_by": int, "ahead_by": int}.
    For cross-repo PRs, head should be "fork_owner:branch_name".
    """
    cmd = ["gh", "api", f"repos/{repo}/compare/{base}...{head}", "--jq", "{behind_by: .behind_by, ahead_by: .ahead_by}"]
    result = _run(cmd)
    _check_errors(cmd, result)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PrOwlError(f"Malformed JSON from compare API: {exc}") from exc


def get_current_user() -> str:
    """Get the authenticated GitHub username."""
    cmd = ["gh", "api", "user", "-q", ".login"]
    result = _run(cmd)
    if result.returncode != 0:
        raise GhAuthError(f"Failed to get current user.\n{result.stderr}")
    return result.stdout.strip()
