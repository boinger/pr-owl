# pr-owl TODOs

## Features

### Long-running agent for periodic PR monitoring
Background agent that checks PR status and acts on fix opportunities.
Smart timing based on repo activity (active repos checked frequently,
dormant repos weekly). Use `pushedAt` or commit frequency to calibrate.

### --notify flag
Post comments on stale PRs to ping reviewers. Needs careful UX to avoid spam.

### Draft-to-ready promotion
Report draft status but don't manage transitions. Could add later.

### Stuck-PR classifier / attention score
Promote age + activity into an action-driving heuristic instead of just
display columns. Both CEO-phase review voices converged on this as the real
10x value unlock. Candidate signals:
- `age > 30d AND updated < 7d AND actionable-blocker present` → "stuck"
- `age > 60d AND no author activity for 14d` → "likely abandoned"
- Unified "attention score" feeding sort order or a glyph in Status column
Needs dedicated design pass because thresholds are taste and sort-order
changes are a user-visible contract. Captured from /autoplan review of the
`Open` column plan.

### Color-by-age styling
Age cell red when >90d, yellow when >30d. Depends on the `Open` column
landing. Small but threshold selection is taste; separate design decision.

### Compact age format (47d / 2w / 1.2y)
Revisit if 3+ digit day counts become routine in real queues. Also migrate
the closed-PR `Days` column for parity. 120-col floor has ~11 chars of
headroom today; a compact format buys space back.

### age_days naming parity on ClosedPRInfo
Currently `ClosedPRInfo.days_open`; rename or add `age_days` method to
align with `PRInfo.age_days`. Simplifies future unified attention-score
code.

### CHANGELOG.md
Project has no CHANGELOG. Worth creating on its own PR to establish format
and backfill recent releases (v0.x breaking change to JSON shape is noted
in README but not in a proper changelog).

### Closed PR branch cleanup
New `cleanup` subcommand: for MERGED PRs in the closed window where
`head_repo` is the user's fork, delete the fork branch (GitHub button
equivalent), `git fetch --prune` the discovered clone, and `git branch -d`
the local branch (safe-delete only — squash-merged branches reported, not
forced). Default dry-run; `--apply` performs deletions. Closed-without-merge
PRs are report-only. Design rationale in
`~/.claude/plans/glittery-hatching-brook.md`.
