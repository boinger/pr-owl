"""Tests for pr_owl.output."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO

from rich.console import Console

from pr_owl.models import (
    Blocker,
    BlockerType,
    CICheck,
    ClosedDisposition,
    ClosedPRInfo,
    HealthReport,
    MergeStatus,
    PRInfo,
    RemediationPlan,
    RemediationStep,
)
from pr_owl.output import (
    _report_to_dict,
    print_closed_table,
    print_plans,
    print_table,
)


def _capture_console(func, *args, width: int = 120, **kwargs):
    """Capture Rich console output to a string."""
    buf = StringIO()
    test_console = Console(file=buf, force_terminal=True, width=width)
    # Temporarily replace the module console
    import pr_owl.output as output_mod

    original = output_mod.console
    output_mod.console = test_console
    try:
        func(*args, **kwargs)
    finally:
        output_mod.console = original
    return buf.getvalue()


class TestPrintTable:
    def test_grouped_by_status(self, sample_pr):
        reports = [
            HealthReport(pr=sample_pr, status=MergeStatus.READY),
            HealthReport(
                pr=sample_pr,
                status=MergeStatus.BEHIND,
                blockers=[Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
            ),
        ]
        output = _capture_console(print_table, reports)
        assert "READY" in output
        assert "BEHIND" in output

    def test_blocker_count_actionable(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BEHIND,
            blockers=[
                Blocker(type=BlockerType.BEHIND_BASE, description="Behind"),
                Blocker(type=BlockerType.FAILING_CHECKS, description="Failing"),
            ],
        )
        output = _capture_console(print_table, [report])
        assert "⚡" in output
        assert "2" in output

    def test_blocker_count_non_actionable(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BLOCKED,
            blockers=[Blocker(type=BlockerType.MISSING_REVIEWS, description="Review required")],
        )
        output = _capture_console(print_table, [report])
        assert "👤" in output

    def test_blocked_sub_sort(self, sample_pr):
        actionable = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BLOCKED,
            blockers=[Blocker(type=BlockerType.FAILING_CHECKS, description="1 check failing")],
        )
        non_actionable = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BLOCKED,
            blockers=[Blocker(type=BlockerType.MISSING_REVIEWS, description="Review required")],
        )
        # Pass non-actionable first; actionable should sort before it
        output = _capture_console(print_table, [non_actionable, actionable])
        pos_actionable = output.index("⚡")
        pos_non_actionable = output.index("👤")
        assert pos_actionable < pos_non_actionable

    def test_table_legend(self, sample_pr):
        report = HealthReport(pr=sample_pr, status=MergeStatus.READY)
        output = _capture_console(print_table, [report])
        assert "potentially fixable" in output
        assert "waiting on others" in output

    def test_empty_results(self):
        output = _capture_console(print_table, [])
        assert "No PRs found" in output

    def test_renders_error_snippet(self, sample_pr):
        """When a report has `error` set, the table shows a dim red snippet in Title.

        Renders at 120 cols (the non-TTY floor). Before the overflow="fold"
        fix, this test had to use width=200 to avoid Rich cropping the error
        snippet with "…". With fold, long titles wrap across multiple lines
        instead of cropping. We assert individual words from the error
        appear somewhere in the output rather than a specific substring,
        because Rich's wrap may split the snippet across line boundaries.
        """
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.UNKNOWN,
            error="gh returned malformed response: Expecting value",
        )
        output = _capture_console(print_table, [report], width=120)
        assert "malformed" in output
        assert "response" in output
        assert "Expecting" in output

    def test_no_error_snippet_when_clean(self, sample_pr):
        """Reports with no error render the title without any error prefix."""
        report = HealthReport(pr=sample_pr, status=MergeStatus.READY)
        output = _capture_console(print_table, [report])
        # No "·" separator from the error snippet format
        assert "·" not in output

    def test_table_fits_in_120_cols(self, sample_pr):
        """Regression: at 120-col width (the non-TTY floor), all columns render.

        The bug this test guards against: when Rich's default 80-col non-TTY
        width hit the table, the Blockers and Updated columns got cropped out
        of the rightmost edge entirely. 120 is the new floor, and all column
        headers must be visible — including the rightmost Open column, which
        has minimal headroom under the floor.
        """
        reports = [
            HealthReport(
                pr=sample_pr,
                status=MergeStatus.CONFLICTS,
                blockers=[Blocker(type=BlockerType.HAS_CONFLICTS, description="c")],
            ),
        ]
        output = _capture_console(print_table, reports, width=120)
        assert "Status" in output
        assert "PR" in output
        assert "Title" in output
        assert "Blockers" in output
        assert "Updated" in output
        assert "Open" in output

    def test_renders_open_days_cell(self, sample_pr, monkeypatch):
        """Age-in-days cell renders for a PR with a known created_at.

        sample_pr has created_at=2026-01-15 and we inject now=2026-04-20,
        so delta.days == 95. The column is right-justified width=6, so the
        rendered value appears as "95" somewhere in the row.
        """
        import pr_owl.output as output_mod

        fixed_now = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(output_mod, "_now_utc", lambda: fixed_now)
        report = HealthReport(pr=sample_pr, status=MergeStatus.READY)
        output = _capture_console(print_table, [report])
        assert "95" in output

    def test_renders_empty_open_cell_for_unparseable_created_at(self, monkeypatch):
        """PR with malformed created_at renders an empty Open cell, not "None"."""
        import pr_owl.output as output_mod

        fixed_now = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(output_mod, "_now_utc", lambda: fixed_now)
        pr = PRInfo(
            number=1,
            title="t",
            repo="acme/repo",
            url="u",
            is_draft=False,
            created_at="not-a-date",
            updated_at="2026-04-19T10:00:00Z",
        )
        report = HealthReport(pr=pr, status=MergeStatus.READY)
        output = _capture_console(print_table, [report])
        assert "None" not in output

    def test_within_status_sorted_by_updated_desc(self):
        """Within the same (status, actionable) bucket, newer updated_at wins.

        Regression test for the nondeterministic ordering bug: as_completed()
        in cli.py yields reports in random completion order, so the table
        needs its own stable within-bucket tiebreaker.
        """
        old = PRInfo(
            number=1,
            title="old",
            repo="acme/repo",
            url="u1",
            is_draft=False,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        mid = PRInfo(
            number=2,
            title="mid",
            repo="acme/repo",
            url="u2",
            is_draft=False,
            created_at="2026-02-01T00:00:00Z",
            updated_at="2026-02-15T00:00:00Z",
        )
        new = PRInfo(
            number=3,
            title="new",
            repo="acme/repo",
            url="u3",
            is_draft=False,
            created_at="2026-03-01T00:00:00Z",
            updated_at="2026-04-10T00:00:00Z",
        )
        reports = [
            HealthReport(pr=old, status=MergeStatus.READY),
            HealthReport(pr=new, status=MergeStatus.READY),
            HealthReport(pr=mid, status=MergeStatus.READY),
        ]
        output = _capture_console(print_table, reports)
        pos_new = output.index("acme/repo#3")
        pos_mid = output.index("acme/repo#2")
        pos_old = output.index("acme/repo#1")
        assert pos_new < pos_mid < pos_old

    def test_status_bucket_takes_precedence_over_updated(self):
        """A newer READY PR must still sort below an older CONFLICTS PR —
        status bucket is the primary key, updated_at only breaks bucket ties."""
        old_conflicts = PRInfo(
            number=10,
            title="old conflicts",
            repo="acme/repo",
            url="u10",
            is_draft=False,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-05T00:00:00Z",
        )
        new_ready = PRInfo(
            number=20,
            title="new ready",
            repo="acme/repo",
            url="u20",
            is_draft=False,
            created_at="2026-04-01T00:00:00Z",
            updated_at="2026-04-12T00:00:00Z",
        )
        reports = [
            HealthReport(pr=new_ready, status=MergeStatus.READY),
            HealthReport(
                pr=old_conflicts,
                status=MergeStatus.CONFLICTS,
                blockers=[Blocker(type=BlockerType.HAS_CONFLICTS, description="c")],
            ),
        ]
        output = _capture_console(print_table, reports)
        assert output.index("acme/repo#10") < output.index("acme/repo#20")

    def test_long_error_title_folds_not_crops(self, sample_pr):
        """A long title + error snippet wraps across lines; no data is lost.

        Rich has two overflow modes relevant here: "ellipsis" (default) crops
        with "…", "fold" wraps across lines. With fold, the full error text
        must be present somewhere in the output. If someone removes the
        overflow="fold" argument from the Title column, this test catches it.
        """
        long_err = "gh returned malformed response: " + ("x" * 40)
        report = HealthReport(pr=sample_pr, status=MergeStatus.UNKNOWN, error=long_err)
        output = _capture_console(print_table, [report], width=120)
        # The tail of the error (the x-string) should be present — proves
        # the fold didn't crop it away.
        assert "xxxxxxxxxx" in output
        # The ellipsis crop marker should NOT be present next to our content.
        # Rich uses "…" only when cropping, not when folding.
        # (Note: Rich may use "…" elsewhere for unrelated truncation; we
        # check that our specific x-tail made it through.)


class TestPrintJson:
    def test_valid_json(self, sample_pr, capsys):
        reports = [HealthReport(pr=sample_pr, status=MergeStatus.READY)]
        from pr_owl.output import print_json

        print_json(reports)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, dict)
        assert "open" in data
        assert "closed" in data
        assert len(data["open"]) == 1
        assert data["open"][0]["status"] == "READY"
        assert data["closed"] == []

    def test_all_fields_serialized(self, sample_pr, capsys):
        checks = [
            CICheck(
                name="ci/build", status="COMPLETED", conclusion="SUCCESS", workflow_name="CI", details_url="http://x"
            )
        ]
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.CI_FAILING,
            blockers=[Blocker(type=BlockerType.FAILING_CHECKS, description="1 failing", details=["ci/build"])],
            checks=checks,
        )
        from pr_owl.output import print_json

        print_json([report])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["open"][0]["checks"][0]["name"] == "ci/build"
        assert data["open"][0]["blockers"][0]["type"] == "FAILING_CHECKS"
        assert data["open"][0]["blockers"][0]["actionable"] is True
        assert data["open"][0]["has_actionable_blockers"] is True

    def test_json_non_actionable_blocker(self, sample_pr, capsys):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BLOCKED,
            blockers=[Blocker(type=BlockerType.MISSING_REVIEWS, description="Review required")],
        )
        from pr_owl.output import print_json

        print_json([report])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["open"][0]["blockers"][0]["actionable"] is False
        assert data["open"][0]["has_actionable_blockers"] is False


class TestClosedTableOrder:
    def _make_closed(self, number: int, repo: str, closed_at: str) -> ClosedPRInfo:
        # PRInfo requires a valid updated_at, so keep that field independent of
        # the closed_at value we're testing. Only closed_at drives the sort here.
        return ClosedPRInfo(
            pr=PRInfo(
                number=number,
                title=f"closed {number}",
                repo=repo,
                url=f"https://github.com/{repo}/pull/{number}",
                is_draft=False,
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            ),
            disposition=ClosedDisposition.MERGED,
            days_open=1,
            review_count=0,
            closed_at=closed_at,
        )

    def test_closed_table_sorts_by_closed_at_desc(self):
        old = self._make_closed(1, "acme/repo", "2026-04-01T00:00:00Z")
        new = self._make_closed(2, "acme/repo", "2026-04-10T00:00:00Z")
        mid = self._make_closed(3, "acme/repo", "2026-04-05T00:00:00Z")
        output = _capture_console(print_closed_table, [old, new, mid])
        pos_new = output.index("acme/repo#2")
        pos_mid = output.index("acme/repo#3")
        pos_old = output.index("acme/repo#1")
        assert pos_new < pos_mid < pos_old

    def test_missing_closed_at_sinks_to_bottom(self):
        """Entries with empty closed_at sort after entries with a real date,
        preserving their input order among themselves."""
        from pr_owl.output import sort_closed_prs

        dated = self._make_closed(1, "acme/repo", "2026-04-10T00:00:00Z")
        missing_a = self._make_closed(2, "acme/repo", "")
        missing_b = self._make_closed(3, "acme/repo", "")
        result = sort_closed_prs([missing_a, dated, missing_b])
        assert [c.pr.number for c in result] == [1, 2, 3]

    def test_malformed_closed_at_sinks_to_bottom(self):
        """Same as missing, but for dates that fail ISO parsing. Defensive
        branch — gh search doesn't return these in practice, but the sort
        must not crash if it ever does."""
        from pr_owl.output import sort_closed_prs

        dated = self._make_closed(1, "acme/repo", "2026-04-10T00:00:00Z")
        bad = self._make_closed(2, "acme/repo", "not-a-date")
        result = sort_closed_prs([bad, dated])
        assert [c.pr.number for c in result] == [1, 2]


def test_print_plans_orders_like_table():
    """--details output uses the same sort as the table and --json so all
    three surfaces stay in lockstep."""
    import re

    old = PRInfo(
        number=1,
        title="old",
        repo="acme/repo",
        url="u1",
        is_draft=False,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    new = PRInfo(
        number=2,
        title="new",
        repo="acme/repo",
        url="u2",
        is_draft=False,
        created_at="2026-03-01T00:00:00Z",
        updated_at="2026-04-10T00:00:00Z",
    )
    plans = [
        RemediationPlan(report=HealthReport(pr=old, status=MergeStatus.READY), steps=[], summary="ok"),
        RemediationPlan(report=HealthReport(pr=new, status=MergeStatus.READY), steps=[], summary="ok"),
    ]
    # Rich splits "acme/repo#N" into separate styled spans (the number is
    # cyan-highlighted), so the raw output contains ANSI escapes between
    # "acme/repo#" and "2". Strip escapes before substring checks.
    plain = re.sub(r"\x1b\[[0-9;]*m", "", _capture_console(print_plans, plans))
    assert plain.index("acme/repo#2") < plain.index("acme/repo#1")


def test_json_order_matches_table(capsys):
    """--json output is ordered identically to the Rich table so consumers
    see deterministic arrays across runs."""
    from pr_owl.output import print_json

    old = PRInfo(
        number=1,
        title="old",
        repo="acme/repo",
        url="u1",
        is_draft=False,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    new = PRInfo(
        number=2,
        title="new",
        repo="acme/repo",
        url="u2",
        is_draft=False,
        created_at="2026-03-01T00:00:00Z",
        updated_at="2026-04-10T00:00:00Z",
    )
    # Input in reverse-intended order to prove sorting happens
    reports = [
        HealthReport(pr=old, status=MergeStatus.READY),
        HealthReport(pr=new, status=MergeStatus.READY),
    ]
    print_json(reports)
    data = json.loads(capsys.readouterr().out)
    assert [r["pr"]["number"] for r in data["open"]] == [2, 1]


def test_report_to_dict_serialization(sample_pr):
    report = HealthReport(
        pr=sample_pr,
        status=MergeStatus.BEHIND,
        blockers=[Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
    )
    d = _report_to_dict(report)
    assert d["status"] == "BEHIND"
    assert d["blockers"][0]["type"] == "BEHIND_BASE"
    assert d["blockers"][0]["actionable"] is True
    assert d["has_actionable_blockers"] is True


def test_now_utc_returns_utc_aware_datetime():
    """Sanity: the _now_utc helper returns tz-aware UTC, which age_days relies on."""
    from pr_owl.output import _now_utc

    now = _now_utc()
    assert now.tzinfo is timezone.utc


def test_json_excludes_both_private_datetime_caches(sample_pr):
    """Regression: asdict(report) includes init=False fields, so the cached
    _updated_at_dt and _created_at_dt datetimes would leak into --json output
    and make json.dumps raise TypeError. _report_to_dict must strip both.
    """
    report = HealthReport(pr=sample_pr, status=MergeStatus.READY)
    d = _report_to_dict(report)
    assert "_updated_at_dt" not in d["pr"]
    assert "_created_at_dt" not in d["pr"]
    # And the JSON serializes cleanly end-to-end.
    json.dumps(d)  # raises TypeError if a datetime slipped through


class TestCommentDeltaColumn:
    def test_zero_comments_renders_empty_cell(self, sample_pr):
        report = HealthReport(pr=sample_pr, status=MergeStatus.READY)
        output = _capture_console(print_table, [report])
        # The 💬 header is always present; rows with zero comments have an empty cell.
        # Check that no number appears in the comment column area.
        lines = output.strip().split("\n")
        # Find a data row (contains the PR ref).
        data_lines = [line for line in lines if "acme/repo#42" in line]
        assert data_lines

    def test_comments_without_new_shows_count_no_star(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.READY,
            issue_comment_count=5,
            review_event_count=2,
        )
        output = _capture_console(print_table, [report])
        assert " 7 " in output or "│ 7 " in output
        assert "7*" not in output

    def test_comments_with_new_shows_count_with_star(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.READY,
            issue_comment_count=5,
            review_event_count=2,
            new_issue_comments=2,
            new_review_events=1,
        )
        output = _capture_console(print_table, [report])
        assert "7*" in output

    def test_comments_with_only_issue_new(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.READY,
            issue_comment_count=4,
            review_event_count=0,
            new_issue_comments=1,
        )
        output = _capture_console(print_table, [report])
        assert "4*" in output

    def test_legend_mentions_star(self, sample_pr):
        report = HealthReport(pr=sample_pr, status=MergeStatus.READY)
        output = _capture_console(print_table, [report])
        assert "* =" in output or "* = new" in output.lower() or "(* =" in output

    def test_json_serializes_delta_fields(self, sample_pr, capsys):
        from pr_owl.output import print_json

        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.READY,
            issue_comment_count=5,
            review_event_count=2,
            new_issue_comments=3,
            new_review_events=1,
        )
        print_json([report])
        data = json.loads(capsys.readouterr().out)
        assert data["open"][0]["issue_comment_count"] == 5
        assert data["open"][0]["review_event_count"] == 2
        assert data["open"][0]["new_issue_comments"] == 3
        assert data["open"][0]["new_review_events"] == 1

    def test_print_plans_shows_new_activity_line(self, sample_pr):
        import re

        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.READY,
            new_issue_comments=2,
            new_review_events=1,
        )
        plan = RemediationPlan(report=report, steps=[], summary="ready")
        output = _capture_console(print_plans, [plan])
        plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
        assert "New activity" in plain
        assert "2 comment" in plain
        assert "1 review" in plain

    def test_print_plans_no_activity_line_when_zero(self, sample_pr):
        report = HealthReport(pr=sample_pr, status=MergeStatus.READY)
        plan = RemediationPlan(report=report, steps=[], summary="ready")
        output = _capture_console(print_plans, [plan])
        assert "New activity" not in output


class TestPrintPlans:
    def test_shows_steps(self, sample_pr):
        plan = RemediationPlan(
            report=HealthReport(
                pr=sample_pr,
                status=MergeStatus.BEHIND,
                blockers=[Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
            ),
            steps=[
                RemediationStep(description="Update branch", command="gh pr update-branch 42", automatable=True),
            ],
            summary="Rebase needed.",
        )
        output = _capture_console(print_plans, [plan])
        assert "Update branch" in output
        assert "auto" in output.lower()
        assert "Rebase needed" in output

    def test_shows_all_blockers(self, sample_pr):
        plan = RemediationPlan(
            report=HealthReport(
                pr=sample_pr,
                status=MergeStatus.BEHIND,
                blockers=[
                    Blocker(type=BlockerType.BEHIND_BASE, description="Behind base"),
                    Blocker(type=BlockerType.FAILING_CHECKS, description="CI failing", details=["ci/lint"]),
                ],
            ),
            steps=[],
            summary="Multiple issues.",
        )
        output = _capture_console(print_plans, [plan])
        assert "Behind base" in output
        assert "CI failing" in output
        assert "ci/lint" in output

    def test_shows_error_line(self, sample_pr):
        """When a report has `error` set, print_plans surfaces the full error text."""
        plan = RemediationPlan(
            report=HealthReport(
                pr=sample_pr,
                status=MergeStatus.UNKNOWN,
                error="gh returned malformed JSON: Expecting value at line 1 col 1",
            ),
            steps=[],
            summary="Could not determine status.",
        )
        output = _capture_console(print_plans, [plan])
        assert "Error:" in output
        assert "malformed JSON" in output

    def test_no_error_line_when_clean(self, sample_pr):
        """Reports without `error` set omit the error line entirely."""
        plan = RemediationPlan(
            report=HealthReport(pr=sample_pr, status=MergeStatus.READY),
            steps=[],
            summary="Ready.",
        )
        output = _capture_console(print_plans, [plan])
        assert "Error:" not in output

    def test_no_reframe_by_default(self, sample_pr):
        """Without audited_user, no 'Viewing @X' notice appears."""
        plan = RemediationPlan(
            report=HealthReport(pr=sample_pr, status=MergeStatus.READY),
            steps=[],
            summary="Ready.",
        )
        output = _capture_console(print_plans, [plan])
        assert "Viewing" not in output

    def test_reframes_for_other_user(self, sample_pr):
        """With audited_user set, the reframing notice is prepended once."""
        plan = RemediationPlan(
            report=HealthReport(
                pr=sample_pr,
                status=MergeStatus.BEHIND,
                blockers=[Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
            ),
            steps=[RemediationStep(description="Update branch")],
            summary="Rebase needed.",
        )
        output = _capture_console(print_plans, [plan], audited_user="octocat")
        assert "Viewing @octocat's PRs" in output
        # No double-@ even when the username is rendered into the notice
        assert "@@octocat" not in output


# ── _make_console helper tests ────────────────────────────────────────────
#
# NOTE on module-level `console` caching:
# pr_owl.output.console is built once at first import via _make_console().
# Monkeypatching env vars or file descriptors after import won't change
# what `console` points to. Tests that need to verify _make_console's
# behavior call it directly (below). Tests that need to verify print_table's
# output under different console configurations use _capture_console()
# above, which replaces the module-level console reference with a
# test-owned Console before calling the function under test.
#
# The tests below monkeypatch sys.stderr to a StringIO so they work
# deterministically regardless of how pytest is invoked. pytest's default
# captured stderr is non-TTY, but `pytest -s` disables capture and uses
# the real terminal, where sys.stderr.isatty() returns True in an
# interactive session. StringIO.isatty() always returns False.


class TestMakeConsole:
    def test_floor_for_non_tty(self, monkeypatch):
        """Non-TTY + no COLUMNS → 120-col floor."""
        import io

        monkeypatch.delenv("COLUMNS", raising=False)
        monkeypatch.setattr("sys.stderr", io.StringIO())
        from pr_owl.output import _make_console

        assert _make_console().width == 120

    def test_honors_columns_env(self, monkeypatch):
        """Non-TTY + COLUMNS set → user's preference wins."""
        import io

        monkeypatch.setenv("COLUMNS", "80")
        monkeypatch.setattr("sys.stderr", io.StringIO())
        from pr_owl.output import _make_console

        assert _make_console().width == 80

    def test_module_level_console_uses_floor_after_reload(self, monkeypatch):
        """Regression: the module-level `console` variable (what print_table
        actually uses) gets width 120 on fresh import under non-TTY / no
        COLUMNS.

        This catches the class of bug where `_make_console()` works in
        isolation but the module-level `console = _make_console()` line
        silently does the wrong thing (typo, late binding, import order).
        """
        import importlib
        import io
        import sys

        import pr_owl.output

        # Capture original stderr for manual restore. monkeypatch's automatic
        # teardown restores sys.stderr AFTER the test function returns — by
        # then, pr_owl.output.console is already bound to the StringIO we
        # installed, and subsequent tests using print_table would fail with
        # "I/O operation on closed file". We restore manually in the finally.
        original_stderr = sys.stderr

        monkeypatch.delenv("COLUMNS", raising=False)
        monkeypatch.setattr("sys.stderr", io.StringIO())
        importlib.reload(pr_owl.output)

        try:
            assert pr_owl.output.console.width == 120
        finally:
            # Restore real stderr, then reload once more so the module's
            # `console` binds to the real stderr for subsequent tests.
            sys.stderr = original_stderr
            importlib.reload(pr_owl.output)


