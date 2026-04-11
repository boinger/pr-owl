"""CLI entry point for pr-owl."""

from __future__ import annotations

import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import click

from pr_owl import __version__, gh
from pr_owl.checker import check_pr
from pr_owl.discovery import discover_prs, filter_stale
from pr_owl.exceptions import GhAuthError, GhNotFoundError, PrOwlError
from pr_owl.models import HealthReport, MergeStatus
from pr_owl.output import (
    console,
    print_json,
    print_plans,
    print_summary,
    print_table,
)
from pr_owl.planner import plan_remediation

logger = logging.getLogger(__name__)

_UNKNOWN_RETRY_DELAY = 2.0  # seconds to wait before retrying UNKNOWN mergeable states


def _normalize_author(ctx: click.Context, param: click.Parameter, value: str) -> str:
    # Treat `--author octocat` and `--author @octocat` as equivalent so users
    # who instinctively type the @-prefix don't end up with `@@octocat` in
    # output or a broken `gh search prs --author @octocat` call. The literal
    # `@me` sentinel is preserved — gh interprets it as a keyword.
    if value and value != "@me" and value.startswith("@"):
        return value.lstrip("@")
    return value


def _retry_unknown_reports(
    reports: list[HealthReport],
    *,
    workers: int,
    audit_start: float,
    json_output: bool,
) -> None:
    """Retry PRs whose mergeable state came back UNKNOWN.

    GitHub sometimes returns ``mergeable=UNKNOWN`` while still computing
    mergeability immediately after a push. A short retry usually resolves
    them. Mutates ``reports`` in place — successful retries replace the
    original entry; unresolved entries are left as-is.
    """
    unknown_indices = [i for i, r in enumerate(reports) if r.mergeable == "UNKNOWN"]
    if not unknown_indices:
        return

    elapsed = time.monotonic() - audit_start
    delay = max(0.0, _UNKNOWN_RETRY_DELAY - elapsed)
    logger.info("Retrying %d UNKNOWN PR(s) after %.1fs delay...", len(unknown_indices), delay)
    if delay > 0:
        # Skip the decorative notice in JSON mode so we never risk
        # contaminating machine-readable output under stream-mixing
        # test harnesses or pipes.
        if not json_output:
            console.print(
                f"[dim]Retrying {len(unknown_indices)} PR(s) in UNKNOWN mergeable state after {delay:.1f}s...[/dim]"
            )
        time.sleep(delay)

    resolved = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_idx = {executor.submit(check_pr, reports[i].pr): i for i in unknown_indices}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                new_report = future.result()
            except (PrOwlError, KeyError, json.JSONDecodeError, AttributeError, TypeError):
                continue  # keep original UNKNOWN report
            if new_report.mergeable != "UNKNOWN":
                reports[idx] = new_report
                resolved += 1

    still_unknown = len(unknown_indices) - resolved
    if resolved:
        logger.info("Resolved %d PR(s); %d still UNKNOWN.", resolved, still_unknown)
    elif still_unknown:
        logger.info("All %d PR(s) still UNKNOWN after retry.", still_unknown)


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
@click.option(
    "--author",
    "-A",
    "author",
    default="@me",
    callback=_normalize_author,
    help="GitHub username to audit. Defaults to the authenticated user.",
)
@click.option("--stale-days", type=int, default=None, help="Only show PRs inactive for N+ days.")
@click.option("--status", "status_filter", default="", help="Filter by MergeStatus value.")
@click.option("--json", "json_output", is_flag=True, help="JSON output to stdout.")
@click.option("--details", "show_plan", is_flag=True, help="Show detailed blockers and remediation steps.")
@click.option("--workers", type=int, default=5, help="Concurrent health check workers (1=serial).")
def audit(
    repo: str,
    org: str,
    author: str,
    stale_days: int | None,
    status_filter: str,
    json_output: bool,
    show_plan: bool,
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
        viewing_other = author != "@me"
        user = author if viewing_other else gh.get_current_user()
        prs = discover_prs(author=author, repo=repo, org=org)
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
    has_unknowns = False
    audit_start = time.monotonic()

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_to_pr = {executor.submit(check_pr, pr): pr for pr in prs}
        for future in as_completed(future_to_pr):
            pr = future_to_pr[future]
            try:
                report = future.result()
                reports.append(report)
                if report.mergeable == "UNKNOWN":
                    has_unknowns = True
            except (PrOwlError, KeyError, json.JSONDecodeError, AttributeError, TypeError) as exc:
                logger.warning("Failed to check %s#%d: %s", pr.repo, pr.number, exc)
                reports.append(HealthReport(pr=pr, status=MergeStatus.UNKNOWN, error=str(exc)))

    # Retry PRs where GitHub returned UNKNOWN mergeable state
    if has_unknowns:
        _retry_unknown_reports(
            reports,
            workers=effective_workers,
            audit_start=audit_start,
            json_output=json_output,
        )

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
        print_plans(plans, audited_user=user if viewing_other else None)
    else:
        print_table(reports)
