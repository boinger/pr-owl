"""Output formatting: Rich table, JSON, remediation plans."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

from pr_owl.models import (
    ClosedDisposition,
    ClosedPRInfo,
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
# Cap table width on ultra-wide terminals. Rationale:
# 1. Emoji (⚡, 💬) take 2 visual cells but Rich counts them as 1 — at full
#    terminal width, this overflows and wraps rows onto multiple lines.
# 2. Titles expanding to 90+ chars for ~35-char content is wasted space.
# 160 chars = 80% of a 200-col terminal; above that, leave margin.
_TABLE_WIDTH_CAP = 160


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


def _now_utc() -> datetime:
    """Current time, tz-aware UTC. Isolated for test injection — tests
    monkeypatch this symbol rather than patching ``datetime`` module-scope
    (which would collaterally affect ``sort_closed_prs`` and other callers)."""
    return datetime.now(tz=timezone.utc)


def print_summary(reports: list[HealthReport], user: str) -> None:
    """Print a one-line summary header."""
    console.print(f"\n[bold]pr-owl[/bold] — @{user} — {len(reports)} open PR(s)\n")


_STATUS_ORDER: dict[MergeStatus, int] = {
    MergeStatus.CONFLICTS: 0,
    MergeStatus.CI_FAILING: 1,
    MergeStatus.BLOCKED: 2,
    MergeStatus.BEHIND: 3,
    MergeStatus.UNKNOWN: 4,
    MergeStatus.DRAFT: 5,
    MergeStatus.READY: 6,
}


def _open_sort_key(report: HealthReport) -> tuple[int, int, float]:
    """Sort key for open PRs. Shared between report-level and plan-level sorts
    so all three surfaces (table, --json, --details) stay in lockstep.

    Relies on ``PRInfo.__post_init__`` having parsed ``updated_at`` into
    ``_updated_at_dt``; PRInfo raises at construction if that parse fails, so
    every instance reaching this key function has a usable timestamp.
    """
    return (
        _STATUS_ORDER.get(report.status, 99),
        0 if report.has_actionable_blockers else 1,
        -report.pr.updated_at_dt.timestamp(),
    )


def sort_open_reports(reports: list[HealthReport]) -> list[HealthReport]:
    """Order open PRs deterministically: status bucket, actionable first, then recency.

    The audit pipeline collects reports via ``as_completed``, so the input
    order is nondeterministic. Sorting here gives stable output across runs —
    most-recently-updated within each (status, actionable) bucket bubbles up,
    matching the tool's action-oriented framing.
    """
    return sorted(reports, key=_open_sort_key)


def sort_closed_prs(closed: list[ClosedPRInfo]) -> list[ClosedPRInfo]:
    """Order closed PRs by most-recently-closed first.

    Entries with missing or unparseable ``closed_at`` sink to the bottom in
    stable input order (Python's ``sorted`` guarantees stability). In practice
    ``closed_at`` is always present from ``gh search prs --state closed``, but
    the defensive branch prevents a malformed result from crashing the sort.
    """

    def key(c: ClosedPRInfo) -> tuple[int, float]:
        if not c.closed_at:
            return (1, 0.0)
        try:
            dt = datetime.fromisoformat(c.closed_at.replace("Z", "+00:00"))
        except ValueError:
            return (1, 0.0)
        return (0, -dt.timestamp())

    return sorted(closed, key=key)


def print_table(reports: list[HealthReport]) -> None:
    """Print a Rich table grouped by status."""
    if not reports:
        console.print("[dim]No PRs found.[/dim]")
        return

    # Fill terminal width up to _TABLE_WIDTH_CAP. Elastic columns (PR, Title
    # via ratio=) claim extra width; fixed-width columns stay put.
    table = Table(show_header=True, header_style="bold", width=min(console.width, _TABLE_WIDTH_CAP))
    # Column widths chosen to fit longest expected content exactly (no padding):
    # Status: "CI_FAILING" = 10 chars. Blockers: "⚡ 99" visual-width ≈ 5 chars,
    # header "Blockers" = 8 chars (header is the binding constraint). Updated:
    # YYYY-MM-DD = 10 chars. Open: 4-digit days = 4 chars, header "Open" = 4.
    table.add_column("Status", width=10)
    table.add_column("PR", min_width=20, ratio=1)
    # Title uses overflow="fold" so long titles (including the error snippet
    # appended when report.error is set) wrap across multiple lines instead of
    # being silently cropped with "…". Removing this will regress DX-1 — the
    # error visibility fix. See tests/test_output.py::test_long_error_title_folds_not_crops.
    # ratio=2 gives Title twice the extra-width share of PR — titles are longer.
    table.add_column("Title", min_width=30, overflow="fold", ratio=2)
    table.add_column("Blockers", width=8, justify="center")
    table.add_column("💬", width=5, justify="center")
    table.add_column("Updated", width=10)
    table.add_column("Open", width=4, justify="right")

    sorted_reports = sort_open_reports(reports)
    now = _now_utc()

    for report in sorted_reports:
        style = _STATUS_STYLE.get(report.status, "")
        if report.blockers:
            indicator = "⚡" if report.has_actionable_blockers else "👤"
            blocker_count = f"{indicator} {len(report.blockers)}"
        else:
            blocker_count = ""
        pr_ref = f"{report.pr.repo}#{report.pr.number}"
        updated = report.pr.updated_at[:10] if report.pr.updated_at else ""

        comment_total = report.issue_comment_count + report.review_event_count
        has_new = (report.new_issue_comments + report.new_review_events) > 0
        if comment_total > 0:
            comment_cell = f"{comment_total}*" if has_new else str(comment_total)
        else:
            comment_cell = ""

        title = report.pr.title[:50]
        if report.error:
            # Append a truncated error snippet so the user has a hint when a row
            # would otherwise show UNKNOWN with no explanation. Full error text
            # is in `--details` / `print_plans`.
            err_snippet = report.error[:60].replace("\n", " ")
            title = f"{title} [dim red]· {err_snippet}[/dim red]"

        age = report.pr.age_days(now)
        age_cell = "" if age is None else str(age)

        table.add_row(
            f"[{style}]{report.status.value}[/{style}]",
            pr_ref,
            title,
            blocker_count,
            comment_cell,
            updated,
            age_cell,
        )

    console.print(table)
    console.print(
        "[dim]⚡ = potentially fixable  👤 = waiting on others  💬 = comment count (* = new since last audit)[/dim]"
    )


_DISPOSITION_STYLE: dict[ClosedDisposition, str] = {
    ClosedDisposition.MERGED: "green",
    ClosedDisposition.CLOSED: "yellow",
}


def print_closed_table(closed: list[ClosedPRInfo]) -> None:
    """Print a Rich table of recently closed PRs."""
    if not closed:
        return

    console.print("\n[bold]Recently closed[/bold]\n")

    table = Table(show_header=True, header_style="bold", width=min(console.width, _TABLE_WIDTH_CAP))
    table.add_column("Disposition", width=12)
    table.add_column("PR", min_width=20, ratio=1)
    table.add_column("Title", min_width=30, overflow="fold", ratio=2)
    table.add_column("Days", width=6, justify="right")
    table.add_column("Reviews", width=8, justify="center")
    table.add_column("Closed", width=12)

    for info in sort_closed_prs(closed):
        style = _DISPOSITION_STYLE.get(info.disposition, "")
        pr_ref = f"{info.pr.repo}#{info.pr.number}"
        closed_date = info.closed_at[:10] if info.closed_at else ""

        table.add_row(
            f"[{style}]{info.disposition.value}[/{style}]",
            pr_ref,
            info.pr.title,
            str(info.days_open),
            str(info.review_count) if info.review_count > 0 else "",
            closed_date,
        )

    console.print(table)


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
    # PRInfo caches parsed datetimes in _updated_at_dt / _created_at_dt that
    # json.dumps can't serialize. Strip them — callers needing timestamps
    # should use updated_at / created_at directly.
    if isinstance(d.get("pr"), dict):
        d["pr"].pop("_updated_at_dt", None)
        d["pr"].pop("_created_at_dt", None)
    return d


def _closed_to_dict(info: ClosedPRInfo) -> dict:
    """Serialize a ClosedPRInfo to a JSON-friendly dict."""
    return {
        "pr": {
            "number": info.pr.number,
            "title": info.pr.title,
            "repo": info.pr.repo,
            "url": info.pr.url,
        },
        "disposition": info.disposition.value,
        "days_open": info.days_open,
        "review_count": info.review_count,
        "closed_at": info.closed_at,
    }


def print_json(reports: list[HealthReport], closed: list[ClosedPRInfo] | None = None) -> None:
    """Print JSON object to stdout with open and closed PR arrays.

    Arrays are emitted in the same order as the Rich tables so ``--json``
    consumers see deterministic output across runs.
    """
    data = {
        "open": [_report_to_dict(r) for r in sort_open_reports(reports)],
        "closed": [_closed_to_dict(c) for c in sort_closed_prs(closed or [])],
    }
    sys.stdout.write(json.dumps(data, indent=2) + "\n")


def print_plans(plans: list[RemediationPlan], audited_user: str | None = None) -> None:
    """Print detailed remediation plans.

    When ``audited_user`` is set, the viewer is looking at someone else's PR
    queue. Prepend a one-line notice making clear the steps below describe
    what *that* user would need to do, not the viewer.
    """
    if audited_user:
        console.print(
            f"\n[dim]Viewing @{audited_user}'s PRs — the steps below describe "
            f"what @{audited_user} would need to do to unblock each PR.[/dim]"
        )
    sorted_plans = sorted(plans, key=lambda p: _open_sort_key(p.report))
    for plan in sorted_plans:
        pr = plan.report.pr
        console.print(f"\n[bold]{pr.repo}#{pr.number}[/bold]: {pr.title}")
        style = _STATUS_STYLE.get(plan.report.status, "")
        console.print(f"  Status: [{style}]{plan.report.status.value}[/{style}]")

        new_issue = plan.report.new_issue_comments
        new_review = plan.report.new_review_events
        if new_issue or new_review:
            parts: list[str] = []
            if new_issue:
                parts.append(f"{new_issue} comment(s)")
            if new_review:
                parts.append(f"{new_review} review(s)")
            console.print(f"  [bold]💬 New activity:[/bold] {', '.join(parts)}")

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
