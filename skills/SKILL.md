---
name: pr-owl
description: |
  Audit outbound GitHub PRs for mergeability and guide remediation.
  Use when: "check my PRs", "PR status", "stale PRs", "merge readiness",
  "pr-owl", "outbound PRs", "PR health", "what PRs need attention",
  "fix my PRs", "rebase needed", "upstream updated", "get rebased",
  "merge upstream", "fix conflicts", "bring branch current",
  "sync with upstream", "get up to date".
---

# pr-owl: PR Health Audit & Remediation

## Prerequisites

```bash
which pr-owl && gh auth status 2>&1 | head -3
```

If not installed:
```bash
uv tool install --from git+https://github.com/boinger/pr-owl.git pr-owl
```

## Mode Detection

- **"fix"**, **"fix my PRs"**, **"rebase"**, **"upstream"**, **"conflicts"**, **"sync"**, **"up to date"**, **"sign"**, **"signing"**, **"signed commits"**, **"unsigned"** → **Fix mode**
- **"audit"**, **"check"**, **"status"** → **Audit mode**
- **"details"** → **Details mode**
- Ambiguous → **Audit mode**

The modes are mutually exclusive. Once you've picked a mode, follow ONLY that mode's invocation pattern. Never run a `--json` capture in Audit Mode, and never run a separate `pr-owl audit` table render in Fix Mode. Mixing patterns causes a state-update race that hides the `*` indicator for new activity (see `cli.py:395-397` save guard and `output.py` asterisk condition).

---

## Fix Mode

When the user wants to fix things, act. Don't audit-then-interpret-then-ask.

### Step 1: Get PR data

Write JSON to temp file (NEVER dump to terminal), then summarize to yourself:

```bash
pr-owl audit --json 2>/dev/null > /tmp/pr-owl-audit.json && echo "Audit saved"
```

Then use the Read tool to read `/tmp/pr-owl-audit.json`. The JSON is an object with `"open"` and `"closed"` arrays. Parse `data["open"]` to build the fix plan. The `"closed"` array lists PRs that closed **since the user's previous `pr-owl audit` run** — it is state-relative, not calendar-relative. Earlier runs consume the closed events, so an empty `"closed"` array does NOT mean nothing closed in recent calendar time; it means nothing has closed since the last audit. Informational, no fix action needed.

**Before processing the fix plan, check for new activity.** Each report in `data["open"]` has a `has_new_activity` boolean populated against the user's last `pr-owl audit` run. Any PR where `has_new_activity` is `true` has feedback (comments, reviews, force-pushes, label changes) the user has not yet seen since their previous audit. Also check `data["closed"]` for recently closed PRs and mention them. List those PRs to the user FIRST, with their URLs, before starting the fix workflow:

> Heads up: PR1 (URL), PR2 (URL) have new activity since your last audit. You may want to read those before I start fixing.

This is non-blocking — proceed with the fix workflow after surfacing it. The point is to make sure new feedback is visible BEFORE the user gets distracted by the rebase/conflict-resolution dance.

**Do NOT additionally run `pr-owl audit 2>&1` to render the table for the user at this point.** That second invocation loads the just-saved state file (the `--json` call above commits state via `save_state()` at `cli.py:397`) and computes zero comment deltas, hiding the `*` indicator for genuinely new activity. The Step 3 verify call (after fixes are applied) is the only non-JSON `pr-owl audit` invocation in Fix Mode.

**Render an open-PR summary as a markdown table from the JSON you just loaded.** Match the same columns and row order that the CLI's rich table uses — see `print_table` in `src/pr_owl/output.py` for the source of truth. The JSON's `open` array is already sorted by the CLI via `sort_open_reports`; preserve that order, do not re-sort. For the comment count column, render `X*` (with the asterisk) when `has_new_activity` is `true` AND `comment_count > 0`; render `*` alone when `has_new_activity` is `true` but `comment_count == 0` (activity like a review or force-push, no comment); otherwise render `X` for any non-zero `comment_count`, blank when zero. This is the assistant-rendered equivalent of the rich table the CLI would have produced.

If `data["closed"]` is non-empty, also render a brief closed-PR summary table using the same column-source-of-truth principle (see `print_closed_table` in `output.py`).

**Activity-flag phrasing rule (applies to all prose summaries):** `*` in the table means *new activity since last audit* — could be a comment, review, force-push, or label change. `X*` means *X total comments AND new activity*. The `*` is a flag, not a count, and it doesn't tell you what kind of activity. When summarizing in prose, never say "X new comments" — say "X comments, new activity since last audit" or "new activity" (without conflating count and flag). Look at the actual PR (via gh CLI or web) to see what specifically changed.

