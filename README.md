# pr-owl

Audit your outbound GitHub PRs for mergeability and get actionable remediation plans.

pr-owl discovers all your open PRs across repos, checks their health (merge conflicts, failing CI, stale reviews), and tells you exactly what to do about each one.

## Install

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
pr-owl audit --plan

# Only show PRs inactive for 7+ days
pr-owl audit --stale-days 7

# Scope to a specific repo
pr-owl audit --repo owner/repo

# Auto-fix BEHIND PRs (rebase)
pr-owl audit --fix

# Dry run — see what --fix would do
pr-owl audit --fix --dry-run
```

## Requirements

- Python >=3.11
- [GitHub CLI](https://cli.github.com/) (`gh`) installed and authenticated
