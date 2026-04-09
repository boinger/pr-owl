"""Output formatting: Rich table, JSON, remediation plans."""

from __future__ import annotations

import json
import os
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

_NON_TTY_WIDTH_FLOOR = 120


def _make_console() -> Console:
    """Build the pr-owl stderr console with a width floor for non-TTY use.

    Rich already honors the POSIX ``COLUMNS`` environment variable natively.
    The only thing we need to add is a floor for the case where stderr isn't
    a TTY AND the user hasn't set COLUMNS. In that case Rich falls back to
    80 cols, which crops pr-owl's Blockers and Updated columns.

    Logic:
    - Real TTY: pass through, Rich auto-detects width.
    - Non-TTY + COLUMNS set: pass through, user's explicit preference wins.
    - Non-TTY + no COLUMNS: floor to 120 so the table renders fully.

    Override: users who need a specific width (narrow terminal, CI that
    mis-reports TTY, piped output) set ``COLUMNS=N pr-owl audit``. That's
    the POSIX convention, no pr-owl-specific env var needed.

    We check ``sys.stderr.isatty()`` directly rather than Rich's
    ``Console.is_terminal``. Rich's check honors FORCE_COLOR (color hint
    for pipes) and TTY_COMPATIBLE (env override for tools that fake TTY).
    Neither tells us anything about *width* — a user piping to ``less -R``
    still wants the table to fit. The file-descriptor level ``isatty()``
    answers the right question.
    """
    try:
        is_tty = sys.stderr.isatty()
    except (AttributeError, ValueError):
        # AttributeError: mock/fake stderr objects lack isatty.
        # ValueError: closed file at teardown ("I/O operation on closed file").
        is_tty = False

    if not is_tty and "COLUMNS" not in os.environ:
        return Console(stderr=True, width=_NON_TTY_WIDTH_FLOOR)
    return Console(stderr=True)


console = _make_console()


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
    # Title uses overflow="fold" so long titles (including the error snippet
    # appended when report.error is set) wrap across multiple lines instead of
    # being silently cropped with "…". Removing this will regress DX-1 — the
    # error visibility fix. See tests/test_output.py::test_long_error_title_folds_not_crops.
    table.add_column("Title", min_width=30, overflow="fold")
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

        title = report.pr.title[:50]
        if report.error:
            # Append a truncated error snippet so the user has a hint when a row
            # would otherwise show UNKNOWN with no explanation. Full error text
            # is in `--details` / `print_plans`.
            err_snippet = report.error[:60].replace("\n", " ")
            title = f"{title} [dim red]· {err_snippet}[/dim red]"

        table.add_row(
            f"[{style}]{report.status.value}[/{style}]",
            pr_ref,
            title,
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
    # PRInfo caches a parsed datetime in _updated_at_dt that json.dumps can't
    # serialize. Strip it — callers needing the timestamp should use updated_at.
    if isinstance(d.get("pr"), dict):
        d["pr"].pop("_updated_at_dt", None)
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

        if plan.report.error:
            console.print(f"  [red]Error:[/red] {plan.report.error}")

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
