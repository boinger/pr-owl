"""Tests for pr_owl.cli."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from pr_owl.cli import cli
from pr_owl.exceptions import GhCommandError
from pr_owl.models import (
    Blocker,
    BlockerType,
    FixResult,
    HealthReport,
    MergeStatus,
    PRInfo,
)

runner = CliRunner()


def _sample_pr(number=42, repo="acme/repo"):
    return PRInfo(
        number=number,
        title=f"PR #{number}",
        repo=repo,
        url=f"https://github.com/{repo}/pull/{number}",
        is_draft=False,
        created_at="2026-01-15T10:00:00Z",
        updated_at="2026-03-20T14:00:00Z",
    )


def _mock_preflight():
    """Common patches for preflight checks."""
    return [
        patch("pr_owl.cli.gh.ensure_gh", return_value="/usr/local/bin/gh"),
        patch("pr_owl.cli.gh.check_auth"),
        patch("pr_owl.cli.gh.get_current_user", return_value="testuser"),
    ]


class TestAuditCommand:
    def test_resilience_mid_audit(self):
        """One check_pr failure shouldn't crash the audit."""
        pr1 = _sample_pr(1)
        pr2 = _sample_pr(2)

        def mock_check(pr):
            if pr.number == 1:
                raise GhCommandError(["gh"], 1, "deleted fork")
            return HealthReport(pr=pr, status=MergeStatus.READY)

        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr1, pr2]),
            patch("pr_owl.cli.check_pr", side_effect=mock_check),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = runner.invoke(cli, ["audit"])
        # Should not crash — both PRs appear in output
        assert result.exit_code == 0

    def test_repo_filter(self):
        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[]),
        ]
        with patches[0], patches[1], patches[2], patches[3]:
            result = runner.invoke(cli, ["audit", "--repo", "acme/repo"])
        assert result.exit_code == 0

    def test_stale_days_filter(self):
        pr = _sample_pr()
        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr]),
            patch("pr_owl.cli.filter_stale", return_value=[]),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = runner.invoke(cli, ["audit", "--stale-days", "7"])
        assert result.exit_code == 0

    def test_fix_confirmation(self):
        pr = _sample_pr()
        report = HealthReport(
            pr=pr,
            status=MergeStatus.BEHIND,
            blockers=[Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
        )

        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr]),
            patch("pr_owl.cli.check_pr", return_value=report),
            patch(
                "pr_owl.cli.fix_pr",
                return_value=FixResult(pr=pr, success=True, command_run="gh pr update-branch"),
            ),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            # Say "y" to confirmation
            result = runner.invoke(cli, ["audit", "--fix"], input="y\n")
        assert result.exit_code == 0

    def test_fix_yes_skips_confirmation(self):
        pr = _sample_pr()
        report = HealthReport(
            pr=pr,
            status=MergeStatus.BEHIND,
            blockers=[Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
        )

        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr]),
            patch("pr_owl.cli.check_pr", return_value=report),
            patch(
                "pr_owl.cli.fix_pr",
                return_value=FixResult(pr=pr, success=True, command_run="gh pr update-branch"),
            ),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = runner.invoke(cli, ["audit", "--fix", "--yes"])
        assert result.exit_code == 0

    def test_json_output(self):
        pr = _sample_pr()
        report = HealthReport(pr=pr, status=MergeStatus.READY)

        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr]),
            patch("pr_owl.cli.check_pr", return_value=report),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = runner.invoke(cli, ["audit", "--json"])
        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        assert len(data) == 1

    def test_no_prs_found(self):
        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[]),
        ]
        with patches[0], patches[1], patches[2], patches[3]:
            result = runner.invoke(cli, ["audit"])
        assert result.exit_code == 0

    def test_version(self):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "pr-owl" in result.output or "version" in result.output.lower()
