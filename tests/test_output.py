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


def _capture_console(func, *args, **kwargs):
    """Capture Rich console output to a string."""
    buf = StringIO()
    test_console = Console(file=buf, force_terminal=True, width=120)
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

    def test_blocker_count(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BEHIND,
            blockers=[
                Blocker(type=BlockerType.BEHIND_BASE, description="Behind"),
                Blocker(type=BlockerType.FAILING_CHECKS, description="Failing"),
            ],
        )
        output = _capture_console(print_table, [report])
        assert "2" in output

    def test_empty_results(self):
        output = _capture_console(print_table, [])
        assert "No PRs found" in output


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


class TestReportToDict:
    def test_serialization(self, sample_pr):
        report = HealthReport(
            pr=sample_pr,
            status=MergeStatus.BEHIND,
            blockers=[Blocker(type=BlockerType.BEHIND_BASE, description="Behind")],
        )
        d = _report_to_dict(report)
        assert d["status"] == "BEHIND"
        assert d["blockers"][0]["type"] == "BEHIND_BASE"


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
