"""Tests for pr_owl.output."""

from __future__ import annotations

import json
from io import StringIO

from rich.console import Console

from pr_owl.models import (
    Blocker,
    BlockerType,
    CICheck,
    HealthReport,
    MergeStatus,
    RemediationPlan,
    RemediationStep,
)
from pr_owl.output import (
    _report_to_dict,
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
        of the rightmost edge entirely. 120 is the new floor, and all five
        column headers must be visible.
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
        assert len(data) == 1
        assert data[0]["status"] == "READY"

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
        assert data[0]["checks"][0]["name"] == "ci/build"
        assert data[0]["blockers"][0]["type"] == "FAILING_CHECKS"
        assert data[0]["blockers"][0]["actionable"] is True
        assert data[0]["has_actionable_blockers"] is True

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
        assert data[0]["blockers"][0]["actionable"] is False
        assert data[0]["has_actionable_blockers"] is False


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