**Closed-table phrasing rule (applies to all prose summaries):** The "Recently closed" / `data["closed"]` table is *state-relative, not calendar-relative*. It shows PRs closed since the user's last `pr-owl audit` run, NOT closures in recent calendar time. When summarizing in prose, never say "no recently closed" or "no recent merges" — say "no closures since last audit" or "no new closures since last run." If the user asks about a closure that isn't in the table (e.g., "what about #1234 that merged this morning?"), the previous audit already consumed it; suggest `pr-owl audit --closed-since 1d` (or `7d`, `2w`, `1m`) to widen the window without re-mutating state when paired with `--peek`.

Then present a brief summary of what you found and what you're about to do.

### Step 2: For each non-ready PR, fix it

Process in this order: BEHIND first, then CONFLICTS/CI_FAILING, then SIGNING_REQUIRED (only on a requirement signal + explicit approval — see below), then report BLOCKED.

**BEHIND** (remote, no clone needed):

First, detect the branch's existing sync style. If a local clone is available:
```bash
git -C <clone> log --merges upstream/<base>..origin/<head_ref> --oneline | head -5
```
- If output contains `Merge branch '<base>' into ...` commits → the branch has been kept in sync via **merge**. Match that style — preserves all existing commit SHAs, no force-push, no CI re-approval reset:
  ```bash
  gh pr update-branch <number> -R <repo>
  ```
- If output is empty → the branch is **linear**. Rebase mode is appropriate (rewrites SHAs and triggers a force-push, but matches the existing style):
  ```bash
  gh pr update-branch <number> -R <repo> --rebase
  ```

If no local clone exists to inspect, default to **merge mode** (no `--rebase` flag). Merge is the safe default: zero SHA churn, no force-push event in the PR timeline, no CI re-approval cascade. Only opt into `--rebase` when style detection confirmed linear OR the user explicitly asked for linearization.

