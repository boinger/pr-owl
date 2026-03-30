"""CLI entry point for pr-owl."""

from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import click

from pr_owl import __version__, gh
from pr_owl.checker import check_pr
from pr_owl.discovery import discover_prs, filter_stale
from pr_owl.exceptions import GhAuthError, GhNotFoundError, PrOwlError
from pr_owl.fixer import fix_pr
from pr_owl.models import FixResult, HealthReport, MergeStatus
from pr_owl.output import (
    console,
    print_fix_results,
    print_json,
    print_plans,
    print_summary,
    print_table,
)
from pr_owl.planner import plan_remediation


@click.group(invoke_without_command=True)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.version_option(version=__version__)
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Audit outbound GitHub PRs for mergeability."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(name)s: %(message)s",
    )
    if ctx.invoked_subcommand is None:
        ctx.invoke(audit)


@cli.command()
@click.option("--repo", "-R", default="", help="Scope to owner/repo.")
@click.option("--org", default="", help="Scope to organization.")
@click.option("--stale-days", type=int, default=None, help="Only show PRs inactive for N+ days.")
@click.option("--status", "status_filter", default="", help="Filter by MergeStatus value.")
@click.option("--json", "json_output", is_flag=True, help="JSON output to stdout.")
@click.option("--plan", "show_plan", is_flag=True, help="Show detailed remediation plans.")
@click.option("--fix", is_flag=True, help="Auto-fix BEHIND PRs (rebase).")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation for --fix.")
@click.option("--dry-run", is_flag=True, help="Show what --fix would do.")
@click.option("--workers", type=int, default=5, help="Concurrent health check workers (1=serial).")
def audit(
    repo: str,
    org: str,
    stale_days: int | None,
    status_filter: str,
    json_output: bool,
    show_plan: bool,
    fix: bool,
    yes: bool,
    dry_run: bool,
    workers: int,
) -> None:
    """Audit your open PRs for mergeability."""
    # Preflight
    try:
        gh.ensure_gh()
        gh.check_auth()
    except (GhNotFoundError, GhAuthError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    # Discover
    try:
        user = gh.get_current_user()
        prs = discover_prs(repo=repo, org=org)
    except PrOwlError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    if not prs:
        if not json_output:
            print_summary([], user)
            console.print("[dim]No open PRs found.[/dim]")
        else:
            print_json([])
        return

    # Filter
    if stale_days is not None:
        prs = filter_stale(prs, stale_days)
        if not prs:
            if not json_output:
                print_summary([], user)
                console.print(f"[dim]No PRs inactive for {stale_days}+ days.[/dim]")
            else:
                print_json([])
            return

    # Check health (concurrent)
    reports: list[HealthReport] = []
    effective_workers = max(1, min(workers, len(prs)))

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_to_pr = {executor.submit(check_pr, pr): pr for pr in prs}
        for future in as_completed(future_to_pr):
            pr = future_to_pr[future]
            try:
                report = future.result()
                reports.append(report)
            except Exception as exc:
                logging.getLogger(__name__).warning("Failed to check %s#%d: %s", pr.repo, pr.number, exc)
                reports.append(HealthReport(pr=pr, status=MergeStatus.UNKNOWN, error=str(exc)))

    # Filter by status
    if status_filter:
        try:
            target = MergeStatus(status_filter.upper())
        except ValueError:
            console.print(f"[red]Unknown status:[/red] {status_filter}")
            console.print(f"Valid values: {', '.join(s.value for s in MergeStatus)}")
            sys.exit(1)
        reports = [r for r in reports if r.status == target]

    # Plan
    plans = [plan_remediation(r) for r in reports]

    # Output
    if json_output:
        print_json(reports)
        return

    print_summary(reports, user)

    if show_plan:
        print_plans(plans)
    else:
        print_table(reports)

    # Fix
    if fix:
        fixable = [p for p in plans if any(b.type.value == "BEHIND_BASE" for b in p.report.blockers)]
        if not fixable:
            console.print("\n[dim]No BEHIND PRs to fix.[/dim]")
            return

        if not yes and not dry_run:
            console.print(f"\n[bold]Will update branch for {len(fixable)} BEHIND PR(s).[/bold]")
            if not click.confirm("Proceed?"):
                console.print("[dim]Aborted.[/dim]")
                return

        results: list[FixResult] = []
        for plan in fixable:
            result = fix_pr(plan, dry_run=dry_run)
            results.append(result)

        print_fix_results(results)
