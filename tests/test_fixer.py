"""Tests for pr_owl.fixer."""

from __future__ import annotations

from unittest.mock import patch

from pr_owl.fixer import fix_pr
from pr_owl.models import (
    Blocker,
    BlockerType,
    FixResult,
    HealthReport,
    MergeStatus,
    RemediationPlan,
    RemediationStep,
)


def _make_plan(sample_pr, status, blockers, steps=None):
    report = HealthReport(pr=sample_pr, status=status, blockers=blockers)
    return RemediationPlan(report=report, steps=steps or [])


class TestFixPr:
    def test_success(self, sample_pr):
        plan = _make_plan(
            sample_pr,
            MergeStatus.BEHIND,
            [Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
            [RemediationStep(description="Update branch", command="gh pr update-branch", automatable=True)],
        )
        mock_result = FixResult(pr=sample_pr, success=True, command_run="gh pr update-branch 42 -R acme/repo --rebase")
        with patch("pr_owl.gh.update_branch", return_value=mock_result):
            result = fix_pr(plan)
        assert result.success

    def test_skip_non_behind(self, sample_pr):
        plan = _make_plan(
            sample_pr,
            MergeStatus.CONFLICTS,
            [Blocker(type=BlockerType.HAS_CONFLICTS, description="Conflicts")],
        )
        result = fix_pr(plan)
        assert result.skipped
        assert "No BEHIND_BASE" in result.reason

    def test_failure(self, sample_pr):
        plan = _make_plan(
            sample_pr,
            MergeStatus.BEHIND,
            [Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
        )
        mock_result = FixResult(pr=sample_pr, success=False, reason="generic error")
        with patch("pr_owl.gh.update_branch", return_value=mock_result):
            result = fix_pr(plan)
        assert not result.success
        assert not result.skipped

    def test_conflicts_on_rebase(self, sample_pr):
        plan = _make_plan(
            sample_pr,
            MergeStatus.BEHIND,
            [Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
        )
        mock_result = FixResult(pr=sample_pr, success=False, reason="conflicts discovered during rebase")
        with patch("pr_owl.gh.update_branch", return_value=mock_result):
            result = fix_pr(plan)
        assert not result.success
        assert "conflicts" in result.reason

    def test_permission_denied(self, sample_pr):
        plan = _make_plan(
            sample_pr,
            MergeStatus.BEHIND,
            [Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
        )
        mock_result = FixResult(pr=sample_pr, success=False, reason="repo doesn't allow branch updates; rebase locally")
        with patch("pr_owl.gh.update_branch", return_value=mock_result):
            result = fix_pr(plan)
        assert not result.success
        assert "rebase locally" in result.reason

    def test_already_up_to_date(self, sample_pr):
        plan = _make_plan(
            sample_pr,
            MergeStatus.BEHIND,
            [Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
        )
        mock_result = FixResult(pr=sample_pr, skipped=True, reason="already up-to-date")
        with patch("pr_owl.gh.update_branch", return_value=mock_result):
            result = fix_pr(plan)
        assert result.skipped

    def test_dry_run(self, sample_pr):
        plan = _make_plan(
            sample_pr,
            MergeStatus.BEHIND,
            [Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
        )
        result = fix_pr(plan, dry_run=True)
        assert result.skipped
        assert "dry run" in result.reason
        assert result.command_run  # should show what would have run
