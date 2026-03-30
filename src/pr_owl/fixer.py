"""Auto-fix for BEHIND PRs."""

from __future__ import annotations

import logging

from pr_owl import gh
from pr_owl.models import BlockerType, FixResult, RemediationPlan

logger = logging.getLogger(__name__)


def fix_pr(plan: RemediationPlan, dry_run: bool = False) -> FixResult:
    """Fix a PR if it has a BEHIND_BASE blocker. Skip everything else."""
    pr = plan.report.pr
    blocker_types = {b.type for b in plan.report.blockers}

    if BlockerType.BEHIND_BASE not in blocker_types:
        return FixResult(
            pr=pr,
            skipped=True,
            reason=f"No BEHIND_BASE blocker (status: {plan.report.status.value})",
        )

    cmd_str = f"gh pr update-branch {pr.number} -R {pr.repo} --rebase"

    if dry_run:
        return FixResult(
            pr=pr,
            skipped=True,
            reason="dry run",
            command_run=cmd_str,
        )

    logger.info("Fixing %s#%d: updating branch...", pr.repo, pr.number)
    return gh.update_branch(pr, rebase=True)
