"""PR health checking and status classification."""

from __future__ import annotations

import logging

from pr_owl import gh
from pr_owl.exceptions import GhCommandError, PrNotFoundError
from pr_owl.models import (
    Blocker,
    BlockerType,
    CICheck,
    HealthReport,
    MergeStatus,
    PRInfo,
)

logger = logging.getLogger(__name__)

# GitHub mergeStateStatus → MergeStatus
_STATUS_MAP: dict[str, MergeStatus] = {
    "CLEAN": MergeStatus.READY,
    "BEHIND": MergeStatus.BEHIND,
    "DIRTY": MergeStatus.CONFLICTS,
    "BLOCKED": MergeStatus.BLOCKED,
    "UNSTABLE": MergeStatus.CI_FAILING,
    "HAS_HOOKS": MergeStatus.READY,
}


def classify_status(merge_state_status: str, is_draft: bool) -> MergeStatus:
    """Map GitHub mergeStateStatus + isDraft to MergeStatus."""
    if is_draft:
        return MergeStatus.DRAFT
    return _STATUS_MAP.get(merge_state_status, MergeStatus.UNKNOWN)


def build_blockers(data: dict) -> list[Blocker]:
    """Inspect all PR fields independently to build the blockers list."""
    blockers: list[Blocker] = []

    merge_state = data.get("mergeStateStatus", "")
    if merge_state == "BEHIND":
        blockers.append(Blocker(type=BlockerType.BEHIND_BASE, description="Branch is behind base branch"))
    if merge_state == "DIRTY":
        blockers.append(Blocker(type=BlockerType.HAS_CONFLICTS, description="Branch has merge conflicts"))

    review = data.get("reviewDecision", "")
    if review == "CHANGES_REQUESTED":
        blockers.append(Blocker(type=BlockerType.CHANGES_REQUESTED, description="Changes requested"))
    elif review == "REVIEW_REQUIRED":
        blockers.append(Blocker(type=BlockerType.MISSING_REVIEWS, description="Review required"))

    checks = _parse_checks(data.get("statusCheckRollup", []))
    failing = [c for c in checks if c.is_failing]
    if failing:
        blockers.append(
            Blocker(
                type=BlockerType.FAILING_CHECKS,
                description=f"{len(failing)} check(s) failing",
                details=[c.name for c in failing],
            )
        )

    if data.get("isDraft", False):
        blockers.append(Blocker(type=BlockerType.IS_DRAFT, description="PR is a draft"))

    # BLOCKED with no other detected blockers → branch protection rules
    if merge_state == "BLOCKED" and not blockers:
        blockers.append(
            Blocker(type=BlockerType.BRANCH_PROTECTION, description="Branch protection rules are blocking merge")
        )

    return blockers


def _parse_checks(rollup: list[dict] | None) -> list[CICheck]:
    """Parse statusCheckRollup into CICheck list, handling both CheckRun and StatusContext."""
    if not rollup:
        return []

    checks: list[CICheck] = []
    for item in rollup:
        typename = item.get("__typename", "")
        if typename == "CheckRun":
            checks.append(
                CICheck(
                    name=item.get("name", ""),
                    status=item.get("status", ""),
                    conclusion=item.get("conclusion"),
                    workflow_name=item.get("workflowName", ""),
                    details_url=item.get("detailsUrl", ""),
                )
            )
        elif typename == "StatusContext":
            state = item.get("state", "")
            checks.append(
                CICheck(
                    name=item.get("context", ""),
                    status=state,
                    conclusion=state,
                    workflow_name="",
                    details_url=item.get("targetUrl", ""),
                )
            )
        else:
            # Unknown type — best effort
            checks.append(
                CICheck(
                    name=item.get("name", item.get("context", "unknown")),
                    status=item.get("status", ""),
                    conclusion=item.get("conclusion", item.get("state")),
                    workflow_name=item.get("workflowName", ""),
                    details_url=item.get("detailsUrl", item.get("targetUrl", "")),
                )
            )
    return checks


def check_pr(pr: PRInfo) -> HealthReport:
    """Check a single PR's health. Raises on unrecoverable errors."""
    try:
        data = gh.view_pr(pr.number, pr.repo)
    except (GhCommandError, PrNotFoundError) as exc:
        logger.warning("Failed to check %s#%d: %s", pr.repo, pr.number, exc)
        return HealthReport(
            pr=pr,
            status=MergeStatus.UNKNOWN,
            error=str(exc),
        )

    merge_state = data.get("mergeStateStatus", "")
    is_draft = data.get("isDraft", False)
    mergeable = data.get("mergeable", "")

    status = classify_status(merge_state, is_draft)
    blockers = build_blockers(data)
    checks = _parse_checks(data.get("statusCheckRollup", []))

    # `gh pr view` may return JSON null for these fields (deleted fork, etc).
    # `data.get("headRepository", {})` returns None (not the default) when the
    # key exists with value null, so chain `or {}` to survive that path.
    head_owner_obj = data.get("headRepositoryOwner") or {}
    head_repo_obj = data.get("headRepository") or {}
    head_repo_owner = head_owner_obj.get("login", "")
    head_repo_name = head_repo_obj.get("name", "")
    head_repo = f"{head_repo_owner}/{head_repo_name}" if head_repo_owner and head_repo_name else ""

    report = HealthReport(
        pr=pr,
        status=status,
        blockers=blockers,
        mergeable=mergeable,
        merge_state_status=merge_state,
        review_decision=data.get("reviewDecision", ""),
        checks=checks,
        head_ref=data.get("headRefName", ""),
        base_ref=data.get("baseRefName", ""),
        head_repo=head_repo,
    )

    if mergeable == "UNKNOWN":
        logger.warning("%s#%d: mergeable state is UNKNOWN — GitHub may still be computing.", pr.repo, pr.number)
        if not report.error:
            report.error = "Mergeable state is UNKNOWN — GitHub may still be computing."

    return report
