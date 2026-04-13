"""Tests for pr_owl.cli."""

from __future__ import annotations

from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from pr_owl.cli import _parse_duration, cli
from pr_owl.exceptions import GhCommandError, GhNotFoundError
from pr_owl.models import (
    Blocker,
    BlockerType,
    ClosedDisposition,
    ClosedPRInfo,
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
        assert isinstance(data, dict)
        assert len(data["open"]) == 1

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
        assert len(data["open"]) == 1
        assert data["open"][0]["status"] == "READY"

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
        assert isinstance(data, dict)
        assert len(data["open"]) == 1
        assert data["open"][0]["status"] == "READY"
        assert "closed" in data
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
            result = runner.invoke(cli, ["audit", "--json", "--no-closed"])
        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        statuses = {d["pr"]["number"]: d["status"] for d in data["open"]}
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
            result = runner.invoke(cli, ["audit", "--json", "--no-closed"])
        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        assert data["open"][0]["status"] == "UNKNOWN"

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
            result = runner.invoke(cli, ["audit", "--json", "--no-closed"])
        assert result.exit_code == 0
        # check_pr should be called exactly once (no retry)
        mock_check_cm.assert_called_once()
        # sleep should not be called (no retry delay)
        mock_sleep_cm.assert_not_called()


class TestStateFlags:
    """Audit pipeline interaction with state load/save and the new flags."""

    def _isolate(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        return tmp_path / "pr-owl" / "seen.json"

    def _run_audit(self, pr, report, *args):
        patches = _mock_preflight() + [
            patch("pr_owl.cli.discover_prs", return_value=[pr]),
            patch("pr_owl.cli.check_pr", return_value=report),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            return runner.invoke(cli, ["audit", *args])

    def test_default_audit_creates_state_file(self, tmp_path, monkeypatch):
        state_file = self._isolate(monkeypatch, tmp_path)
        pr = _sample_pr(1)
        report = HealthReport(pr=pr, status=MergeStatus.READY, issue_comment_count=3, review_event_count=1)
        result = self._run_audit(pr, report)
        assert result.exit_code == 0
        assert state_file.exists()
        import json as _json

        data = _json.loads(state_file.read_text())
        assert data["prs"][pr.url]["issue_comments"] == 3
        assert data["prs"][pr.url]["review_events"] == 1

    def test_first_run_emits_hint(self, tmp_path, monkeypatch):
        self._isolate(monkeypatch, tmp_path)
        pr = _sample_pr(1)
        report = HealthReport(pr=pr, status=MergeStatus.READY)
        result = self._run_audit(pr, report)
        assert "Comment tracking enabled" in result.output

    def test_second_run_no_hint(self, tmp_path, monkeypatch):
        self._isolate(monkeypatch, tmp_path)
        pr = _sample_pr(1)
        report = HealthReport(pr=pr, status=MergeStatus.READY, issue_comment_count=2)
        # First run primes state.
        self._run_audit(pr, report)
        # Second run should not show the first-run hint.
        result = self._run_audit(pr, report)
        assert "Comment tracking enabled" not in result.output

    def test_no_state_skips_state_file(self, tmp_path, monkeypatch):
        state_file = self._isolate(monkeypatch, tmp_path)
        pr = _sample_pr(1)
        report = HealthReport(pr=pr, status=MergeStatus.READY, issue_comment_count=5)
        result = self._run_audit(pr, report, "--no-state")
        assert result.exit_code == 0
        assert not state_file.exists()

    def test_status_filter_skips_save(self, tmp_path, monkeypatch):
        state_file = self._isolate(monkeypatch, tmp_path)
        pr = _sample_pr(1)
        report = HealthReport(pr=pr, status=MergeStatus.READY, issue_comment_count=5)
        result = self._run_audit(pr, report, "--status", "READY")
        assert result.exit_code == 0
        # No save with --status filter.
        assert not state_file.exists()

    def test_author_other_skips_state(self, tmp_path, monkeypatch):
        state_file = self._isolate(monkeypatch, tmp_path)
        pr = _sample_pr(1)
        report = HealthReport(pr=pr, status=MergeStatus.READY, issue_comment_count=5)
        result = self._run_audit(pr, report, "--author", "octocat")
        assert result.exit_code == 0
        assert not state_file.exists()

    def test_peek_loads_state_but_skips_save(self, tmp_path, monkeypatch):
        state_file = self._isolate(monkeypatch, tmp_path)
        pr = _sample_pr(1)
        # Prime state with a baseline.
        baseline = HealthReport(pr=pr, status=MergeStatus.READY, issue_comment_count=2, review_event_count=0)
        self._run_audit(pr, baseline)
        baseline_mtime = state_file.stat().st_mtime
        # Now run --peek with a higher count.
        bumped = HealthReport(pr=pr, status=MergeStatus.READY, issue_comment_count=5, review_event_count=0)
        import time as _time

        _time.sleep(0.01)
        result = self._run_audit(pr, bumped, "--peek")
        assert result.exit_code == 0
        # New activity should display with * indicator.
        assert "5*" in result.output
        # File mtime should be unchanged — peek did not save.
        assert state_file.stat().st_mtime == baseline_mtime

    def test_delta_displayed_on_normal_run(self, tmp_path, monkeypatch):
        self._isolate(monkeypatch, tmp_path)
        pr = _sample_pr(1)
        baseline = HealthReport(pr=pr, status=MergeStatus.READY, issue_comment_count=2)
        self._run_audit(pr, baseline)
        bumped = HealthReport(pr=pr, status=MergeStatus.READY, issue_comment_count=5)
        result = self._run_audit(pr, bumped)
        assert "5*" in result.output

    def test_delta_marked_seen_after_normal_run(self, tmp_path, monkeypatch):
        self._isolate(monkeypatch, tmp_path)
        pr = _sample_pr(1)
        baseline = HealthReport(pr=pr, status=MergeStatus.READY, issue_comment_count=2)
        self._run_audit(pr, baseline)
        bumped = HealthReport(pr=pr, status=MergeStatus.READY, issue_comment_count=5)
        self._run_audit(pr, bumped)  # marks as seen
        result = self._run_audit(pr, bumped)  # third run, no new activity
        # Count should show without * (no new activity). The * only appears
        # when there are new comments since last audit.
        assert "5*" not in result.output

    def test_error_path_does_not_clobber_baseline(self, tmp_path, monkeypatch):
        state_file = self._isolate(monkeypatch, tmp_path)
        pr = _sample_pr(1)
        good = HealthReport(pr=pr, status=MergeStatus.READY, issue_comment_count=10)
        self._run_audit(pr, good)
        # Now simulate a failure for the same PR.
        errored = HealthReport(pr=pr, status=MergeStatus.UNKNOWN, error="api failure")
        self._run_audit(pr, errored)
        import json as _json

        data = _json.loads(state_file.read_text())
        # Baseline must be preserved at 10, NOT clobbered by the error path's 0.
        assert data["prs"][pr.url]["issue_comments"] == 10


class TestStatePathSubcommand:
    def test_state_path_prints_resolved_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        result = runner.invoke(cli, ["state", "path"])
        assert result.exit_code == 0
        assert str(tmp_path / "pr-owl" / "seen.json") in result.output


# ---------------------------------------------------------------------------
# _parse_duration
# ---------------------------------------------------------------------------

# _parse_duration is a Click callback — build a minimal context + param.
_DUMMY_CTX = click.Context(click.Command("test"))
_DUMMY_PARAM = click.Option(["--closed-since"])


def test_parse_duration_7d():
    from datetime import datetime, timezone

    result = _parse_duration(_DUMMY_CTX, _DUMMY_PARAM, "7d")
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    # Should be roughly 7 days ago.
    diff = datetime.now(tz=timezone.utc) - result
    assert 6.9 < diff.total_seconds() / 86400 < 7.1


def test_parse_duration_2w():
    from datetime import datetime, timezone

    result = _parse_duration(_DUMMY_CTX, _DUMMY_PARAM, "2w")
    diff = datetime.now(tz=timezone.utc) - result
    assert 13.9 < diff.total_seconds() / 86400 < 14.1


def test_parse_duration_1m():
    from datetime import datetime, timezone

    result = _parse_duration(_DUMMY_CTX, _DUMMY_PARAM, "1m")
    diff = datetime.now(tz=timezone.utc) - result
    assert 29.9 < diff.total_seconds() / 86400 < 30.1


def test_parse_duration_3m():
    from datetime import datetime, timezone

    result = _parse_duration(_DUMMY_CTX, _DUMMY_PARAM, "3m")
    diff = datetime.now(tz=timezone.utc) - result
    assert 89.9 < diff.total_seconds() / 86400 < 90.1


def test_parse_duration_iso_date():
    from datetime import datetime

    result = _parse_duration(_DUMMY_CTX, _DUMMY_PARAM, "2026-03-01")
    assert isinstance(result, datetime)
    assert result.year == 2026
    assert result.month == 3
    assert result.day == 1
    assert result.tzinfo is not None


def test_parse_duration_zero_raises():
    with pytest.raises(click.BadParameter, match="positive"):
        _parse_duration(_DUMMY_CTX, _DUMMY_PARAM, "0d")


def test_parse_duration_negative_raises():
    with pytest.raises(click.BadParameter, match="positive"):
        _parse_duration(_DUMMY_CTX, _DUMMY_PARAM, "-3d")


def test_parse_duration_unsupported_unit_raises():
    with pytest.raises(click.BadParameter, match="Invalid duration"):
        _parse_duration(_DUMMY_CTX, _DUMMY_PARAM, "1y")


def test_parse_duration_garbage_raises():
    with pytest.raises(click.BadParameter, match="Invalid duration"):
        _parse_duration(_DUMMY_CTX, _DUMMY_PARAM, "banana")


def test_parse_duration_none_returns_none():
    assert _parse_duration(_DUMMY_CTX, _DUMMY_PARAM, None) is None


# ---------------------------------------------------------------------------
# Closed-PR CLI integration tests
# ---------------------------------------------------------------------------


def _sample_closed_pr(number=7, state="merged"):
    """Build a ClosedPRInfo for test mocking."""
    pr = PRInfo(
        number=number,
        title=f"Closed PR #{number}",
        repo="acme/repo",
        url=f"https://github.com/acme/repo/pull/{number}",
        is_draft=False,
        created_at="2026-04-01T10:00:00Z",
        updated_at="2026-04-10T12:00:00Z",
    )
    return ClosedPRInfo(
        pr=pr,
        disposition=ClosedDisposition.MERGED if state == "merged" else ClosedDisposition.CLOSED,
        days_open=9,
        review_count=2,
        closed_at="2026-04-10T12:00:00Z",
    )


def test_no_closed_flag_suppresses_closed_table():
    pr = _sample_pr()
    report = HealthReport(pr=pr, status=MergeStatus.READY)

    patches = _mock_preflight() + [
        patch("pr_owl.cli.discover_prs", return_value=[pr]),
        patch("pr_owl.cli.check_pr", return_value=report),
        patch("pr_owl.cli.discover_closed_prs", return_value=[_sample_closed_pr()]),
    ]
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = runner.invoke(cli, ["audit", "--no-closed"])
    assert result.exit_code == 0
    assert "Recently closed" not in result.output


def test_closed_since_shows_closed_table():
    pr = _sample_pr()
    report = HealthReport(pr=pr, status=MergeStatus.READY)
    closed = _sample_closed_pr()

    patches = _mock_preflight() + [
        patch("pr_owl.cli.discover_prs", return_value=[pr]),
        patch("pr_owl.cli.check_pr", return_value=report),
        patch("pr_owl.cli.discover_closed_prs", return_value=[closed]),
    ]
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = runner.invoke(cli, ["audit", "--closed-since", "7d"])
    assert result.exit_code == 0
    assert "Recently closed" in result.output


def test_no_closed_wins_over_closed_since():
    pr = _sample_pr()
    report = HealthReport(pr=pr, status=MergeStatus.READY)
    mock_discover_closed = patch("pr_owl.cli.discover_closed_prs", return_value=[_sample_closed_pr()])

    patches = _mock_preflight() + [
        patch("pr_owl.cli.discover_prs", return_value=[pr]),
        patch("pr_owl.cli.check_pr", return_value=report),
        mock_discover_closed,
    ]
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as mock_dc:
        result = runner.invoke(cli, ["audit", "--no-closed", "--closed-since", "7d"])
    assert result.exit_code == 0
    assert "Recently closed" not in result.output
    mock_dc.assert_not_called()


def test_json_output_includes_closed():
    pr = _sample_pr()
    report = HealthReport(pr=pr, status=MergeStatus.READY)
    closed = _sample_closed_pr()

    patches = _mock_preflight() + [
        patch("pr_owl.cli.discover_prs", return_value=[pr]),
        patch("pr_owl.cli.check_pr", return_value=report),
        patch("pr_owl.cli.discover_closed_prs", return_value=[closed]),
    ]
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = runner.invoke(cli, ["audit", "--json", "--closed-since", "7d"])
    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    assert "open" in data
    assert "closed" in data
    assert len(data["open"]) == 1
    assert len(data["closed"]) == 1
    assert data["closed"][0]["disposition"] == "MERGED"


def test_json_no_open_prs_still_has_shape():
    patches = _mock_preflight() + [
        patch("pr_owl.cli.discover_prs", return_value=[]),
        patch("pr_owl.cli.discover_closed_prs", return_value=[]),
    ]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = runner.invoke(cli, ["audit", "--json", "--closed-since", "7d"])
    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    assert "open" in data
    assert "closed" in data
    assert data["open"] == []
    assert data["closed"] == []


def test_zero_open_prs_closed_table_still_shown():
    """When there are 0 open PRs but closed PRs exist, the closed table is shown."""
    closed = _sample_closed_pr()

    patches = _mock_preflight() + [
        patch("pr_owl.cli.discover_prs", return_value=[]),
        patch("pr_owl.cli.discover_closed_prs", return_value=[closed]),
    ]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = runner.invoke(cli, ["audit", "--closed-since", "7d"])
    assert result.exit_code == 0
    assert "Recently closed" in result.output


def test_enrich_closed_prs_failure_is_non_fatal():
    """When gh pr view fails for a closed PR, the PR keeps review_count=0."""
    from pr_owl.cli import _enrich_closed_prs
    from pr_owl.exceptions import GhCommandError

    closed = _sample_closed_pr()
    closed.review_count = 0  # reset to pre-enrichment default

    with patch("pr_owl.cli.gh.view_pr", side_effect=GhCommandError(["gh"], 1, "rate limited")):
        _enrich_closed_prs([closed], workers=1)

    # review_count stays at 0 — failure is non-fatal
    assert closed.review_count == 0


def test_enrich_closed_prs_success_populates_review_count():
    """When gh pr view succeeds, review_count is populated from the reviews array."""
    from pr_owl.cli import _enrich_closed_prs

    closed = _sample_closed_pr()
    mock_data = {"reviews": [{"state": "APPROVED"}, {"state": "CHANGES_REQUESTED"}]}

    with patch("pr_owl.cli.gh.view_pr", return_value=mock_data):
        _enrich_closed_prs([closed], workers=1)

    assert closed.review_count == 2
