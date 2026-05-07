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
        blockers = build_blockers(data, behind_by=3)
        assert len(blockers) == 1
        assert blockers[0].type == BlockerType.BEHIND_BASE
        assert "3 commit(s)" in blockers[0].description

    def test_behind_fallback(self):
        """mergeStateStatus=BEHIND with behind_by=0 (compare failed) falls back to old behavior."""
        data = load_fixture("pr_view_behind.json")
        blockers = build_blockers(data, behind_by=0)
        assert len(blockers) == 1
        assert blockers[0].type == BlockerType.BEHIND_BASE
        assert "commit(s)" not in blockers[0].description

    def test_behind_via_compare(self):
        """mergeStateStatus=BLOCKED but compare says behind → BEHIND_BASE with count."""
        data = load_fixture("pr_view_blocked_behind.json")
        blockers = build_blockers(data, behind_by=5)
        types = {b.type for b in blockers}
        assert BlockerType.BEHIND_BASE in types
        behind = next(b for b in blockers if b.type == BlockerType.BEHIND_BASE)
        assert "5 commit(s)" in behind.description

    def test_blocked_behind_compound(self):
        """The actual motivating scenario: BLOCKED + REVIEW_REQUIRED + behind_by=5."""
        data = load_fixture("pr_view_blocked_behind.json")
        blockers = build_blockers(data, behind_by=5)
        types = {b.type for b in blockers}
        assert BlockerType.BEHIND_BASE in types
        assert BlockerType.MISSING_REVIEWS in types

    def test_dirty(self):
        data = load_fixture("pr_view_dirty.json")
        blockers = build_blockers(data)
        assert any(b.type == BlockerType.HAS_CONFLICTS for b in blockers)

    def test_conflicts_via_mergeable(self):
        """mergeable=CONFLICTING with non-DIRTY mergeStateStatus → HAS_CONFLICTS."""
        data = {
            "mergeStateStatus": "BLOCKED",
            "mergeable": "CONFLICTING",
            "reviewDecision": "",
            "isDraft": False,
            "statusCheckRollup": [],
        }
        blockers = build_blockers(data)
        assert any(b.type == BlockerType.HAS_CONFLICTS for b in blockers)

    def test_no_double_conflict(self):
        """DIRTY + CONFLICTING produces exactly one HAS_CONFLICTS blocker."""
        data = load_fixture("pr_view_dirty.json")
        data["mergeable"] = "CONFLICTING"
        blockers = build_blockers(data)
        conflict_blockers = [b for b in blockers if b.type == BlockerType.HAS_CONFLICTS]
        assert len(conflict_blockers) == 1

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
        with patch("pr_owl.gh.view_pr", return_value=data), patch("pr_owl.gh.compare_refs") as mock_compare:
            report = check_pr(sample_pr)
        assert report.status == MergeStatus.READY
        assert report.is_ready
        assert len(report.checks) == 1
        # CLEAN → compare_refs should not be called
        mock_compare.assert_not_called()

    def test_deleted_fork(self, sample_pr):
        with patch("pr_owl.gh.view_pr", side_effect=GhCommandError(["gh"], 1, "Could not resolve")):
            report = check_pr(sample_pr)
        assert report.status == MergeStatus.UNKNOWN
        assert report.error

    # T9: new graphql-specific error types from view_pr must also produce UNKNOWN
    # (audit doesn't crash). The pre-rewrite test_deleted_fork covered gh-pr-view's
    # error path; these guard the new graphql error paths introduced in the rewrite.

    def test_view_pr_raises_pr_not_found_propagates_to_unknown(self, sample_pr):
        """GraphQL returns null pullRequest → PrNotFoundError → UNKNOWN report."""
        from pr_owl.exceptions import PrNotFoundError

        with patch("pr_owl.gh.view_pr", side_effect=PrNotFoundError("PR not accessible")):
            report = check_pr(sample_pr)
        assert report.status == MergeStatus.UNKNOWN
        assert report.error

    def test_view_pr_raises_graphql_errors_envelope_propagates_to_unknown(self, sample_pr):
        """GraphQL returns 200 with errors array → GhCommandError → UNKNOWN report."""
        with patch("pr_owl.gh.view_pr", side_effect=GhCommandError(["gh", "api", "graphql"], 0, "GraphQL errors: ...")):
            report = check_pr(sample_pr)
        assert report.status == MergeStatus.UNKNOWN
        assert report.error

    def test_unknown_mergeable(self, sample_pr):
        data = load_fixture("pr_view_unknown_mergeable.json")
        compare_result = {"behind_by": 0, "ahead_by": 1}
        with (
            patch("pr_owl.gh.view_pr", return_value=data),
            patch("pr_owl.gh.compare_refs", return_value=compare_result),
        ):
            report = check_pr(sample_pr)
        assert "UNKNOWN" in report.error

    def test_handles_null_head_repo(self, sample_pr):
        """Deleted fork: headRepository and headRepositoryOwner are JSON null.

        compare_refs is skipped (nothing to compare against), behind_by stays 0.
        """
        data = {
            "number": 42,
            "title": "t",
            "url": "",
            "isDraft": False,
            "mergeStateStatus": "BLOCKED",
            "mergeable": "MERGEABLE",
            "reviewDecision": "REVIEW_REQUIRED",
            "headRefName": "feature",
            "baseRefName": "main",
            "headRepository": None,
            "headRepositoryOwner": None,
            "statusCheckRollup": [],
            "totalCommentsCount": 0,
        }
        with patch("pr_owl.gh.view_pr", return_value=data), patch("pr_owl.gh.compare_refs") as mock_compare:
            report = check_pr(sample_pr)
        assert report.head_repo == ""
        assert report.behind_by == 0
        mock_compare.assert_not_called()

    def test_compare_success(self, sample_pr):
        """view + compare both succeed → behind_by populated on report."""
        data = load_fixture("pr_view_blocked_behind.json")
        with (
            patch("pr_owl.gh.view_pr", return_value=data),
            patch("pr_owl.gh.compare_refs", return_value={"behind_by": 7, "ahead_by": 2}),
        ):
            report = check_pr(sample_pr)
        assert report.behind_by == 7
        assert any(b.type == BlockerType.BEHIND_BASE for b in report.blockers)

    def test_compare_failure_fallback(self, sample_pr):
        """view ok + compare raises → behind_by=0, report still valid."""
        data = load_fixture("pr_view_blocked_behind.json")
        with (
            patch("pr_owl.gh.view_pr", return_value=data),
            patch("pr_owl.gh.compare_refs", side_effect=GhCommandError(["gh"], 1, "fail")),
        ):
            report = check_pr(sample_pr)
        assert report.behind_by == 0
        assert report.status == MergeStatus.BLOCKED
        # No BEHIND_BASE blocker since mergeStateStatus is BLOCKED, not BEHIND
        assert not any(b.type == BlockerType.BEHIND_BASE for b in report.blockers)

    def test_fork_head_spec(self, sample_pr):
        """Cross-repo PR → compare called with fork_owner:branch format."""
        data = load_fixture("pr_view_blocked_behind.json")
        data["headRepositoryOwner"] = {"login": "contributor"}
        data["headRepository"] = {"name": "repo"}
        with (
            patch("pr_owl.gh.view_pr", return_value=data),
            patch("pr_owl.gh.compare_refs", return_value={"behind_by": 2, "ahead_by": 1}) as mock_compare,
        ):
            report = check_pr(sample_pr)
        mock_compare.assert_called_once_with("acme/repo", "main", "contributor:feature/widget")
        assert report.behind_by == 2

    def test_clean_skips_compare(self, sample_pr):
        """mergeStateStatus=CLEAN → compare_refs never called."""
        data = load_fixture("pr_view_clean.json")
        data["headRepository"] = {"name": "repo"}
        data["headRepositoryOwner"] = {"login": "acme"}
        with (
            patch("pr_owl.gh.view_pr", return_value=data),
            patch("pr_owl.gh.compare_refs") as mock_compare,
        ):
            check_pr(sample_pr)
        mock_compare.assert_not_called()
