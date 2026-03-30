"""Integration tests — hit real GitHub API. Skipped by default."""

from __future__ import annotations

import json
import subprocess

import pytest


@pytest.mark.integration
class TestRealGitHub:
    def test_search_prs(self):
        """Real gh search prs returns valid JSON."""
        result = subprocess.run(
            ["gh", "search", "prs", "--author", "@me", "--state", "open", "--limit", "5", "--json", "number,title,url"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_pr_view_fields(self):
        """Real gh pr view returns expected fields (requires at least one open PR)."""
        # First find a PR
        search = subprocess.run(
            [
                "gh",
                "search",
                "prs",
                "--author",
                "@me",
                "--state",
                "open",
                "--limit",
                "1",
                "--json",
                "number,repository",
            ],
            capture_output=True,
            text=True,
        )
        if search.returncode != 0:
            pytest.skip("gh search failed")

        prs = json.loads(search.stdout)
        if not prs:
            pytest.skip("No open PRs to test against")

        pr = prs[0]
        repo = pr["repository"]["nameWithOwner"]
        number = pr["number"]

        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(number),
                "-R",
                repo,
                "--json",
                "number,mergeStateStatus,mergeable,reviewDecision,statusCheckRollup",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "mergeStateStatus" in data
        assert "mergeable" in data
