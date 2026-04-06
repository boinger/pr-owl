"""Tests for pr_owl.discovery."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from pr_owl.discovery import discover_prs, filter_stale
from pr_owl.models import PRInfo
from tests.conftest import load_fixture


class TestDiscoverPrs:
    def test_parses_nameWithOwner(self):
        fixture = load_fixture("search_prs.json")
        with patch("pr_owl.gh.search_prs", return_value=fixture):
            prs = discover_prs()
        assert len(prs) == 2
        assert prs[0].repo == "acme/repo"
        assert prs[1].repo == "acme/other"

    def test_empty_results(self):
        with patch("pr_owl.gh.search_prs", return_value=[]):
            prs = discover_prs()
        assert prs == []

    def test_filter_by_repo(self):
        fixture = load_fixture("search_prs.json")
        with patch("pr_owl.gh.search_prs", return_value=fixture) as mock:
            discover_prs(repo="acme/repo")
        mock.assert_called_once_with(author="@me", state="open", repo="acme/repo", org="", limit=100)

    def test_filter_by_org(self):
        fixture = load_fixture("search_prs.json")
        with patch("pr_owl.gh.search_prs", return_value=fixture) as mock:
            discover_prs(org="acme")
        mock.assert_called_once_with(author="@me", state="open", repo="", org="acme", limit=100)


class TestFilterStale:
    def test_includes_old(self):
        pr = PRInfo(
            number=1,
            title="Old",
            repo="a/b",
            url="",
            is_draft=False,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        result = filter_stale([pr], days=7)
        assert len(result) == 1

    def test_excludes_recent(self):
        yesterday = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        pr = PRInfo(
            number=1,
            title="Recent",
            repo="a/b",
            url="",
            is_draft=False,
            created_at=yesterday,
            updated_at=yesterday,
        )
        result = filter_stale([pr], days=7)
        assert len(result) == 0
