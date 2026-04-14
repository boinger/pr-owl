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
