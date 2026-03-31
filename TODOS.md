# pr-owl TODOs

## UX

### Table output truncated in narrow terminals
Rich table renders wider than the available terminal width, clipping columns.
Fix: set a max width or collapse long titles more aggressively.

## Features

### PR lifecycle tracking
Track PRs across runs. When a PR disappears from results, look it up:
merged? closed? Why? Persist prior run results and diff against current.

### Long-running agent for periodic PR monitoring
Background agent that checks PR status and acts on fix opportunities.
Smart timing based on repo activity (active repos checked frequently,
dormant repos weekly). Use `pushedAt` or commit frequency to calibrate.

### give-back workspace registry integration
Use give-back's workspace tracking to find local clones. Would improve
the skill's clone discovery beyond the `~/Projects/<repo-name>` heuristic.

### Fixability scores in audit output
Show users how many blockers are automatable vs require human judgment.

### --notify flag
Post comments on stale PRs to ping reviewers. Needs careful UX to avoid spam.

### Draft-to-ready promotion
Report draft status but don't manage transitions. Could add later.
