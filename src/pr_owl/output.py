"""Output formatting: Rich table, JSON, remediation plans."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

from rich.console import Console
from rich.table import Table

from pr_owl.models import (
    HealthReport,
    MergeStatus,
    RemediationPlan,
)

_STATUS_STYLE: dict[MergeStatus, str] = {
    MergeStatus.READY: "green",
    MergeStatus.BEHIND: "yellow",
    MergeStatus.CONFLICTS: "red",
    MergeStatus.BLOCKED: "red",
    MergeStatus.CI_FAILING: "red",
    MergeStatus.DRAFT: "dim",
    MergeStatus.UNKNOWN: "yellow",
}

console = Console(stderr=True)


def print_summary(reports: list[HealthReport], user: str) -> None:
    """Print a one-line summary header."""
    console.print(f"\n[bold]pr-owl[/bold] — @{user} — {len(reports)} open PR(s)\n")


def print_table(reports: list[HealthReport]) -> None:
    """Print a Rich table grouped by status."""
    if not reports:
        console.print("[dim]No PRs found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Status", width=12)
    table.add_column("PR", min_width=20)
    table.add_column("Title", min_width=30)
    table.add_column("Blockers", width=10, justify="center")
    table.add_column("Updated", width=12)

    # Sort by status priority: problems first
    status_order = {
        MergeStatus.CONFLICTS: 0,
        MergeStatus.CI_FAILING: 1,
        MergeStatus.BLOCKED: 2,
        MergeStatus.BEHIND: 3,
        MergeStatus.UNKNOWN: 4,
        MergeStatus.DRAFT: 5,
        MergeStatus.READY: 6,
    }
    sorted_reports = sorted(
        reports,
        key=lambda r: (status_order.get(r.status, 99), 0 if r.has_actionable_blockers else 1),
    )

    for report in sorted_reports:
        style = _STATUS_STYLE.get(report.status, "")
        if report.blockers:
            indicator = "⚡" if report.has_actionable_blockers else "👤"
            blocker_count = f"{indicator} {len(report.blockers)}"
        else:
            blocker_count = ""
        pr_ref = f"{report.pr.repo}#{report.pr.number}"
        updated = report.pr.updated_at[:10] if report.pr.updated_at else ""

        table.add_row(
            f"[{style}]{report.status.value}[/{style}]",
            pr_ref,
            report.pr.title[:50],
            blocker_count,
            updated,
        )

    console.print(table)
    console.print("[dim]⚡ = potentially fixable  👤 = waiting on others[/dim]")


def _report_to_dict(report: HealthReport) -> dict:
    """Serialize a HealthReport to a JSON-friendly dict."""
    d = asdict(report)
    d["status"] = report.status.value
    d["blockers"] = [
        {"type": b.type.value, "description": b.description, "details": b.details, "actionable": b.actionable}
        for b in report.blockers
    ]
    d["has_actionable_blockers"] = report.has_actionable_blockers
    d["checks"] = [asdict(c) for c in report.checks]
    return d


def print_json(reports: list[HealthReport]) -> None:
    """Print JSON array to stdout."""
    data = [_report_to_dict(r) for r in reports]
    sys.stdout.write(json.dumps(data, indent=2) + "\n")


def print_plans(plans: list[RemediationPlan]) -> None:
    """Print detailed remediation plans."""
    for plan in plans:
        pr = plan.report.pr
        console.print(f"\n[bold]{pr.repo}#{pr.number}[/bold]: {pr.title}")
        style = _STATUS_STYLE.get(plan.report.status, "")
        console.print(f"  Status: [{style}]{plan.report.status.value}[/{style}]")

        if plan.report.blockers:
            console.print(f"  Blockers ({len(plan.report.blockers)}):")
            for blocker in plan.report.blockers:
                console.print(f"    • {blocker.description}")
                for detail in blocker.details:
                    console.print(f"      - {detail}")

        if plan.steps:
            console.print("  Steps:")
            for i, step in enumerate(plan.steps, 1):
                auto = " [green](auto)[/green]" if step.automatable else ""
                console.print(f"    {i}. {step.description}{auto}")
                if step.command:
                    console.print(f"       [dim]$ {step.command}[/dim]")

        if plan.summary:
            console.print(f"  [bold]Summary:[/bold] {plan.summary}")
