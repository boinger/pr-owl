"""Tests for pr_owl.checker."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pr_owl.checker import _parse_checks, build_blockers, check_pr, classify_status
from pr_owl.exceptions import GhCommandError
from pr_owl.models import BlockerType, MergeStatus
from tests.conftest import load_fixture


class TestClassifyStatus:
    @pytest.mark.parametrize(
        "merge_state,expected",
        [
            ("CLEAN", MergeStatus.READY),
            ("BEHIND", MergeStatus.BEHIND),
            ("DIRTY", MergeStatus.CONFLICTS),
            ("BLOCKED", MergeStatus.BLOCKED),
            ("UNSTABLE", MergeStatus.CI_FAILING),
            ("HAS_HOOKS", MergeStatus.READY),
        ],
    )
    def test_status_mapping(self, merge_state, expected):
        assert classify_status(merge_state, is_draft=False) == expected

    def test_draft_overrides(self):
        assert classify_status("CLEAN", is_draft=True) == MergeStatus.DRAFT
        assert classify_status("BLOCKED", is_draft=True) == MergeStatus.DRAFT

    def test_unknown(self):
        assert classify_status("SOMETHING_NEW", is_draft=False) == MergeStatus.UNKNOWN
        assert classify_status("", is_draft=False) == MergeStatus.UNKNOWN


class TestParseChecks:
    def test_check_run(self):
        rollup = load_fixture("pr_view_clean.json")["statusCheckRollup"]
        checks = _parse_checks(rollup)
        assert len(checks) == 1
        assert checks[0].name == "ci/build"
        assert checks[0].conclusion == "SUCCESS"
        assert checks[0].workflow_name == "CI"

    def test_status_context(self):
        rollup = load_fixture("pr_view_status_context.json")["statusCheckRollup"]
        checks = _parse_checks(rollup)
        assert len(checks) == 2
        # StatusContext normalizes context→name, state→conclusion
        cla = next(c for c in checks if c.name == "cla/google")
        assert cla.conclusion == "SUCCESS"
        assert cla.workflow_name == ""
        assert "cla.developers" in cla.details_url

    def test_mixed(self):
        rollup = load_fixture("pr_view_mixed_checks.json")["statusCheckRollup"]
        checks = _parse_checks(rollup)
        assert len(checks) == 2
        names = {c.name for c in checks}
        assert "ci/build" in names
        assert "ci/external" in names

    def test_empty_rollup(self):
        assert _parse_checks([]) == []
        assert _parse_checks(None) == []

    def test_unknown_typename(self):
        rollup = [
            {
                "__typename": "SomethingNew",
                "name": "external-check",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "workflowName": "",
                "detailsUrl": "https://example.com",
            }
        ]
        checks = _parse_checks(rollup)
        assert len(checks) == 1
        assert checks[0].name == "external-check"
        assert checks[0].conclusion == "SUCCESS"

    def test_unknown_typename_no_name(self):
        rollup = [{"__typename": "Unknown", "status": "COMPLETED", "conclusion": "FAILURE"}]
        checks = _parse_checks(rollup)
        assert len(checks) == 1
        assert checks[0].name == "unknown"


class TestBuildBlockers:
    def test_behind(self):
        data = load_fixture("pr_view_behind.json")
        blockers = build_blockers(data)
        assert len(blockers) == 1
        assert blockers[0].type == BlockerType.BEHIND_BASE

    def test_dirty(self):
        data = load_fixture("pr_view_dirty.json")
        blockers = build_blockers(data)
        assert any(b.type == BlockerType.HAS_CONFLICTS for b in blockers)

    def test_review_required(self):
        data = load_fixture("pr_view_blocked.json")
        blockers = build_blockers(data)
        assert any(b.type == BlockerType.MISSING_REVIEWS for b in blockers)

    def test_changes_requested(self):
        data = load_fixture("pr_view_blocked.json")
        data["reviewDecision"] = "CHANGES_REQUESTED"
        blockers = build_blockers(data)
        assert any(b.type == BlockerType.CHANGES_REQUESTED for b in blockers)
        assert not any(b.type == BlockerType.MISSING_REVIEWS for b in blockers)

    def test_failing_checks(self):
        data = load_fixture("pr_view_unstable.json")
        blockers = build_blockers(data)
        failing = [b for b in blockers if b.type == BlockerType.FAILING_CHECKS]
        assert len(failing) == 1
        assert "ci/lint" in failing[0].details

    def test_draft(self):
        data = load_fixture("pr_view_draft.json")
        blockers = build_blockers(data)
        assert any(b.type == BlockerType.IS_DRAFT for b in blockers)

    def test_compound_behind_and_failing(self):
        data = load_fixture("pr_view_behind.json")
        # Inject a failing check
        data["statusCheckRollup"].append(
            {
                "__typename": "CheckRun",
                "name": "ci/lint",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
                "workflowName": "CI",
                "detailsUrl": "",
            }
        )
        blockers = build_blockers(data)
        types = {b.type for b in blockers}
        assert BlockerType.BEHIND_BASE in types
        assert BlockerType.FAILING_CHECKS in types

    def test_blocked_no_visible_cause(self):
        """BLOCKED with no reviews/checks/conflicts → BRANCH_PROTECTION."""
        data = {
            "mergeStateStatus": "BLOCKED",
            "mergeable": "MERGEABLE",
            "reviewDecision": "",
            "isDraft": False,
            "statusCheckRollup": [],
        }
        blockers = build_blockers(data)
        assert len(blockers) == 1
        assert blockers[0].type == BlockerType.BRANCH_PROTECTION

    def test_blocked_with_review_not_branch_protection(self):
        """BLOCKED with review required → MISSING_REVIEWS, not BRANCH_PROTECTION."""
        data = load_fixture("pr_view_blocked.json")
        blockers = build_blockers(data)
        types = {b.type for b in blockers}
        assert BlockerType.MISSING_REVIEWS in types
        assert BlockerType.BRANCH_PROTECTION not in types


class TestCheckPr:
    def test_normal_flow(self, sample_pr):
        data = load_fixture("pr_view_clean.json")
        with patch("pr_owl.gh.view_pr", return_value=data):
            report = check_pr(sample_pr)
        assert report.status == MergeStatus.READY
        assert report.is_ready
        assert len(report.checks) == 1

    def test_deleted_fork(self, sample_pr):
        with patch("pr_owl.gh.view_pr", side_effect=GhCommandError(["gh"], 1, "Could not resolve")):
            report = check_pr(sample_pr)
        assert report.status == MergeStatus.UNKNOWN
        assert report.error

    def test_unknown_mergeable(self, sample_pr):
        data = load_fixture("pr_view_unknown_mergeable.json")
        with patch("pr_owl.gh.view_pr", return_value=data):
            report = check_pr(sample_pr)
        assert "UNKNOWN" in report.error

    def test_handles_null_head_repo(self, sample_pr):
        """Deleted fork: headRepository and headRepositoryOwner are JSON null.

        Previously this produced an AttributeError from `None.get(...)`.
        Now the report renders normally with an empty head_repo string.
        """
        data = {
            "number": 42,
            "title": "t",
            "url": "",
            "isDraft": False,
            "mergeStateStatus": "CLEAN",
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
            "headRefName": "feature",
            "baseRefName": "main",
            "headRepository": None,
            "headRepositoryOwner": None,
            "statusCheckRollup": [],
        }
        with patch("pr_owl.gh.view_pr", return_value=data):
            report = check_pr(sample_pr)
        assert report.head_repo == ""
        assert report.status == MergeStatus.READY
        assert report.mergeable == "MERGEABLE"
        assert not report.error
