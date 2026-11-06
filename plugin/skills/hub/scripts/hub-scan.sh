#!/usr/bin/env bash
# hub-scan.sh — gathers the raw state of one repo so the hub never has to read transcripts.
# Run it from anywhere inside the repo (main checkout or any worktree).
# Usage: hub-scan.sh [--no-github]   (--no-github skips section 5, for frequent refreshes like the tower pane)
#
# Prints:
#   1. WORKTREES     — every worktree with its effective state (tower state), the WIP count, unshipped work
#   2. LIVE SESSIONS — Claude sessions open in each worktree right now (from agent view)
#   3. ORPHAN CARDS  — cards whose worktree no longer exists (finished or deleted work)
#   4. GROUPS        — every worktree sorted into the hub's report order (Needs you, Blocked, Running, Waiting on
#                      CI or review, Done, Housekeeping), each with the reason for its state, so the hub never regroups
#   5. GITHUB        — your open PRs, and open issues (all of them on your own repo, assigned to you otherwise)
#   6. BACKLOG       — parked ideas waiting for a slot (only used when there's no GitHub)
#
# Sections 1, 3 and 4 come from one run of `tower state` (tower's core, src/tower/state.py), the one place a
# worktree's status is decided, so the hub, the pane (which parses section 1) and the WIP count always agree.

set -uo pipefail    # unset variables and pipe failures are errors; NOT -e, so one failing
                    # section (e.g. no GitHub remote) doesn't kill the rest of the report

here="$(cd "$(dirname "$0")" && pwd)"
skip_github=0; [ "${1:-}" = "--no-github" ] && skip_github=1
# The shared library sits at <plugin>/scripts/, three levels up from <plugin>/skills/hub/scripts/.
# shellcheck source=../../../scripts/ledger-lib.sh
source "${TOWER_LIB:-$here/../../../scripts/ledger-lib.sh}"

repo="$(ledger_repo_name)" || exit 1
project_dir="$(ledger_project_dir)" || exit 1              # prints why if config is missing
ledger="$project_dir/Ledger"
mkdir -p "$ledger"
pr_approval="$(tower_conf_get pr_approval every)"          # every | none; anything else is every
case "$pr_approval" in every|none) ;; *) pr_approval=every ;; esac

echo "REPO: $repo"
echo "LEDGER: $ledger"
echo

state_out="$(mktemp)"   # tower state's report: sections 1, 3 and 4, printed in their places below
gh_err="$(mktemp)"      # each failing command's stderr, so failures show their real reason
trap 'rm -f "$state_out" "$gh_err"' EXIT
state_failed=""         # why tower state didn't run; sections 1, 3 and 4 say so instead of guessing
if ! command -v python3 >/dev/null 2>&1; then
  state_failed="python3 not found"
elif ! python3 -I "$here/../../../scripts/run-tower.py" state >"$state_out" 2>"$gh_err"; then
  state_failed="tower state failed: $(tail -n1 "$gh_err")"
fi

# state_section NAME — print one "== NAME ..." section of tower state's report, header included, up to its blank line.
state_section() {
  if [ -n "$state_failed" ]; then
    echo "== $1 =="
    echo "($state_failed)"
  else
    awk -v name="== $1" 'index($0, name) == 1 { p = 1 } p && $0 == "" { exit } p { print }' "$state_out"
  fi
}

# ---------- 1. WORKTREES ----------
state_section WORKTREES
echo "PR approval: $pr_approval"
echo

# ---------- 2. LIVE SESSIONS ----------
echo "== LIVE SESSIONS (agent view) =="
# One BRANCH=PATH pair per worktree for sessions.py, labelled the way tower state labels them (a detached HEAD is
# "(detached <short sha>)"), so the pane can match sessions to section 1's rows. `git worktree list --porcelain`
# prints one block per worktree: "worktree <path>", "HEAD <sha>", "branch refs/heads/<name>" or "detached", blank line.
session_args=()
path=""; branch=""; sha=""
while IFS= read -r line; do
  case "$line" in
    "worktree "*) path="${line#worktree }" ;;
    "HEAD "*)     sha="${line#HEAD }" ;;
    "branch "*)   branch="${line#branch refs/heads/}" ;;
    "")
      [ -n "$path" ] && session_args+=("${branch:-(detached ${sha:0:7})}=$path")
      path=""; branch=""; sha=""
      ;;
  esac
done < <(git worktree list --porcelain; echo)   # trailing echo guarantees a final blank line
if command -v python3 >/dev/null 2>&1; then
  python3 -I "$here/sessions.py" "${session_args[@]}"
else
  echo "(python3 not found; skipping live sessions)"
fi
echo

# ---------- 3. ORPHAN CARDS ----------
state_section "ORPHAN CARDS"
echo

# ---------- 4. GROUPS ----------
state_section GROUPS
echo

# ---------- 5. GITHUB ----------
echo "== GITHUB =="

# gh_section TITLE ARGS... — run one gh command; on failure print its actual error, never a guess.
gh_section() {
  local title="$1"; shift
  local out
  echo "-- $title --"
  if out="$(gh "$@" 2>"$gh_err")"; then
    if [ -n "$out" ]; then printf '%s\n' "$out"; else echo "(none)"; fi
  else
    echo "(gh failed: $(tail -n1 "$gh_err"))"
  fi
}

if [ "$skip_github" -eq 1 ]; then
  echo "(skipped: --no-github)"
elif ! command -v gh >/dev/null 2>&1; then
  echo "(gh not installed — skipping)"
elif ! gh auth status >/dev/null 2>&1; then
  echo "(gh not logged in — run: gh auth login)"
elif ! gh_repo="$(gh repo view --json nameWithOwner,owner --jq '.nameWithOwner + " " + .owner.login' 2>"$gh_err")"; then
  echo "(no GitHub repo for this checkout: $(tail -n1 "$gh_err"))"
else
  repo_slug="${gh_repo% *}"; repo_owner="${gh_repo#* }"
  me="$(gh api user --jq .login 2>"$gh_err")" || me=""
  echo "repo: $repo_slug | you: ${me:-unknown ($(tail -n1 "$gh_err"))}"
  gh_section "my open PRs" pr list --author "@me" --state open \
     --json number,title,headRefName,reviewDecision,isDraft \
     --template '{{range .}}- #{{.number}} {{.title}} [{{.headRefName}}] review:{{if .reviewDecision}}{{.reviewDecision}}{{else}}none{{end}}{{if .isDraft}} DRAFT{{end}}{{"\n"}}{{end}}'
  # On your own repo, tickets from /to-tickets are usually unassigned, so list all open issues.
  # On someone else's repo (work), list only what's assigned to you.
  issue_template='{{range .}}- #{{.number}} {{.title}}{{range .labels}} [{{.name}}]{{end}}{{"\n"}}{{end}}'
  if [ -n "$me" ] && [ "$repo_owner" = "$me" ]; then
    gh_section "open issues (all — your repo)" issue list --state open --limit 30 \
       --json number,title,labels --template "$issue_template"
  else
    gh_section "open issues assigned to you" issue list --assignee "@me" --state open --limit 30 \
       --json number,title,labels --template "$issue_template"
  fi
fi
echo

# ---------- 6. BACKLOG ----------
echo "== BACKLOG (parked ideas) =="
if [ -s "$ledger/_backlog.md" ]; then   # -s = file exists and isn't empty
  cat "$ledger/_backlog.md"
else
  echo "(empty)"
fi
