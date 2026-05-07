"""CLI entry point for pr-owl."""

from __future__ import annotations

import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import click

from pr_owl import __version__, gh
from pr_owl.checker import check_pr
from pr_owl.discovery import discover_closed_prs, discover_prs, filter_stale
from pr_owl.exceptions import GhAuthError, GhNotFoundError, PrOwlError, StateError
from pr_owl.models import ClosedPRInfo, HealthReport, MergeStatus
from pr_owl.output import (
    console,
    print_closed_table,
    print_json,
    print_plans,
    print_summary,
    print_table,
)
from pr_owl.planner import plan_remediation
from pr_owl.state import compute_delta, get_last_audit_at, load_state, save_state, state_path

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


_DURATION_UNITS = {"d": 1, "w": 7, "m": 30}
_DEFAULT_CLOSED_DAYS = 7  # fallback for --author other-user or first run


def _parse_duration(ctx: click.Context, param: click.Parameter, value: str | None) -> datetime | None:
    """Parse a relative duration (7d/2w/1m) or ISO date into a UTC datetime.

    Returns None when the flag is omitted. Raises click.BadParameter on
    invalid input. Used as a Click callback for --closed-since.
    """
    if not value:
        return None

    # Try relative duration: Nd, Nw, Nm
    if len(value) >= 2 and value[-1] in _DURATION_UNITS:
        try:
            n = int(value[:-1])
        except ValueError:
            raise click.BadParameter(f"Invalid duration '{value}'. Use Nd, Nw, Nm (e.g. 7d, 2w, 1m) or ISO date.")
        if n <= 0:
            raise click.BadParameter(f"Duration must be positive, got '{value}'.")
        days = n * _DURATION_UNITS[value[-1]]
        return datetime.now(tz=timezone.utc) - timedelta(days=days)

    # Try ISO date
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise click.BadParameter(f"Invalid duration or date '{value}'. Use Nd, Nw, Nm (e.g. 7d, 2w, 1m) or ISO date.")


def _enrich_closed_prs(
    closed: list[ClosedPRInfo],
    workers: int,
) -> None:
    """Enrich closed PRs with review count from gh pr view. Mutates in place.

    Failures are non-fatal: if a view call fails, the PR keeps its default
    review_count=0 from the search result.
    """
    if not closed:
        return

    effective_workers = max(1, min(workers, len(closed)))

    def _enrich_one(info: ClosedPRInfo) -> None:
        try:
            data = gh.view_pr(info.pr.number, info.pr.repo)
            info.review_count = len(data.get("reviews") or [])
        except (PrOwlError, KeyError, json.JSONDecodeError):
            pass  # keep review_count=0

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {executor.submit(_enrich_one, info): info for info in closed}
        for future in as_completed(futures):
            try:
                future.result()
            except (PrOwlError, KeyError, json.JSONDecodeError, AttributeError, TypeError, ValueError):
                pass  # non-fatal; PR keeps review_count=0


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