**CONFLICTS or CI_FAILING** (needs local clone):
1. Find the clone (see Clone Discovery below)
2. Run the Safety Checks (see Safety Protocol below)
3. Determine remotes (see Remote Identification below)
4. Rebase:
```bash
ORIG_BRANCH=$(git rev-parse --abbrev-ref HEAD)
git fetch <upstream_remote> <base_ref>
git fetch <push_remote> <head_ref>
git checkout <head_ref> || git checkout -b <head_ref> <push_remote>/<head_ref>
git rebase <upstream_remote>/<base_ref>
```
5. If conflicts: read the conflicting files, resolve the conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`), `git add` each resolved file, `git rebase --continue`. Repeat for each conflicting commit.
6. Push: `git push --force-with-lease <push_remote> <head_ref>`
7. Restore: `git checkout $ORIG_BRANCH`

**CI_FAILING with no conflicts** (branch is current but CI failed):
Check if failures are transient or real:
```bash
gh run view <run_id> -R <repo> --log-failed 2>&1 | tail -30
```
- If logs show `error_api`, `rate limit`, `timeout`, or infrastructure errors → re-run failed jobs:
  ```bash
  gh run rerun <run_id> -R <repo> --failed
  ```
- If logs show real test failures (assertion errors, lint errors, type errors) → report to user with the specific failures. Don't re-run blindly.

**SIGNING_REQUIRED** (commits unsigned AND a maintainer/branch-protection requires signing): rewrite the branch to add SSH/GPG signatures, then force-push. This is the **opposite** of BEHIND's safe merge-mode — it rewrites SHAs and force-pushes, so it is **never automatic**: run only on a requirement signal + explicit user approval.

> Note: commit *signature* (SSH/GPG) is a different thing from DCO *sign-off* (a `Signed-off-by` trailer, fixed by `rebase --signoff`, surfaced via give-back's `dco_required`). A red **DCO** check is NOT a signing signal — do not route it here.

**1. Detect — branch on `.reason`, NOT the `.verified` boolean.** The boolean collapses states that need different fixes. Per authored commit:
```bash
gh api repos/<head_repo>/commits/<sha> --jq '.commit.verification.reason'
```
| reason | meaning | remedy |
|--------|---------|--------|
| `valid` | signed + verified | none |
| `unsigned` | no signature on the object | **this routine** (rebase -S -f) |
| `unknown_key` / `not_signing_key` | signed, key not registered as a *signing* key | **setup, not a rewrite** — register the signing key (rewriting re-signs with the same key → still unverified). Hand off to give-back / the registration steps below. |
| `unverified_email` / `bad_email` / `no_user` | signed, committer email isn't a verified account identity | **setup, not a rewrite** — fix the email/identity |
| `gpgverify_error` / `gpgverify_unavailable` | transient verification-service blip | retry/ignore — do NOT surface as a signature problem |
| `invalid` / `expired_key` / `malformed_signature` / … | real signature problem | surface for human; don't auto-remediate |

Only `reason==unsigned` warrants the force-push rewrite. The signed-but-unverified reasons are the auth-key-vs-signing-key footgun class — a rewrite there pays the force-push/approval cost to reproduce the same unverified state.

**2. Trigger (on-demand only)** — a `reason==unsigned` commit is the necessary condition; act only with a corroborating *requirement* signal:
- **(a) maintainer asks** — a comment requesting signing (surfaced via `has_new_activity`; confirm with the user).
- **(b-ii) direct check signal** — `mergeStateStatus` BLOCKED with a failing/blocking required check whose context matches the **signature-specific** pattern `/gpg|signature|ssh.?sign|signed.?commit|commit.?sign|verify.?sign/i`. **Highest confidence.** Do NOT require checks-green here. Exclude `dco` and bare `sign`/`verif` (a red DCO check is a sign-off fix, not signing; bare tokens false-match `image-signing-scan`/`verify-build`).
- **(b-i) inferred protection signal** — `mergeStateStatus` BLOCKED + all checks green + reviews approved + no other apparent blocker (the invisible `required_signatures` mode mints no check). **Lowest confidence:** surface to the user as *"signing is the LIKELY blocker, unconfirmed — can't read the upstream's protection rules,"* never as fact (CODEOWNERS / unresolved threads / required-linear-history also yield BLOCKED). Bad path if wrong: force-push → approval dismissed → signing wasn't it → churn + still blocked.
- **Never auto-sweep** unsigned branches.

**3. Preconditions** (clone needed):
- Signing active in the clone: `git -C <clone> config --get commit.gpgsign` = `true`.
- Key registered as a *signing* key: `gh api user/ssh_signing_keys --jq '.[].title'` (needs only `read:ssh_signing_key`; works with the default token). If absent → it's a setup gap (see registration steps below), not a rewrite.
- ssh-agent has the key, matched by **fingerprint** (not comment grep):
  ```bash
  FP=$(ssh-keygen -lf <signing_key>.pub | awk '{print $2}'); ssh-add -l | grep -q "$FP" || echo "run: ssh-add <signing_key>"
  ```
- **Pre-batch liveness test-sign** (fail fast before rewriting N branches): gate clean first, then sign a throwaway detached commit and assert a `gpgsig` header:
  ```bash
  git -C <clone> status --porcelain   # must be empty, else stash/skip
  git -C <clone> checkout --detach
  git -C <clone> commit --allow-empty -S -m "signing pipeline test"
  git -C <clone> cat-file commit HEAD | grep -q '^gpgsig' && echo "signing OK" || echo "NOT SIGNED — fix agent/config"
  git -C <clone> checkout -           # orphan the test commit
  ```

**4. Per branch** (every command `git -C <clone>`):
```bash
git -C <clone> status --porcelain                       # SAFETY: empty, else stash/skip
ORIG=$(git -C <clone> rev-parse --abbrev-ref HEAD)      # restore at end
git -C <clone> fetch <upstream_remote> <base>
git -C <clone> fetch <push_remote> <head>
git -C <clone> checkout -B <head> <push_remote>/<head>  # pin rebase input to fetched tip
git -C <clone> rebase -S -f <upstream_remote>/<base>   # -f (--force-rebase) is REQUIRED: a plain `rebase -S` NO-OPS on a branch already current with base ("up to date") and leaves commits UNSIGNED; -f forces replay+sign. Harmless on behind branches (they replay anyway). ORIG_HEAD/range-diff/--force-if-includes all still behave.
#   conflicts: trivial only → add + --continue; non-trivial → rebase --abort, checkout "$ORIG", surface
# human checkpoint — authored commits must show '=' (only dropped sync-merges differ):
git -C <clone> range-diff "$(git -C <clone> merge-base ORIG_HEAD <upstream_remote>/<base>)..ORIG_HEAD" "<upstream_remote>/<base>..HEAD"
git -C <clone> push --force-with-lease --force-if-includes <push_remote> <head>
git -C <clone> checkout "$ORIG"
```
- `--force-with-lease --force-if-includes` together is the safe rebase-then-force idiom — bare `--force-with-lease` is a no-op right after a `fetch` (the lease compares against the just-moved tracking ref).

**5. Verify on the FORK** (not upstream — base repo can 404 until ref propagation):
```bash
for sha in $(git -C <clone> rev-list <upstream_remote>/<base>..HEAD); do
  gh api repos/<head_repo>/commits/$sha --jq '.commit.verification.reason'   # expect: valid
