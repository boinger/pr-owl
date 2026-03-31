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
    result = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GhAuthError(f"gh is not authenticated. Run 'gh auth login' first.\n{result.stderr}")


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command and return the CompletedProcess. Centralizes subprocess usage."""
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True)


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

    data = json.loads(result.stdout) if result.stdout.strip() else []

    if len(data) == limit:
        logger.warning("Search returned exactly %d results — results may be truncated.", limit)

    return data


# gh pr view fields
_VIEW_FIELDS = (
    "number,title,url,isDraft,mergeStateStatus,mergeable,reviewDecision,"
    "headRefName,baseRefName,headRepository,headRepositoryOwner,statusCheckRollup"
)


def view_pr(number: int, repo: str) -> dict:
    """Get detailed PR info via gh pr view."""
    cmd = ["gh", "pr", "view", str(number), "-R", repo, "--json", _VIEW_FIELDS]

    result = _run(cmd)
    _check_errors(cmd, result)

    return json.loads(result.stdout)


def get_current_user() -> str:
    """Get the authenticated GitHub username."""
    cmd = ["gh", "api", "user", "-q", ".login"]
    result = _run(cmd)
    if result.returncode != 0:
        raise GhAuthError(f"Failed to get current user.\n{result.stderr}")
    return result.stdout.strip()
