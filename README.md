# pr-owl

Audit your outbound GitHub PRs for mergeability and get actionable remediation plans.

You maintain PRs across multiple repos. Some are behind their base branch, some have merge conflicts, some have failing CI, some are waiting on reviews. pr-owl finds all of them, classifies their health, and tells you exactly what's wrong and how to fix each one.

## Example

```
$ pr-owl audit

pr-owl — @you — 6 open PR(s)

┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━┳━━━━━━━━━━━━┓
┃ Status     ┃ PR                  ┃ Title                        ┃ Blockers ┃  💬 ┃ Updated    ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━╇━━━━━━━━━━━━┩
│ CONFLICTS  │ acme/api#218        │ Add rate limiting middleware │   ⚡ 2   │ 8*  │ 2026-03-15 │
│ CI_FAILING │ widgets/core#74     │ Fix timezone handling        │   ⚡ 1   │  3  │ 2026-03-28 │
│ BEHIND     │ acme/api#220        │ Update OpenAPI spec for v3   │   ⚡ 1   │ 2*  │ 2026-03-29 │
│ BLOCKED    │ bigcorp/svc#1042    │ Add gRPC health check        │   👤 1   │  1  │ 2026-03-20 │
│ DRAFT      │ widgets/core#80     │ Refactor event pipeline      │   ⚡ 1   │     │ 2026-03-30 │
│ READY      │ acme/docs#55        │ Fix typo in quickstart       │          │     │ 2026-03-30 │
└────────────┴─────────────────────┴──────────────────────────────┴──────────┴─────┴────────────┘
⚡ = potentially fixable  👤 = waiting on others  💬 = comment count (* = new since last audit)
```

The `💬` column shows the total comment count (issue comments + review events)
for each PR. A `*` suffix marks PRs with new activity since your last audit.
The first time you run `pr-owl audit` it establishes the baseline; subsequent
runs flag any changes. See the [Comment tracking](#comment-tracking)
section for details on how the state file works.

With `--details`, you get remediation steps for each PR:

```
$ pr-owl audit --details

acme/api#218: Add rate limiting middleware
  Status: CONFLICTS
  Blockers (2):
    • Branch has merge conflicts
    • Review required
  Steps:
    1. Resolve merge conflicts locally
       $ git fetch <REMOTE> && git rebase <REMOTE>/main
    2. After resolving conflicts, force-push
       $ git push --force-with-lease
    3. Request review from required reviewers
    4. (replace <REMOTE> with whichever remote tracks acme/api — see `git remote -v`)
  Summary: acme/api#218: 2 blocker(s) — Branch has merge conflicts, Review required.
```

## Install

From a clone:

```bash
git clone https://github.com/boinger/pr-owl.git
cd pr-owl
./setup
```

This installs the `pr-owl` CLI via `uv tool` and registers the Claude Code skill (`/pr-owl`).

Or install just the CLI without cloning:

```bash
uv tool install --from git+https://github.com/boinger/pr-owl.git pr-owl
```

## Usage

```bash
# Audit all your open PRs
pr-owl audit

# JSON output for scripting
pr-owl audit --json

# Detailed remediation plans
pr-owl audit --details

# Only show PRs inactive for 7+ days
pr-owl audit --stale-days 7

# Scope to a specific repo
pr-owl audit --repo owner/repo

# Scope to an organization
pr-owl audit --org myorg

# Audit another user's PR queue (read-only — for teammates and managers)
pr-owl audit --author octocat
pr-owl audit -A octocat --details   # see what they'd need to do to unblock

# Filter by status
pr-owl audit --status CONFLICTS

# Control concurrency (default 5, 1=serial for debugging)
pr-owl audit --workers 1

# Debug logging
pr-owl audit --verbose

# See new-comment deltas without marking them as seen
pr-owl audit --peek

# Skip the comment-tracking state file entirely (dry run)
pr-owl audit --no-state

# Print the state file location
pr-owl state path
```

## Comment tracking

`pr-owl audit` remembers comment counts between runs in a small JSON file at
`$XDG_STATE_HOME/pr-owl/seen.json` (defaults to `~/.local/state/pr-owl/seen.json`
on Linux/macOS). On each run, the `New` column flags PRs that have gained
issue comments or review activity since your previous audit. The deltas are
auto-marked as seen on the next normal `pr-owl audit` run.

If you want to glance at activity without marking it seen, use `--peek` —
it loads the state and shows deltas but does not save. Useful when you might
get distracted before reading the actual comments.

`--no-state` skips state I/O entirely. `--status` and `--author other-user`
also skip state save automatically (filtered runs would lose deltas for hidden
PRs, and auditing someone else's queue would pollute your own state). The
state file is per-authenticated-user; auditing someone else is read-only.

To find the file: `pr-owl state path`. To reset tracking: delete the file.

## Status Classification

pr-owl maps GitHub's `mergeStateStatus` to a simplified set of statuses:

| GitHub mergeStateStatus | pr-owl Status |
|-------------------------|---------------|
| CLEAN                   | READY         |
| BEHIND                  | BEHIND        |
| DIRTY                   | CONFLICTS     |
| BLOCKED                 | BLOCKED       |
| UNSTABLE                | CI_FAILING    |
| HAS_HOOKS               | READY         |
| isDraft=true            | DRAFT         |
| anything else           | UNKNOWN       |

Each PR can have multiple blockers (e.g., BEHIND + CI_FAILING). The primary status is used for display; the full blocker list drives remediation.

## Claude Code Skill

pr-owl includes a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill for guided remediation. Where the CLI is diagnostic only (it tells you what's wrong), the skill acts on it: rebasing branches, resolving conflicts, triaging CI failures, and pushing fixes.

Install the skill by running `./setup` from a clone, then use `/pr-owl` in Claude Code:

- `/pr-owl audit` — run the audit and summarize results
- `/pr-owl fix` — audit and fix everything it can

The skill uses direct `git` and `gh` commands, not the pr-owl CLI, for remediation. It handles clone discovery, remote identification for forks, conflict resolution, and CI failure triage.

## Requirements

- Python >=3.11
- [GitHub CLI](https://cli.github.com/) (`gh`) installed and authenticated

## Development

```bash
make dev          # install with dev deps
make test         # unit tests (excludes integration)
make lint         # ruff check
make format       # ruff format
make pre-commit   # format + lint + test
```

## License

[MIT](LICENSE)