done
```
- Do NOT verify locally with `%G?` / `git verify-commit` — both need `gpg.ssh.allowedSignersFile` and false-fail on valid sigs. The `gpgsig`-header check (step 3) is the local liveness proof; trust verification is the fork API.

**6. Approval handling:** the rewrite changes SHAs → may dismiss an approval. After push, check `gh pr view <n> -R <repo> --json reviewDecision`. If it **held**, just comment that you rewrote to sign (no content change). If **dismissed**, comment + re-request. Don't reflexively re-request — many repos don't dismiss on push.

**Registration steps** (when a key isn't registered as a *signing* key — the `unknown_key`/`not_signing_key` remedy, a setup fix, NOT a rewrite):
```bash
gh ssh-key add <key>.pub --type signing --title "<name> (signing)"   # needs admin:ssh_signing_key
#   if it 404s on scope: GITHUB_TOKEN= gh auth refresh -h github.com -s admin:ssh_signing_key  (then retry; clear GITHUB_TOKEN if an env token shadows the OAuth one)
#   or register via web UI: https://github.com/settings/ssh/new  (Key type = Signing Key)
```

**Side effect:** a signing rebase also clears BEHIND (it rebases onto the base). **Activity note:** the force-push shows as a bare `*` (activity, no comment) on the next audit — expected self-inflicted activity, not maintainer feedback.

### Step 3: Verify

After fixing, re-run the audit to confirm:
```bash
pr-owl audit 2>&1
```
Report what changed: which PRs moved from CONFLICTS/CI_FAILING to READY or BEHIND.

### Clone Discovery

Search these paths in order for `owner/repo` (e.g., `grafana/loki`):
1. `~/Projects/<owner>/<repo>` (owner-nested, e.g., give-back workspaces)
2. `~/Projects/<repo>` (flat layout)
3. Ask the user: "Where is your local clone of `owner/repo`?"

For each candidate directory that exists, verify with `git remote -v` that ANY remote URL contains `owner/repo`. This handles forks correctly (clone has `origin → your-fork`, `upstream → base-repo`).

Never `find /` or guess blindly. If no clone is found and the user doesn't know, skip that PR and report it.

### give-back Workspace Context

After finding a clone, check for give-back contribution context:

```bash
[ -f <clone_path>/.give-back/context.json ] && echo "GIVE_BACK_WORKSPACE" || echo "NO_CONTEXT"
```

If GIVE_BACK_WORKSPACE, read `<clone_path>/.give-back/context.json`. The
relevant fields are:
- `upstream_owner` — base repo owner (e.g., "pallets")
- `repo` — repo name (e.g., "flask")
- `issue_number` — upstream issue this PR addresses
- `branch_name` — the contribution branch
- `dco_required` — whether commits need Signed-off-by
- `test_command` — how to run tests after rebase

Also read `<clone_path>/.give-back/brief.md` for commit format conventions.

Use this context to:
- **Enrich reporting**: "PR #57 (for pallets/flask#1234)" instead of just "PR #57"
- **Respect DCO**: if `dco_required` is true, ensure rebase preserves Signed-off-by
- **Respect signing**: if `brief.md` notes the repo requires signed commits (give-back records this as "may require") and the PR has `reason==unsigned` commits, that's corroborating context for the SIGNING_REQUIRED path above — still confirm with a real requirement signal (maintainer ask or blocked-merge) before any rewrite.
- **Run post-rebase tests**: use `test_command` from context if available
- **Follow conventions**: use commit format from the brief during conflict resolution

If context.json is missing, malformed, or lacks expected fields, proceed
normally. This is optional enrichment, never a gate.

### Remote Identification

A fork clone typically has two remotes:
- `origin` → your fork (e.g., `boinger/loki`) — this is where you push
- `upstream` → the base repo (e.g., `grafana/loki`) — this is what you rebase against

To determine which is which:
```bash
git remote -v
```
- The remote whose URL contains the PR's `repo` field (the base repo, e.g., `grafana/loki`) is the **upstream remote** (rebase target)
- The remote whose URL contains the PR's `head_repo` field (your fork, e.g., `boinger/loki`) is the **push remote**

If both point to the same repo (same-repo PR, not a fork), use the same remote for both.

### Safety Protocol

Before touching any clone:
- `git status --porcelain` — if any output, stash or skip (don't lose work)
- Check for `.git/REBASE_HEAD`, `.git/MERGE_HEAD`, `.git/CHERRY_PICK_HEAD`, `.git/BISECT_LOG` — if any exist, an operation is in progress. Either abort it (`git rebase --abort`) or skip.
- Record current branch: `ORIG_BRANCH=$(git rev-parse --abbrev-ref HEAD)` — always restore when done, even on failure
- If rebase fails mid-way: `git rebase --abort` then `git checkout $ORIG_BRANCH` — never leave a repo mid-rebase
- Push with `--force-with-lease` only (never `--force`)
- Verify clone identity: `git remote -v` must show a URL matching the PR's repo before operating
- For fork PRs: push to the fork remote, not upstream. The `head_repo` field in the JSON identifies which remote to push to.

---

## Audit Mode

Show the table directly:
```bash
pr-owl audit 2>&1
```

This is the ONLY pr-owl invocation in Audit Mode. Do NOT capture JSON; do NOT make a follow-up call. The single `pr-owl audit 2>&1` shows the rich table with the `*` indicator correctly because state hasn't been mutated yet when the table renders.

Adapt: `--stale-days 14`, `--repo owner/repo`, `--status CONFLICTS`

After showing the table, briefly summarize what needs attention and offer to fix.

**Activity-flag phrasing rule:** in the rendered table, `*` means *new activity since last audit* (comments, reviews, force-pushes, or label changes — the column shows comment count but the `*` flag covers any activity). `X*` means *X comments AND new activity since last audit*. When summarizing in prose, never say "X new comments" — say "X comments, new activity since last audit" or just "new activity". Look at the actual PR to see what changed. Same rule applies to Fix Mode summaries.

**Closed-table phrasing rule:** the "Recently closed" table is *state-relative, not calendar-relative*. It shows PRs closed since the user's previous `pr-owl audit` run, NOT closures in recent calendar time. When summarizing in prose, never say "no recently closed" or "no recent merges" — say "no closures since last audit" or "no new closures since last run." If the table is absent and the user asks about a closure they expected to see (e.g., a PR that merged earlier today), the previous audit already consumed it; suggest `pr-owl audit --closed-since 1d` (or `7d`, `2w`, `1m`) to widen the window. Pair with `--peek` to avoid re-mutating state.

---

## Details Mode

```bash
pr-owl audit --details 2>&1
```

---

## Notes

- The CLI is diagnostic only. All remediation runs as direct git/gh commands from this skill.
- `--workers` controls concurrent health checks (default 5, 1=serial for debugging).
- JSON goes to stdout, table/details go to stderr.
- `headRepository.nameWithOwner` is always empty from `gh pr view`. The JSON uses `headRepositoryOwner.login` + `headRepository.name` to construct `head_repo`.
- **Activity tracking fields**: Each report includes `comment_count` (current total comments, sourced from GitHub's GraphQL `totalCommentsCount` — the canonical number used in /pulls and notifications) plus `has_new_activity` (boolean: True iff the PR's `updated_at` is strictly after the relevant cutoff — per-PR `last_seen_at` for repeat sightings, global `last_audit_at` for first sightings). The cutoff is recorded in `~/.local/state/pr-owl/seen.json` and auto-marked as seen on each `pr-owl audit` run that does not pass `--peek` or `--no-state`. Use `has_new_activity` to surface unread activity before starting any fix workflow (see Fix Mode Step 1). The flag covers any PR-level activity (comments, reviews, force-pushes, labels) — it's NOT just comments. The `new_comments` field is deprecated (always 0) and retained one release for backward compat. State schema is v3.
- **Recently closed PRs**: The audit automatically shows a "Recently closed" table with PRs that closed since the last audit. The label is *state-relative, not calendar-relative* — "recently" means "since the user's previous audit run," not "in the past N days." If the user asks about a closure that's not in the table, the previous audit already consumed it; suggest `--closed-since 1d` (or `7d`, `2w`, `1m`) to widen. Each entry has `disposition` (MERGED/CLOSED), `days_open`, `review_count`, and `closed_at`. The `--json` output is now `{"open": [...], "closed": [...]}` (breaking change from the old bare array).
- **--closed-since**: Override the default time window for closed PRs. Accepts `7d`, `2w`, `1m` (30 days), or ISO date. Use `--no-closed` to suppress the closed table entirely.
- **--peek**: read-only audit. Loads state, computes deltas, shows them, but does NOT update state. Use when you only want to glance at activity without marking it seen.
- **--no-state**: skip state I/O entirely. Use for dry runs or when you don't want to touch the state file.
