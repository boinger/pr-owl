"""Tests for pr_owl.gh."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pr_owl.exceptions import (
    GhAuthError,
    GhCommandError,
    GhNotFoundError,
    GhRateLimitError,
    PrNotFoundError,
    PrOwlError,
)
from pr_owl.gh import (
    check_auth,
    compare_refs,
    ensure_gh,
    get_current_user,
    search_prs,
    view_pr,
)


class TestEnsureGh:
    def test_found(self):
        with patch("shutil.which", return_value="/usr/local/bin/gh"):
            assert ensure_gh() == "/usr/local/bin/gh"

    def test_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(GhNotFoundError):
                ensure_gh()


class TestCheckAuth:
    def test_success(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="", stderr="")
        check_auth()
        mock_subprocess.assert_called_once()
        # Verify shell=False (list args)
        call_args = mock_subprocess.call_args
        assert call_args[0][0] == ["gh", "auth", "status"]

    def test_failure(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="not logged in")
        with pytest.raises(GhAuthError):
            check_auth()


class TestCheckErrors:
    """Tests for _check_errors via the public functions that use it."""

    def test_shell_false(self, mock_subprocess):
        """All subprocess calls use list args, never shell=True."""
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        search_prs()
        call_args = mock_subprocess.call_args
        assert isinstance(call_args[0][0], list)

    def test_rate_limit_via_search(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="API rate limit exceeded")
        with pytest.raises(GhRateLimitError):
            search_prs()

    def test_not_found_via_view(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="Could not resolve repository")
        with pytest.raises(PrNotFoundError):
            view_pr(1, "deleted/repo")

    def test_generic_error_via_search(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="something broke")
        with pytest.raises(GhCommandError) as exc_info:
            search_prs()
        assert exc_info.value.returncode == 1
        assert "something broke" in exc_info.value.stderr

    def test_timeout_expired(self, mock_subprocess):
        mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd=["gh", "search", "prs"], timeout=30)
        with pytest.raises(GhCommandError) as exc_info:
            search_prs()
        assert "timed out" in str(exc_info.value).lower()


class TestSearchPrs:
    def test_basic(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout='[{"number": 1}]',
            stderr="",
        )
        result = search_prs()
        assert len(result) == 1
        assert result[0]["number"] == 1

    def test_empty(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        result = search_prs()
        assert result == []

    def test_truncation_warning(self, mock_subprocess, caplog):
        # Return exactly limit items
        items = [{"number": i} for i in range(100)]
        import json

        mock_subprocess.return_value = MagicMock(returncode=0, stdout=json.dumps(items), stderr="")
        with caplog.at_level("WARNING"):
            result = search_prs(limit=100)
        assert len(result) == 100
        assert "truncated" in caplog.text

    def test_rate_limit(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="API rate limit exceeded")
        with pytest.raises(GhRateLimitError):
            search_prs()

    def test_org_filter(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        search_prs(org="acme")
        args = mock_subprocess.call_args[0][0]
        owner_idx = args.index("--owner")
        assert args[owner_idx + 1] == "acme"

    def test_repo_filter(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        search_prs(repo="acme/repo")
        args = mock_subprocess.call_args[0][0]
        repo_idx = args.index("--repo")
        assert args[repo_idx + 1] == "acme/repo"

    def test_translates_json_decode_error(self, mock_subprocess):
        """Malformed JSON from `gh search prs` becomes PrOwlError, not JSONDecodeError."""
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="{ not json", stderr="")
        with pytest.raises(PrOwlError, match="Malformed JSON"):
            search_prs()


def _graphql_envelope(pr_dict: dict | None) -> str:
    """Helper: build a GraphQL response envelope around a pullRequest dict."""
    return json.dumps({"data": {"repository": {"pullRequest": pr_dict}}})


def _full_pr(**overrides) -> dict:
    """Helper: full GraphQL pullRequest dict shape with sensible defaults."""
    base = {
        "number": 42,
        "title": "Test PR",
        "url": "https://github.com/acme/repo/pull/42",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "APPROVED",
        "headRefName": "feature",
        "baseRefName": "main",
        "headRepository": {"name": "repo"},
        "headRepositoryOwner": {"login": "acme"},
        "totalCommentsCount": 0,
        "reviews": {"totalCount": 0, "nodes": []},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS", "contexts": {"nodes": []}}}}]},
    }
    base.update(overrides)
    return base


class TestViewPr:
    def test_basic(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout=_graphql_envelope(_full_pr()),
            stderr="",
        )
        result = view_pr(42, "acme/repo")
        assert result["mergeStateStatus"] == "CLEAN"
        assert result["totalCommentsCount"] == 0
        assert result["reviews"] == []  # nodes list, not totalCount

    def test_not_found(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Could not resolve to a Repository",
        )
        with pytest.raises(PrNotFoundError):
            view_pr(42, "deleted/repo")

    def test_translates_json_decode_error(self, mock_subprocess):
        """Malformed JSON from `gh api graphql` becomes PrOwlError, not JSONDecodeError."""
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        with pytest.raises(PrOwlError, match="Malformed JSON"):
            view_pr(42, "acme/repo")

    # T5: GraphQL errors envelope (HTTP 200 with errors array) → GhCommandError
    def test_graphql_errors_envelope(self, mock_subprocess):
        from pr_owl.exceptions import GhCommandError

        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"errors": [{"message": "Field 'totalCommentsCount' doesn't exist"}]}),
            stderr="",
        )
        with pytest.raises(GhCommandError, match="GraphQL errors"):
            view_pr(42, "acme/repo")

    # T6: missing data.repository.pullRequest (null) → PrNotFoundError
    def test_null_pull_request(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout=_graphql_envelope(None),
            stderr="",
        )
        with pytest.raises(PrNotFoundError):
            view_pr(42, "acme/repo")

    # T7: totalCommentsCount: null → coerces to 0
    def test_null_total_comments_count(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout=_graphql_envelope(_full_pr(totalCommentsCount=None)),
            stderr="",
        )
        result = view_pr(42, "acme/repo")
        assert result["totalCommentsCount"] == 0

    # T8: totalCommentsCount with wrong type → coerces to 0 (does not raise)
    def test_wrong_type_total_comments_count(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout=_graphql_envelope(_full_pr(totalCommentsCount="twelve")),
            stderr="",
        )
        result = view_pr(42, "acme/repo")
        assert result["totalCommentsCount"] == 0

    def test_split_repo_rejects_malformed(self, mock_subprocess):
        with pytest.raises(PrOwlError, match="Malformed repo"):
            view_pr(42, "no-slash")
        with pytest.raises(PrOwlError, match="Malformed repo"):
            view_pr(42, "/missing-owner")
        with pytest.raises(PrOwlError, match="Malformed repo"):
            view_pr(42, "missing-name/")

    def test_reviews_returned_as_list_not_count(self, mock_subprocess):
        """T10b: the reviews key MUST be list[dict] so cli._enrich_closed_prs can len() it."""
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout=_graphql_envelope(
                _full_pr(
                    reviews={
                        "totalCount": 3,
                        "nodes": [{"state": "APPROVED"}, {"state": "COMMENTED"}, {"state": "CHANGES_REQUESTED"}],
                    }
                )
            ),
            stderr="",
        )
        result = view_pr(42, "acme/repo")
        assert isinstance(result["reviews"], list)
        assert len(result["reviews"]) == 3


class TestCompareRefs:
    def test_success(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout='{"behind_by": 3, "ahead_by": 5}',
            stderr="",
        )
        result = compare_refs("acme/repo", "main", "feature-branch")
        assert result["behind_by"] == 3
        assert result["ahead_by"] == 5

    def test_cross_repo_head(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout='{"behind_by": 0, "ahead_by": 1}',
            stderr="",
        )
        compare_refs("acme/repo", "main", "contributor:feature-branch")
        args = mock_subprocess.call_args[0][0]
        assert "repos/acme/repo/compare/main...contributor:feature-branch" in args

    def test_not_found(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="Not Found")
        with pytest.raises(PrNotFoundError):
            compare_refs("acme/repo", "main", "deleted-branch")

    def test_rate_limit(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="API rate limit exceeded")
        with pytest.raises(GhRateLimitError):
            compare_refs("acme/repo", "main", "feature")

    def test_generic_error(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="something broke")
        with pytest.raises(GhCommandError):
            compare_refs("acme/repo", "main", "feature")

    def test_malformed_json(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        with pytest.raises(PrOwlError, match="Malformed JSON"):
            compare_refs("acme/repo", "main", "feature")

    def test_timeout(self, mock_subprocess):
        mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd=["gh", "api"], timeout=30)
        with pytest.raises(GhCommandError):
            compare_refs("acme/repo", "main", "feature")


class TestGetCurrentUser:
    def test_success(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="jeff\n", stderr="")
        assert get_current_user() == "jeff"

    def test_failure(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="auth error")
        with pytest.raises(GhAuthError):
            get_current_user()