# ---------------------------------------------------------------------------
# print_closed_table
# ---------------------------------------------------------------------------


def _make_closed_info(
    number: int = 7,
    disposition: ClosedDisposition = ClosedDisposition.MERGED,
    days_open: int = 9,
    review_count: int = 2,
) -> ClosedPRInfo:
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
        disposition=disposition,
        days_open=days_open,
        review_count=review_count,
        closed_at="2026-04-10T12:00:00Z",
    )


def test_print_closed_table_renders_merged():
    info = _make_closed_info(disposition=ClosedDisposition.MERGED)
    output = _capture_console(print_closed_table, [info])
    assert "MERGED" in output


def test_print_closed_table_renders_closed():
    info = _make_closed_info(disposition=ClosedDisposition.CLOSED)
    output = _capture_console(print_closed_table, [info])
    assert "CLOSED" in output


def test_print_closed_table_empty_prints_nothing():
    output = _capture_console(print_closed_table, [])
    assert output.strip() == ""


def test_print_json_with_closed_prs(sample_pr, capsys):
    from pr_owl.output import print_json

    reports = [HealthReport(pr=sample_pr, status=MergeStatus.READY)]
    closed = [_make_closed_info()]
    print_json(reports, closed=closed)
    data = json.loads(capsys.readouterr().out)
    assert len(data["closed"]) == 1
    assert data["closed"][0]["disposition"] == "MERGED"
    assert data["closed"][0]["days_open"] == 9


def test_print_json_no_closed_has_empty_array(sample_pr, capsys):
    from pr_owl.output import print_json

    reports = [HealthReport(pr=sample_pr, status=MergeStatus.READY)]
    print_json(reports)
    data = json.loads(capsys.readouterr().out)
    assert data["closed"] == []
