"""Tests for pr_owl.gh."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pr_owl.exceptions import (
    GhAuthError,
    GhCommandError,
    GhNotFoundError,
    GhRateLimitError,
    PrNotFoundError,
)
from pr_owl.gh import (
    check_auth,
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


class TestViewPr:
    def test_basic(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout='{"number": 42, "mergeStateStatus": "CLEAN"}',
            stderr="",
        )
        result = view_pr(42, "acme/repo")
        assert result["mergeStateStatus"] == "CLEAN"

    def test_not_found(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Could not resolve to a Repository",
        )
        with pytest.raises(PrNotFoundError):
            view_pr(42, "deleted/repo")


class TestGetCurrentUser:
    def test_success(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="jeff\n", stderr="")
        assert get_current_user() == "jeff"

    def test_failure(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="auth error")
        with pytest.raises(GhAuthError):
            get_current_user()
