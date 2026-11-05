#!/usr/bin/env bash
# hub-scan.sh — gathers the raw state of one repo so the hub never has to read transcripts.
# Run it from anywhere inside the repo (main checkout or any worktree).
# Usage: hub-scan.sh [--no-github]   (--no-github skips section 4, for frequent refreshes like the tower pane)
#
# Prints:
#   1. WORKTREES     — every worktree, its branch, and its card's status; then the WIP count
#   2. LIVE SESSIONS — Claude sessions open in each worktree right now (from agent view)
#   3. ORPHAN CARDS  — cards whose worktree no longer exists (finished or deleted work)
#   4. GITHUB        — your open PRs, and open issues (all of them on your own repo, assigned to you otherwise)
#   5. BACKLOG       — parked ideas waiting for a slot (only used when there's no GitHub)

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
wip_limit="$(tower_conf_get wip_limit 3)"
stale_minutes=$((24 * 60))                                 # a `running` card untouched this long is suspect

echo "REPO: $repo"
echo "LEDGER: $ledger"
echo

# ---------- 1. WORKTREES ----------
echo "== WORKTREES =="
seen_cards=""        # space-separated card names that matched a live worktree
session_args=()      # BRANCH=PATH pairs for sessions.py
wip_count=0          # worktrees whose card says running or needs-you
is_first=1           # git lists the main checkout first

# `git worktree list --porcelain` prints a stable, machine-readable block per worktree:
#   worktree /path/to/folder
#   HEAD <sha>
#   branch refs/heads/<name>      (or "detached")
#   <blank line>
# We read it line by line and print one summary row each time a block ends.
path=""; branch=""; sha=""
while IFS= read -r line; do
  case "$line" in
    "worktree "*) path="${line#worktree }" ;;
    "HEAD "*)     sha="${line#HEAD }" ;;
    "branch "*)   branch="${line#branch refs/heads/}" ;;
    "detached")   branch="" ;;                                            # the lib names it detached-<sha>
    "")                                                                   # blank line = end of one block
      if [ -n "$path" ]; then
        label="${branch:-(detached ${sha:0:7})}"
        session_args+=("$label=$path")
        card_name="$(ledger_card_name "$branch" "$sha")"                  # same rule card-path.sh uses
        card="$ledger/$card_name"
        if [ -f "$card" ]; then
          status="$(grep -m1 '^status:' "$card" | sed 's/^status:[[:space:]]*//')"
          ticket="$(grep -m1 '^ticket:' "$card" | sed 's/^ticket:[[:space:]]*//; s/^"\(.*\)"$/\1/')"
          updated="$(grep -m1 '^updated:' "$card" | sed 's/^updated:[[:space:]]*//')"
          stale=""
          if [ "$status" = "running" ] && [ -n "$(find "$card" -mmin +"$stale_minutes" 2>/dev/null)" ]; then
            stale=" | STALE: not updated in over 24h"
          fi
          case "$status" in running|needs-you) wip_count=$((wip_count + 1)) ;; esac
          echo "- $label | status: ${status:-?} | ticket: ${ticket:-none} | updated: ${updated:-?}$stale | $path"
          seen_cards="$seen_cards $card_name"
        elif [ "$is_first" -eq 1 ]; then
          echo "- $label | main checkout (no card expected) | $path"
        else
          echo "- $label | status: NO CARD | $path"                       # a worktree that never reported
        fi
        is_first=0
      fi
      path=""; branch=""; sha=""
      ;;
  esac
done < <(git worktree list --porcelain; echo)   # trailing echo guarantees a final blank line
if [ "$wip_count" -ge "$wip_limit" ]; then
  echo "WIP: $wip_count of $wip_limit (running + needs-you) — AT LIMIT"
else
  echo "WIP: $wip_count of $wip_limit (running + needs-you)"
fi
echo

# ---------- 2. LIVE SESSIONS ----------
echo "== LIVE SESSIONS (agent view) =="
if command -v python3 >/dev/null 2>&1; then
  python3 -I "$here/sessions.py" "${session_args[@]}"
else
  echo "(python3 not found; skipping live sessions)"
fi
echo

# ---------- 3. ORPHAN CARDS ----------
echo "== ORPHAN CARDS (worktree gone) =="
found_orphan=0
for card in "$ledger"/*.md; do
  [ -e "$card" ] || continue                       # glob matched nothing -> skip
  name="$(basename "$card")"
  [ "$name" = "_backlog.md" ] && continue          # the backlog isn't a card
  case " $seen_cards " in
    *" $name "*) ;;                                # matched a live worktree above -> not an orphan
    *)
      status="$(grep -m1 '^status:' "$card" | sed 's/^status:[[:space:]]*//')"
      echo "- ${name%.md} | status: ${status:-?} | $card"
      found_orphan=1
      ;;
  esac
done
[ "$found_orphan" -eq 0 ] && echo "(none)"
echo

# ---------- 4. GITHUB ----------
echo "== GITHUB =="
gh_err="$(mktemp)"; trap 'rm -f "$gh_err"' EXIT   # each gh call's stderr, so failures show their real reason

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

# ---------- 5. BACKLOG ----------
echo "== BACKLOG (parked ideas) =="
if [ -s "$ledger/_backlog.md" ]; then   # -s = file exists and isn't empty
  cat "$ledger/_backlog.md"
else
  echo "(empty)"
fi
