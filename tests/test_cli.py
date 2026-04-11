"""Tests for pr_owl.cli."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from pr_owl.cli import cli
from pr_owl.exceptions import GhCommandError, GhNotFoundError
from pr_owl.models import (
    Blocker,
    BlockerType,
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

    def test_resilience_key_error(self):
        """KeyError from malformed JSON response shouldn't crash the audit."""
        pr1 = _sample_pr(1)
        pr2 = _sample_pr(2)

        def mock_check(pr):
            if pr.number == 1:
                raise KeyError("missing_field")
            return HealthReport(pr=pr, status=MergeStatus.READY)

        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr1, pr2]),
            patch("pr_owl.cli.check_pr", side_effect=mock_check),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = runner.invoke(cli, ["audit"])
        assert result.exit_code == 0

    def test_resilience_json_decode_error(self):
        """JSONDecodeError from broken gh output shouldn't crash the audit."""
        import json

        pr1 = _sample_pr(1)
        pr2 = _sample_pr(2)

        def mock_check(pr):
            if pr.number == 1:
                raise json.JSONDecodeError("bad json", "", 0)
            return HealthReport(pr=pr, status=MergeStatus.READY)

        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr1, pr2]),
            patch("pr_owl.cli.check_pr", side_effect=mock_check),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = runner.invoke(cli, ["audit"])
        assert result.exit_code == 0

    def test_resilience_attribute_error(self):
        """AttributeError from null nested API fields shouldn't crash the audit."""
        pr1 = _sample_pr(1)
        pr2 = _sample_pr(2)

        def mock_check(pr):
            if pr.number == 1:
                raise AttributeError("'NoneType' object has no attribute 'get'")
            return HealthReport(pr=pr, status=MergeStatus.READY)

        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr1, pr2]),
            patch("pr_owl.cli.check_pr", side_effect=mock_check),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = runner.invoke(cli, ["audit"])
        assert result.exit_code == 0

    def test_resilience_type_error(self):
        """TypeError from non-iterable API response fields shouldn't crash the audit."""
        pr1 = _sample_pr(1)
        pr2 = _sample_pr(2)

        def mock_check(pr):
            if pr.number == 1:
                raise TypeError("'NoneType' object is not iterable")
            return HealthReport(pr=pr, status=MergeStatus.READY)

        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr1, pr2]),
            patch("pr_owl.cli.check_pr", side_effect=mock_check),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = runner.invoke(cli, ["audit"])
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

    def test_preflight_gh_not_found(self):
        """Missing gh CLI exits with code 1."""
        with patch("pr_owl.cli.gh.ensure_gh", side_effect=GhNotFoundError("gh not found")):
            result = runner.invoke(cli, ["audit"])
        assert result.exit_code == 1

    def test_status_filter_valid(self):
        pr = _sample_pr()
        ready_report = HealthReport(pr=pr, status=MergeStatus.READY)
        behind_pr = _sample_pr(number=2)
        behind_report = HealthReport(pr=behind_pr, status=MergeStatus.BEHIND)

        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr, behind_pr]),
            patch("pr_owl.cli.check_pr", side_effect=[ready_report, behind_report]),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = runner.invoke(cli, ["audit", "--status", "READY", "--json"])
        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["status"] == "READY"

    def test_status_filter_invalid(self):
        pr = _sample_pr()
        report = HealthReport(pr=pr, status=MergeStatus.READY)

        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr]),
            patch("pr_owl.cli.check_pr", return_value=report),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = runner.invoke(cli, ["audit", "--status", "BOGUS"])
        assert result.exit_code == 1

    def test_details_flag(self):
        pr = _sample_pr()
        report = HealthReport(
            pr=pr,
            status=MergeStatus.BEHIND,
            blockers=[Blocker(type=BlockerType.BEHIND_BASE, description="Branch is behind")],
        )

        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr]),
            patch("pr_owl.cli.check_pr", return_value=report),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = runner.invoke(cli, ["audit", "--details"])
        assert result.exit_code == 0

    def test_version(self):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "pr-owl" in result.output or "version" in result.output.lower()

    def test_author_flag_threaded_to_discovery(self):
        """--author X passes through to discover_prs and skips get_current_user."""
        pr = _sample_pr()
        report = HealthReport(pr=pr, status=MergeStatus.READY)

        with (
            patch("pr_owl.cli.gh.ensure_gh", return_value="/usr/local/bin/gh"),
            patch("pr_owl.cli.gh.check_auth"),
            patch("pr_owl.cli.gh.get_current_user", return_value="testuser") as mock_user,
            patch("pr_owl.cli.discover_prs", return_value=[pr]) as mock_discover,
            patch("pr_owl.cli.check_pr", return_value=report),
        ):
            result = runner.invoke(cli, ["audit", "--author", "octocat"])
        assert result.exit_code == 0
        mock_discover.assert_called_once_with(author="octocat", repo="", org="")
        mock_user.assert_not_called()

    def test_author_flag_header_shows_target(self):
        """--author X renders @X in the summary header, not the viewer."""
        pr = _sample_pr()
        report = HealthReport(pr=pr, status=MergeStatus.READY)

        with (
            patch("pr_owl.cli.gh.ensure_gh", return_value="/usr/local/bin/gh"),
            patch("pr_owl.cli.gh.check_auth"),
            patch("pr_owl.cli.gh.get_current_user", return_value="testuser"),
            patch("pr_owl.cli.discover_prs", return_value=[pr]),
            patch("pr_owl.cli.check_pr", return_value=report),
        ):
            result = runner.invoke(cli, ["audit", "--author", "octocat"])
        assert result.exit_code == 0
        assert "@octocat" in result.output
        assert "@testuser" not in result.output

    def test_default_author_still_uses_current_user(self):
        """Regression: default invocation still resolves the viewer via get_current_user."""
        pr = _sample_pr()
        report = HealthReport(pr=pr, status=MergeStatus.READY)

        with (
            patch("pr_owl.cli.gh.ensure_gh", return_value="/usr/local/bin/gh"),
            patch("pr_owl.cli.gh.check_auth"),
            patch("pr_owl.cli.gh.get_current_user", return_value="testuser") as mock_user,
            patch("pr_owl.cli.discover_prs", return_value=[pr]) as mock_discover,
            patch("pr_owl.cli.check_pr", return_value=report),
        ):
            result = runner.invoke(cli, ["audit"])
        assert result.exit_code == 0
        mock_user.assert_called_once()
        mock_discover.assert_called_once_with(author="@me", repo="", org="")
        assert "@testuser" in result.output

    def test_author_details_shows_reframing(self):
        """--author X --details prints the 'Viewing @X's PRs' notice."""
        pr = _sample_pr()
        report = HealthReport(
            pr=pr,
            status=MergeStatus.BEHIND,
            blockers=[Blocker(type=BlockerType.BEHIND_BASE, description="Behind base")],
        )

        with (
            patch("pr_owl.cli.gh.ensure_gh", return_value="/usr/local/bin/gh"),
            patch("pr_owl.cli.gh.check_auth"),
            patch("pr_owl.cli.gh.get_current_user", return_value="testuser"),
            patch("pr_owl.cli.discover_prs", return_value=[pr]),
            patch("pr_owl.cli.check_pr", return_value=report),
        ):
            result = runner.invoke(cli, ["audit", "--author", "octocat", "--details"])
        assert result.exit_code == 0
        assert "Viewing @octocat's PRs" in result.output

    def test_author_with_json_unchanged(self):
        """--author X --json produces well-formed JSON identical in shape to default."""
        pr = _sample_pr()
        report = HealthReport(pr=pr, status=MergeStatus.READY)

        with (
            patch("pr_owl.cli.gh.ensure_gh", return_value="/usr/local/bin/gh"),
            patch("pr_owl.cli.gh.check_auth"),
            patch("pr_owl.cli.gh.get_current_user", return_value="testuser"),
            patch("pr_owl.cli.discover_prs", return_value=[pr]),
            patch("pr_owl.cli.check_pr", return_value=report),
        ):
            result = runner.invoke(cli, ["audit", "--author", "octocat", "--json"])
        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["status"] == "READY"
        # JSON must not contain the human-readable reframing notice
        assert "Viewing" not in result.output

    def test_author_normalizes_leading_at(self):
        """--author @octocat is normalized to octocat (no @@ in output, no broken gh call)."""
        pr = _sample_pr()
        report = HealthReport(pr=pr, status=MergeStatus.READY)

        with (
            patch("pr_owl.cli.gh.ensure_gh", return_value="/usr/local/bin/gh"),
            patch("pr_owl.cli.gh.check_auth"),
            patch("pr_owl.cli.gh.get_current_user", return_value="testuser"),
            patch("pr_owl.cli.discover_prs", return_value=[pr]) as mock_discover,
            patch("pr_owl.cli.check_pr", return_value=report),
        ):
            result = runner.invoke(cli, ["audit", "--author", "@octocat"])
        assert result.exit_code == 0
        mock_discover.assert_called_once_with(author="octocat", repo="", org="")
        assert "@octocat" in result.output
        assert "@@octocat" not in result.output

    def test_author_at_me_sentinel_preserved(self):
        """--author @me explicitly behaves identically to default; @me is not stripped."""
        pr = _sample_pr()
        report = HealthReport(pr=pr, status=MergeStatus.READY)

        with (
            patch("pr_owl.cli.gh.ensure_gh", return_value="/usr/local/bin/gh"),
            patch("pr_owl.cli.gh.check_auth"),
            patch("pr_owl.cli.gh.get_current_user", return_value="testuser") as mock_user,
            patch("pr_owl.cli.discover_prs", return_value=[pr]) as mock_discover,
            patch("pr_owl.cli.check_pr", return_value=report),
        ):
            result = runner.invoke(cli, ["audit", "--author", "@me"])
        assert result.exit_code == 0
        mock_user.assert_called_once()
        mock_discover.assert_called_once_with(author="@me", repo="", org="")
        assert "@testuser" in result.output

    def test_unknown_retry_resolves(self):
        """UNKNOWN mergeable PRs are retried and resolved."""
        pr1 = _sample_pr(1)
        pr2 = _sample_pr(2)

        unknown_report = HealthReport(pr=pr1, status=MergeStatus.UNKNOWN, mergeable="UNKNOWN")
        blocked_report = HealthReport(
            pr=pr1,
            status=MergeStatus.BLOCKED,
            mergeable="MERGEABLE",
            blockers=[Blocker(type=BlockerType.MISSING_REVIEWS, description="Review required")],
        )
        ready_report = HealthReport(pr=pr2, status=MergeStatus.READY)

        call_count = 0

        def mock_check(pr):
            nonlocal call_count
            call_count += 1
            if pr.number == 1:
                # First call returns UNKNOWN, second call returns BLOCKED
                if call_count <= 2:
                    return unknown_report
                return blocked_report
            return ready_report

        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr1, pr2]),
            patch("pr_owl.cli.check_pr", side_effect=mock_check),
            patch("pr_owl.cli.time.sleep"),
            patch("pr_owl.cli.time.monotonic", side_effect=[0.0, 0.5, 0.5, 3.0]),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = runner.invoke(cli, ["audit", "--json"])
        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        statuses = {d["pr"]["number"]: d["status"] for d in data}
        assert statuses[1] == "BLOCKED"
        assert statuses[2] == "READY"

    def test_unknown_retry_still_unknown(self):
        """PRs that stay UNKNOWN after retry are reported as UNKNOWN."""
        pr = _sample_pr(1)

        unknown_report = HealthReport(pr=pr, status=MergeStatus.UNKNOWN, mergeable="UNKNOWN")

        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr]),
            patch("pr_owl.cli.check_pr", return_value=unknown_report),
            patch("pr_owl.cli.time.sleep"),
            patch("pr_owl.cli.time.monotonic", side_effect=[0.0, 0.5, 3.0]),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = runner.invoke(cli, ["audit", "--json"])
        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        assert data[0]["status"] == "UNKNOWN"

    def test_unknown_no_retry_for_errors(self):
        """Exception-caused UNKNOWNs (mergeable='') are not retried."""
        pr = _sample_pr(1)

        def mock_check(pr):
            raise GhCommandError(["gh"], 1, "connection refused")

        mock_check_patch = patch("pr_owl.cli.check_pr", side_effect=mock_check)
        mock_sleep_patch = patch("pr_owl.cli.time.sleep")
        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr]),
            mock_check_patch,
            mock_sleep_patch,
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4] as mock_check_cm, patches[5] as mock_sleep_cm:
            result = runner.invoke(cli, ["audit", "--json"])
        assert result.exit_code == 0
        # check_pr should be called exactly once (no retry)
        mock_check_cm.assert_called_once()
        # sleep should not be called (no retry delay)
        mock_sleep_cm.assert_not_called()
