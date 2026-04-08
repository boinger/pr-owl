"""Tests for pr_owl.planner."""

from __future__ import annotations

from pr_owl.models import (
    Blocker,
    BlockerType,
    CICheck,
    HealthReport,
    MergeStatus,
)
from pr_owl.planner import plan_remediation


class TestPlanRemediation:
    def test_ready_pr(self, sample_pr):
        report = HealthReport(pr=sample_pr, status=MergeStatus.READY)
        plan = plan_remediation(report)
        assert not plan.steps
        assert "Ready to merge" in plan.summary

    def test_behind(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BEHIND,
            blockers=[Blocker(type=BlockerType.BEHIND_BASE, description="Behind base branch")],
            base_ref="main",
        )
        plan = plan_remediation(report)
        assert len(plan.steps) >= 2
        assert plan.steps[0].automatable
        assert "update-branch" in plan.steps[0].command
        assert not plan.steps[1].automatable  # fallback local rebase

    def test_conflicts(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.CONFLICTS,
            blockers=[Blocker(type=BlockerType.HAS_CONFLICTS, description="Merge conflicts")],
            base_ref="main",
        )
        plan = plan_remediation(report)
        assert any("rebase" in s.command.lower() for s in plan.steps if s.command)
        assert all(not s.automatable for s in plan.steps)

    def test_blocked_reviews(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BLOCKED,
            blockers=[Blocker(type=BlockerType.MISSING_REVIEWS, description="Review required")],
            review_decision="REVIEW_REQUIRED",
        )
        plan = plan_remediation(report)
        assert any("review" in s.description.lower() for s in plan.steps)

    def test_changes_requested(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BLOCKED,
            blockers=[Blocker(type=BlockerType.CHANGES_REQUESTED, description="Changes requested")],
            review_decision="CHANGES_REQUESTED",
        )
        plan = plan_remediation(report)
        assert any("Address requested changes and request re-review" in s.description for s in plan.steps)

    def test_ci_failing(self, sample_pr):
        failing_check = CICheck(
            name="ci/lint",
            status="COMPLETED",
            conclusion="FAILURE",
            workflow_name="CI",
            details_url="https://example.com/run/456",
        )
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.CI_FAILING,
            blockers=[Blocker(type=BlockerType.FAILING_CHECKS, description="1 check failing", details=["ci/lint"])],
            checks=[failing_check],
        )
        plan = plan_remediation(report)
        assert any("ci/lint" in s.description for s in plan.steps)

    def test_draft(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.DRAFT,
            blockers=[Blocker(type=BlockerType.IS_DRAFT, description="PR is a draft")],
        )
        plan = plan_remediation(report)
        assert any("draft" in s.description.lower() for s in plan.steps)

    def test_compound_behind_and_ci(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BEHIND,
            blockers=[
                Blocker(type=BlockerType.BEHIND_BASE, description="Behind base branch"),
                Blocker(type=BlockerType.FAILING_CHECKS, description="1 check failing", details=["ci/lint"]),
            ],
            checks=[
                CICheck(
                    name="ci/lint",
                    status="COMPLETED",
                    conclusion="FAILURE",
                    workflow_name="CI",
                    details_url="",
                )
            ],
            base_ref="main",
        )
        plan = plan_remediation(report)
        assert "Rebase first" in plan.summary
        # Has both update-branch and CI investigation steps
        has_rebase = any("update-branch" in s.command for s in plan.steps if s.command)
        has_ci = any("ci/lint" in s.description for s in plan.steps)
        assert has_rebase and has_ci

    def test_compound_blocked_and_ci(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BLOCKED,
            blockers=[
                Blocker(type=BlockerType.MISSING_REVIEWS, description="Review required"),
                Blocker(type=BlockerType.FAILING_CHECKS, description="1 check failing", details=["ci/lint"]),
            ],
            review_decision="REVIEW_REQUIRED",
            checks=[
                CICheck(
                    name="ci/lint",
                    status="COMPLETED",
                    conclusion="FAILURE",
                    workflow_name="CI",
                    details_url="",
                )
            ],
        )
        plan = plan_remediation(report)
        assert len(plan.report.blockers) == 2
        assert "2 blocker" in plan.summary

    def test_branch_protection_blocker(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BLOCKED,
            blockers=[Blocker(type=BlockerType.BRANCH_PROTECTION, description="Branch protection rules")],
        )
        plan = plan_remediation(report)
        assert any("branch protection" in s.description.lower() for s in plan.steps)

    def test_unknown_blocker(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.UNKNOWN,
            blockers=[Blocker(type=BlockerType.UNKNOWN_BLOCKER, description="Something unexpected")],
        )
        plan = plan_remediation(report)
        assert any("unknown" in s.description.lower() for s in plan.steps)

    def test_automatable_flags(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BEHIND,
            blockers=[Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
            base_ref="main",
        )
        plan = plan_remediation(report)
        automatable = [s for s in plan.steps if s.automatable]
        assert len(automatable) >= 1
        assert "update-branch" in automatable[0].command

    def test_emits_remote_placeholder_for_behind(self, sample_pr):
        """Local git rebase command uses <REMOTE> placeholder and a legend is emitted."""
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BEHIND,
            blockers=[Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
            base_ref="main",
        )
        plan = plan_remediation(report)
        # Fallback uses `git rebase`, not `gh pr update-branch --rebase`
        git_rebase = [s for s in plan.steps if s.command and "git rebase" in s.command]
        assert git_rebase, "expected a `git rebase` fallback command"
        assert all("<REMOTE>" in s.command for s in git_rebase)
        assert all("upstream" not in s.command for s in git_rebase)
        # Legend step mentions pr.repo and git remote -v
        legends = [s for s in plan.steps if "<REMOTE>" in s.description and "git remote" in s.description]
        assert len(legends) == 1
        assert sample_pr.repo in legends[0].description

    def test_emits_remote_placeholder_for_conflicts(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.CONFLICTS,
            blockers=[Blocker(type=BlockerType.HAS_CONFLICTS, description="Merge conflicts")],
            base_ref="main",
        )
        plan = plan_remediation(report)
        assert any("<REMOTE>" in s.command for s in plan.steps if s.command)
        assert all("upstream" not in (s.command or "") for s in plan.steps)
        # Exactly one legend step
        legends = [s for s in plan.steps if "<REMOTE>" in s.description and "git remote" in s.description]
        assert len(legends) == 1

    def test_single_legend_for_compound_blockers(self, sample_pr):
        """Even with multiple <REMOTE>-using blockers, emit only one legend."""
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BEHIND,
            blockers=[
                Blocker(type=BlockerType.BEHIND_BASE, description="Behind"),
                Blocker(type=BlockerType.HAS_CONFLICTS, description="Conflicts"),
            ],
            base_ref="main",
        )
        plan = plan_remediation(report)
        legends = [s for s in plan.steps if "<REMOTE>" in s.description and "git remote" in s.description]
        assert len(legends) == 1

    def test_no_legend_when_no_remote_commands(self, sample_pr):
        """Plans without <REMOTE> commands must not emit the legend."""
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BLOCKED,
            blockers=[Blocker(type=BlockerType.MISSING_REVIEWS, description="Review required")],
        )
        plan = plan_remediation(report)
        legends = [s for s in plan.steps if "<REMOTE>" in s.description]
        assert legends == []
