"""Remediation plan generation from health reports."""

from __future__ import annotations

from pr_owl.models import (
    BlockerType,
    HealthReport,
    RemediationPlan,
    RemediationStep,
)


def plan_remediation(report: HealthReport) -> RemediationPlan:
    """Generate a remediation plan addressing all blockers."""
    if report.is_ready:
        return RemediationPlan(
            report=report,
            summary=f"{report.pr.repo}#{report.pr.number}: Ready to merge.",
        )

    steps: list[RemediationStep] = []
    # Track whether any emitted command references <REMOTE> so we can append
    # exactly one legend step at the end.
    needs_remote_legend = False

    for blocker in report.blockers:
        match blocker.type:
            case BlockerType.BEHIND_BASE:
                steps.append(
                    RemediationStep(
                        description="Update branch to match base (rebase)",
                        command=f"gh pr update-branch {report.pr.number} -R {report.pr.repo} --rebase",
                        automatable=True,
                    )
                )
                steps.append(
                    RemediationStep(
                        description="Fallback: rebase locally if update-branch fails",
                        command=(
                            f"git fetch <REMOTE> && git rebase <REMOTE>/{report.base_ref}"
                            " && git push --force-with-lease"
                        ),
                        automatable=False,
                    )
                )
                needs_remote_legend = True
            case BlockerType.HAS_CONFLICTS:
                steps.append(
                    RemediationStep(
                        description="Resolve merge conflicts locally",
                        command=f"git fetch <REMOTE> && git rebase <REMOTE>/{report.base_ref}",
                        automatable=False,
                    )
                )
                steps.append(
                    RemediationStep(
                        description="After resolving conflicts, force-push",
                        command="git push --force-with-lease",
                        automatable=False,
                    )
                )
                needs_remote_legend = True
            case BlockerType.CHANGES_REQUESTED:
                steps.append(RemediationStep(description="Address requested changes and request re-review"))
            case BlockerType.MISSING_REVIEWS:
                steps.append(RemediationStep(description="Request review from required reviewers"))
            case BlockerType.FAILING_CHECKS:
                failing_names = blocker.details or ["(unknown checks)"]
                steps.append(
                    RemediationStep(
                        description=f"Investigate {len(failing_names)} failing check(s): {', '.join(failing_names)}",
                    )
                )
                for check in report.checks_failing:
                    if check.details_url:
                        steps.append(RemediationStep(description=f"  → {check.name}: {check.details_url}"))
            case BlockerType.IS_DRAFT:
                steps.append(
                    RemediationStep(
                        description="PR is a draft — mark ready for review when done",
                        command=f"gh pr ready {report.pr.number} -R {report.pr.repo}",
                        automatable=False,
                    )
                )
            case BlockerType.BRANCH_PROTECTION:
                steps.append(
                    RemediationStep(description="Branch protection rules are blocking merge — check repo settings")
                )
            case BlockerType.UNKNOWN_BLOCKER:
                steps.append(RemediationStep(description=f"Unknown blocker: {blocker.description}"))

    # Emit one legend step if any command used the <REMOTE> placeholder.
    # pr-owl can't inspect local git state from inside the CLI, so we never
    # guess the remote name — we leave it for the user (or the skill) to fill
    # in after consulting `git remote -v`.
    if needs_remote_legend:
        steps.append(
            RemediationStep(
                description=(f"(replace <REMOTE> with whichever remote tracks {report.pr.repo} — see `git remote -v`)"),
            )
        )

    blocker_types = {b.type for b in report.blockers}
    if BlockerType.BEHIND_BASE in blocker_types and BlockerType.FAILING_CHECKS in blocker_types:
        summary = f"{report.pr.repo}#{report.pr.number}: Rebase first, then investigate CI failures."
    elif len(report.blockers) == 1:
        summary = f"{report.pr.repo}#{report.pr.number}: {report.blockers[0].description}."
    elif report.blockers:
        summary = (
            f"{report.pr.repo}#{report.pr.number}: "
            f"{len(report.blockers)} blocker(s) — {', '.join(b.description for b in report.blockers)}."
        )
    else:
        summary = f"{report.pr.repo}#{report.pr.number}: Status is {report.status.value}."

    return RemediationPlan(report=report, steps=steps, summary=summary)
