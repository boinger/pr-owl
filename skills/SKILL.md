---
name: pr-owl
description: |
  Audit outbound GitHub PRs for mergeability and guide remediation.
  Use when: "check my PRs", "PR status", "stale PRs", "merge readiness",
  "pr-owl", "outbound PRs", "PR health", "what PRs need attention".
---

# pr-owl: PR Health Audit & Remediation

## Prerequisites

Before running, verify the tool is available:

```bash
which pr-owl && gh auth status 2>&1 | head -3
```

If pr-owl is not installed:
```bash
uv tool install --from git+https://github.com/boinger/pr-owl.git pr-owl
```

## Workflow

### 1. Audit

**IMPORTANT: Never dump raw JSON to the terminal.** The JSON output can be very large (40KB+). Always write to a temp file and read it with the Read tool:

```bash
pr-owl audit --json 2>/dev/null > /tmp/pr-owl-audit.json && echo "Audit complete: $(python3 -c "import json; d=json.load(open('/tmp/pr-owl-audit.json')); print(f'{len(d)} PRs')")"
```

Then read the file silently:
```
Read tool: /tmp/pr-owl-audit.json
```

Adapt the base command to the user's request:
- "just show stale PRs" → add `--stale-days 14`
- "check this repo" → add `--repo owner/repo`
- "fix all behind" → add `--fix --yes`

For human-readable output that the user SHOULD see in terminal, use the table or details view instead of JSON:
- Quick overview: `pr-owl audit` (table output, compact)
- Full details: `pr-owl audit --details` (blockers + remediation steps)

### 2. Interpret

From the JSON data (read via Read tool, NOT shown to user), present a clean summary:
- Count PRs by status: N ready, N behind, N conflicts, N failing CI, N draft, N blocked
- Highlight PRs that need immediate action (conflicts, CI failures)
- Note any PRs with compound blockers (e.g., BEHIND + CI_FAILING)
- Group by org/repo when there are multiple PRs in the same repo

### 3. Guided Remediation

For each non-ready PR, offer specific actions based on blocker type:

**BEHIND (automatable):**
- Offer to fix: "Want me to rebase this?"
- Execute: `pr-owl audit --fix --repo owner/repo --yes`
- If fix fails with conflicts, fall through to CONFLICTS handling

**CONFLICTS (manual):**
Guide the user through local resolution:
1. `cd` to the local clone (or clone if needed)
2. `git fetch upstream && git rebase upstream/<base-branch>`
3. Resolve conflicts (offer to help analyze conflict markers)
4. `git push --force-with-lease`

**CI_FAILING:**
- List failing check names and detail URLs from the JSON data
- Offer to fetch logs: `gh run view <run-id> --repo owner/repo --log-failed`
- Help investigate common failure patterns (lint, type errors, test failures)

**BLOCKED (reviews):**
- Identify what's needed: REVIEW_REQUIRED vs CHANGES_REQUESTED
- For CHANGES_REQUESTED: offer to fetch review comments
  `gh pr view <number> -R owner/repo --comments`
- For REVIEW_REQUIRED: suggest pinging reviewers

**DRAFT:**
- Note the draft status, don't push action unless asked
- If user wants to promote: `gh pr ready <number> -R owner/repo`

**Compound blockers:**
Address in dependency order:
1. Rebase first (BEHIND) — CI results may change after rebase
2. Wait for CI / investigate failures
3. Address review feedback
4. Mark ready (if draft)

### 4. Batch Operations

When the user wants to fix multiple PRs:
- `pr-owl audit --fix --dry-run` to preview
- `pr-owl audit --fix --yes` to execute all BEHIND fixes
- Report results: fixed, skipped, failed

## Notes

- pr-owl only auto-fixes BEHIND PRs (via `gh pr update-branch --rebase`). All other remediation is guided, not automated.
- The `--workers` flag controls concurrent health checks (default 5). Use `--workers 1` for debugging.
- JSON output goes to stdout; table/details output goes to stderr. Use `> /tmp/pr-owl-audit.json` to capture JSON cleanly.
