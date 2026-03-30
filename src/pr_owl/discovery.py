"""PR discovery and filtering."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from pr_owl import gh
from pr_owl.models import PRInfo

logger = logging.getLogger(__name__)


def discover_prs(
    author: str = "@me",
    repo: str = "",
    org: str = "",
    limit: int = 100,
) -> list[PRInfo]:
    """Discover open PRs for the given author."""
    results = gh.search_prs(author=author, state="open", repo=repo, org=org, limit=limit)
    return [PRInfo.from_search_result(r) for r in results]


def filter_stale(prs: list[PRInfo], days: int) -> list[PRInfo]:
    """Keep only PRs that haven't been updated in at least `days` days."""
    cutoff = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
    return [pr for pr in prs if pr.updated_at_dt < cutoff]
