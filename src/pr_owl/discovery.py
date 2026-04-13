"""PR discovery and filtering."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from pr_owl import gh
from pr_owl.exceptions import PrOwlError
from pr_owl.models import ClosedPRInfo, PRInfo

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


def discover_closed_prs(
    author: str = "@me",
    since: datetime | None = None,
    repo: str = "",
    org: str = "",
    limit: int = 100,
) -> list[ClosedPRInfo]:
    """Discover PRs closed since ``since`` for the given author.

    GitHub search uses day-granularity qualifiers (``closed:>=YYYY-MM-DD``),
    so this function applies a client-side filter on the precise ``since``
    datetime to avoid including PRs that closed earlier on the same calendar
    day.
    """
    since_date = since.strftime("%Y-%m-%d") if since else ""
    results = gh.search_closed_prs(author=author, since=since_date, repo=repo, org=org, limit=limit)

    closed = []
    for r in results:
        try:
            info = ClosedPRInfo.from_search_result(r)
        except (PrOwlError, KeyError, AttributeError, TypeError, ValueError):
            logger.warning("Skipping unparseable closed PR result: %s", r.get("url", "?"))
            continue

        # Client-side precision filter: gh search uses day granularity,
        # so we may get results from earlier in the same calendar day.
        if since and info.closed_at:
            try:
                closed_dt = datetime.fromisoformat(info.closed_at.replace("Z", "+00:00"))
                if closed_dt < since:
                    continue
            except ValueError:
                pass  # keep it if we can't parse — better to show than hide

        closed.append(info)

    if len(results) == limit:
        logger.warning(
            "Showing %d most recent closed PRs (search limit reached). "
            "Use a narrower --closed-since window for completeness.",
            limit,
        )

    return closed


def filter_stale(prs: list[PRInfo], days: int) -> list[PRInfo]:
    """Keep only PRs that haven't been updated in at least `days` days."""
    cutoff = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
    return [pr for pr in prs if pr.updated_at_dt < cutoff]
