"""Tests for pr_owl.models."""

import pytest

from pr_owl.models import (
    Blocker,
    BlockerType,
    CICheck,
    HealthReport,
    MergeStatus,
    PRInfo,
)


class TestMergeStatus:
    def test_all_values(self):
        assert len(MergeStatus) == 7
        expected = {"READY", "BEHIND", "CONFLICTS", "BLOCKED", "CI_FAILING", "DRAFT", "UNKNOWN"}
        assert {s.value for s in MergeStatus} == expected

    def test_string_enum(self):
        assert MergeStatus.READY == "READY"
        assert str(MergeStatus.BEHIND) == "MergeStatus.BEHIND"


class TestBlockerType:
    def test_all_values(self):
        assert len(BlockerType) == 8
        expected = {
            "BEHIND_BASE",
            "HAS_CONFLICTS",
            "MISSING_REVIEWS",
            "CHANGES_REQUESTED",
            "FAILING_CHECKS",
            "IS_DRAFT",
            "BRANCH_PROTECTION",
            "UNKNOWN_BLOCKER",
        }
        assert {b.value for b in BlockerType} == expected


class TestBlocker:
    def test_without_details(self):
        b = Blocker(type=BlockerType.BEHIND_BASE, description="Behind base branch")
        assert b.details == []

    def test_with_details(self):
        b = Blocker(type=BlockerType.FAILING_CHECKS, description="2 checks failing", details=["ci/build", "ci/lint"])
        assert len(b.details) == 2
        assert "ci/build" in b.details

    @pytest.mark.parametrize(
        "btype,expected",
        [
            (BlockerType.BEHIND_BASE, True),
            (BlockerType.HAS_CONFLICTS, True),
            (BlockerType.FAILING_CHECKS, True),
            (BlockerType.IS_DRAFT, True),
            (BlockerType.CHANGES_REQUESTED, True),
            (BlockerType.MISSING_REVIEWS, False),
            (BlockerType.BRANCH_PROTECTION, False),
            (BlockerType.UNKNOWN_BLOCKER, False),
        ],
    )
    def test_actionable(self, btype, expected):
        b = Blocker(type=btype, description="test")
        assert b.actionable is expected

    def test_all_blocker_types_have_defined_actionability(self):
        """Completeness guard: every BlockerType must be explicitly classified."""
        actionable = {bt for bt in BlockerType if Blocker(type=bt, description="").actionable}
        non_actionable = {bt for bt in BlockerType if not Blocker(type=bt, description="").actionable}
        assert actionable | non_actionable == set(BlockerType)
        assert len(actionable) == 5
        assert len(non_actionable) == 3


class TestCICheck:
    def test_is_passing(self):
        c = CICheck(name="ci/build", status="COMPLETED", conclusion="SUCCESS", workflow_name="CI", details_url="")
        assert c.is_passing
        assert not c.is_failing
        assert not c.is_pending

    def test_is_failing(self):
        c = CICheck(name="ci/build", status="COMPLETED", conclusion="FAILURE", workflow_name="CI", details_url="")
        assert c.is_failing
        assert not c.is_passing

    def test_is_pending(self):
        c = CICheck(name="ci/build", status="IN_PROGRESS", conclusion=None, workflow_name="CI", details_url="")
        assert c.is_pending
        assert not c.is_passing
        assert not c.is_failing

    def test_neutral_is_passing(self):
        c = CICheck(name="ci/optional", status="COMPLETED", conclusion="NEUTRAL", workflow_name="CI", details_url="")
        assert c.is_passing


class TestPRInfo:
    def test_from_search_result(self):
        data = {
            "number": 42,
            "title": "Add widget",
            "url": "https://github.com/acme/repo/pull/42",
            "repository": {"nameWithOwner": "acme/repo"},
            "isDraft": False,
            "createdAt": "2026-01-15T10:00:00Z",
            "updatedAt": "2026-03-20T14:00:00Z",
        }
        pr = PRInfo.from_search_result(data)
        assert pr.number == 42
        assert pr.repo == "acme/repo"
        assert not pr.is_draft

    def test_updated_at_dt(self, sample_pr):
        dt = sample_pr.updated_at_dt
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 20


class TestHealthReport:
    def test_is_ready_when_ready_no_blockers(self, sample_pr):
        r = HealthReport(pr=sample_pr, status=MergeStatus.READY)
        assert r.is_ready

    def test_not_ready_when_has_blockers(self, sample_pr):
        r = HealthReport(
            pr=sample_pr,
            status=MergeStatus.READY,
            blockers=[Blocker(type=BlockerType.MISSING_REVIEWS, description="Review required")],
        )
        assert not r.is_ready

    def test_not_ready_when_not_ready_status(self, sample_pr):
        r = HealthReport(pr=sample_pr, status=MergeStatus.BEHIND)
        assert not r.is_ready

    def test_has_actionable_blockers_no_blockers(self, sample_pr):
        r = HealthReport(pr=sample_pr, status=MergeStatus.READY)
        assert not r.has_actionable_blockers

    def test_has_actionable_blockers_only_non_actionable(self, sample_pr):
        r = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BLOCKED,
            blockers=[Blocker(type=BlockerType.MISSING_REVIEWS, description="Review required")],
        )
        assert not r.has_actionable_blockers

    def test_has_actionable_blockers_actionable(self, sample_pr):
        r = HealthReport(
            pr=sample_pr,
            status=MergeStatus.CI_FAILING,
            blockers=[Blocker(type=BlockerType.FAILING_CHECKS, description="1 check(s) failing")],
        )
        assert r.has_actionable_blockers

    def test_has_actionable_blockers_mixed(self, sample_pr):
        r = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BLOCKED,
            blockers=[
                Blocker(type=BlockerType.MISSING_REVIEWS, description="Review required"),
                Blocker(type=BlockerType.FAILING_CHECKS, description="1 check(s) failing"),
            ],
        )
        assert r.has_actionable_blockers

    def test_checks_computed(self, sample_pr):
        checks = [
            CICheck(name="pass", status="COMPLETED", conclusion="SUCCESS", workflow_name="CI", details_url=""),
            CICheck(name="fail", status="COMPLETED", conclusion="FAILURE", workflow_name="CI", details_url=""),
            CICheck(name="pend", status="IN_PROGRESS", conclusion=None, workflow_name="CI", details_url=""),
        ]
        r = HealthReport(pr=sample_pr, status=MergeStatus.CI_FAILING, checks=checks)
        assert len(r.checks_passing) == 1
        assert len(r.checks_failing) == 1
        assert len(r.checks_pending) == 1