def _annotate_comment_deltas(reports: list[HealthReport], state: dict) -> None:
    """Mutate each report with new_comments vs `state`.

    Must run after _retry_unknown_reports so retry-resolved counts are
    annotated against the loaded baseline (not zeros from the failed
    initial check). Must run before --status filtering so deltas display
    correctly for the filtered subset.
    """
    for report in reports:
        report.new_comments = compute_delta(report, state)


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
@click.option(
    "--status",
    "status_filter",
    default="",
    help="Filter by MergeStatus value. Skips state save (deltas for filtered PRs would be lost).",
)
@click.option("--json", "json_output", is_flag=True, help="JSON output to stdout.")
@click.option("--details", "show_plan", is_flag=True, help="Show detailed blockers and remediation steps.")
@click.option("--workers", type=int, default=5, help="Concurrent health check workers (1=serial).")
@click.option(
    "--no-state",
    is_flag=True,
    help="Skip loading and saving the comment-tracking state file. Use for dry runs or test isolation.",
)
@click.option(
    "--peek",
    is_flag=True,
    help="Show comment deltas without marking them as seen. State is loaded but not saved. "
    "Useful when you want to glance at activity but might get distracted before reading it.",
)
@click.option(
    "--closed-since",
    "closed_since",
    default=None,
    callback=_parse_duration,
    expose_value=True,
    help="Show PRs closed within this window. Accepts: 7d, 2w, 1m (30 days), or ISO date.",
)
@click.option(
    "--no-closed",
    is_flag=True,
    help="Suppress the recently-closed PR table.",
)
def audit(
    repo: str,
    org: str,
    author: str,
    stale_days: int | None,
    status_filter: str,
    json_output: bool,
    show_plan: bool,
    workers: int,
    no_state: bool,
    peek: bool,
    closed_since: datetime | None,
    no_closed: bool,
) -> None:
    """Audit your open PRs for mergeability."""
    # Preflight
    try:
        gh.ensure_gh()
        gh.check_auth()
    except (GhNotFoundError, GhAuthError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    viewing_other = author != "@me"

    # Discover user
    try:
        user = author if viewing_other else gh.get_current_user()
    except PrOwlError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    # Load state. Split: baseline-read (for last_audit_at + comment deltas)
    # vs comment-save (conditional). --no-state skips both. --author other
    # skips both (state is per-authenticated-user). --status and --peek only
    # affect the save path.
    state_skip_reason = ""
    save_skip_reason = ""
    if no_state:
        state_skip_reason = "--no-state"
        save_skip_reason = "--no-state"
    elif viewing_other:
        state_skip_reason = "--author other-user"
        save_skip_reason = "--author other-user"

    if not save_skip_reason and status_filter:
        save_skip_reason = "--status filter"

    state: dict = {}
    # True only when we actually loaded state and it had no PR entries.
    # Stays False when state_skip_reason prevents the load (--no-state,
    # --author other). The first-run hints gate on both this AND
    # not state_skip_reason, so the False default is safe.
    state_was_empty = False
    if not state_skip_reason:
        try:
            state = load_state()
            state_was_empty = not (state.get("prs") or {})
        except StateError as exc:
            console.print(f"[yellow]Warning:[/yellow] {exc}")
            state = {}
            state_was_empty = True

    # Discover open PRs
    try:
        prs = discover_prs(author=author, repo=repo, org=org)
    except PrOwlError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    # Filter stale
    if stale_days is not None and prs:
        prs = filter_stale(prs, stale_days)

    # Determine closed-PR window. --no-closed suppresses entirely.
    # Priority: --no-closed > --closed-since > last_audit_at > 7d fallback.
    closed: list[ClosedPRInfo] = []
    closed_cutoff: datetime | None = None
    if not no_closed:
        if closed_since:
            closed_cutoff = closed_since
        else:
            last_audit = get_last_audit_at(state)
            if last_audit:
                closed_cutoff = last_audit
            elif viewing_other or not state_skip_reason:
                # No last_audit_at: first run, v1→v2 upgrade (state has prs
                # but no timestamp), or --author other-user (no state loaded).
                # Default to 7d so the closed table isn't empty on first use.
                closed_cutoff = datetime.now(tz=timezone.utc) - timedelta(days=_DEFAULT_CLOSED_DAYS)

    # Discover closed PRs
    if closed_cutoff and not no_closed:
        try:
            closed = discover_closed_prs(
                author=author,
                since=closed_cutoff,
                repo=repo,
                org=org,
            )
        except PrOwlError as exc:
            logger.warning("Could not discover closed PRs: %s", exc)

    # Check health (concurrent) — only when there are open PRs
    reports: list[HealthReport] = []
    has_unknowns = False
    audit_start = time.monotonic()

    if prs:
        effective_workers = max(1, min(workers, len(prs)))

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

    # Annotate comment deltas (safe even when reports is empty)
    _annotate_comment_deltas(reports, state)

    # Enrich closed PRs with review count from gh pr view
    if closed:
        _enrich_closed_prs(closed, workers)

        # Dedup: remove closed PRs that also appear in the open set (reopened PRs)
        open_urls = {r.pr.url for r in reports}
        closed = [c for c in closed if c.pr.url not in open_urls]

    # Filter by status (open PRs only)
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

    # Persist updated state. Skipped under any of:
    # - --no-state (explicit opt-out)
    # - --peek (load happened, save deliberately skipped)
    # - --author other (state is per-authenticated-user)
    # - --status filter (deltas for filtered-out PRs would be lost)
    if not save_skip_reason and not peek:
        try:
            save_state(state, reports)
        except StateError as exc:
            console.print(f"[yellow]Warning:[/yellow] could not save state: {exc}")

    # Output
    if json_output:
        print_json(reports, closed=closed)
        return

    print_summary(reports, user)

    if reports:
        if show_plan:
            print_plans(plans, audited_user=user if viewing_other else None)
        else:
            print_table(reports)
    elif not closed:
        console.print("[dim]No open PRs found.[/dim]")

    # Closed table (always shown when there are results, regardless of open PR count)
    if closed:
        print_closed_table(closed)

    # First-run hints
    if not state_skip_reason and not peek and not json_output:
        if state_was_empty:
            console.print("[dim]💬 Comment tracking enabled — new activity will appear on subsequent runs.[/dim]")
        if not get_last_audit_at(state) and not closed_since and not no_closed:
            console.print("[dim]📋 Closed PR tracking enabled — resolved PRs will appear on subsequent runs.[/dim]")


@cli.group()
def state() -> None:
    """Inspect or manage the comment-tracking state file."""


@state.command("path")
def state_path_cmd() -> None:
    """Print the resolved state file path and exit."""
    click.echo(str(state_path()))
