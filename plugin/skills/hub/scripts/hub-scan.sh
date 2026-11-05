#!/usr/bin/env bash
# hub-scan.sh — gathers the raw state of one repo so the hub never has to read transcripts.
# Run it from anywhere inside the repo (main checkout or any worktree).
# Usage: hub-scan.sh [--no-github]   (--no-github skips section 5, for frequent refreshes like the tower pane)
#
# Prints:
#   1. WORKTREES     — every worktree, its branch, and its card's status; then the WIP count
#   2. LIVE SESSIONS — Claude sessions open in each worktree right now (from agent view)
#   3. ORPHAN CARDS  — cards whose worktree no longer exists (finished or deleted work)
#   4. GROUPS        — sections 1-3 sorted into the hub's report order (Needs you, Blocked, Running, Done,
#                      Housekeeping), with card-vs-session mismatches called out, so the hub never regroups
#   5. GITHUB        — your open PRs, and open issues (all of them on your own repo, assigned to you otherwise)
#   6. BACKLOG       — parked ideas waiting for a slot (only used when there's no GitHub)

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
pr_approval="$(tower_conf_get pr_approval every)"          # every | none; anything else is every
case "$pr_approval" in every|none) ;; *) pr_approval=every ;; esac
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
row_label=(); row_status=(); row_ticket=()   # one entry per worktree, for section 4; status is the card's,
                                             # or NO CARD / MAIN (the main checkout, which needs none)

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
          [ -n "$stale" ] && status="$status STALE"
          row_status+=("${status:-?}"); row_ticket+=("${ticket:-none}")
        elif [ "$is_first" -eq 1 ]; then
          echo "- $label | main checkout (no card expected) | $path"
          row_status+=("MAIN"); row_ticket+=("none")
        else
          echo "- $label | status: NO CARD | $path"                       # a worktree that never reported
          row_status+=("NO CARD"); row_ticket+=("none")
        fi
        row_label+=("$label")
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
echo "PR approval: $pr_approval"
echo

# ---------- 2. LIVE SESSIONS ----------
echo "== LIVE SESSIONS (agent view) =="
states="$(mktemp)"   # sessions.py's per-worktree waiting/interactive counts, read by section 4
gh_err="$(mktemp)"   # each gh call's stderr (section 5), so failures show their real reason
trap 'rm -f "$states" "$gh_err"' EXIT
if command -v python3 >/dev/null 2>&1; then
  python3 -I "$here/sessions.py" --states-file "$states" "${session_args[@]}"
else
  echo "(python3 not found; skipping live sessions)"
fi
echo

# ---------- 3. ORPHAN CARDS ----------
echo "== ORPHAN CARDS (worktree gone) =="
found_orphan=0
orphans_txt=""       # one line per orphan, repeated under Housekeeping in section 4
for card in "$ledger"/*.md; do
  [ -e "$card" ] || continue                       # glob matched nothing -> skip
  name="$(basename "$card")"
  [ "$name" = "_backlog.md" ] && continue          # the backlog isn't a card
  case " $seen_cards " in
    *" $name "*) ;;                                # matched a live worktree above -> not an orphan
    *)
      status="$(grep -m1 '^status:' "$card" | sed 's/^status:[[:space:]]*//')"
      echo "- ${name%.md} | status: ${status:-?} | $card"
      orphans_txt+="- ${name%.md} | orphan card (worktree gone), status: ${status:-?}"$'\n'
      found_orphan=1
      ;;
  esac
done
[ "$found_orphan" -eq 0 ] && echo "(none)"
echo

# ---------- 4. GROUPS ----------
# Sorting here, not in the hub's head, makes the report the same every time. A live session waiting on
# the user beats whatever its card says, because cards go stale and session status doesn't.
echo "== GROUPS (report in this order) =="
needs_txt=""; blocked_txt=""; running_txt=""; done_txt=""; house_txt="$orphans_txt"
i=0
while [ "$i" -lt "${#row_label[@]}" ]; do
  label="${row_label[$i]}"; status="${row_status[$i]}"; ticket="${row_ticket[$i]}"
  waiting=""; interactive=0
  IFS=$'\t' read -r _ interactive waiting < <(awk -F'\t' -v b="$label" '$1 == b' "$states") || true
  row="- $label | ticket: $ticket"
  if [ -n "$waiting" ]; then
    case "$status" in
      needs-you*) needs_txt+="$row | card: needs-you | session waiting: $waiting"$'\n' ;;
      MAIN)       needs_txt+="- $label (main checkout) | session waiting: $waiting"$'\n' ;;
      *)          needs_txt+="$row | session waiting: $waiting, but card says ${status} (card may be out of date)"$'\n' ;;
    esac
  else
    case "$status" in
      needs-you*) needs_txt+="$row | card: needs-you (no session waiting on this machine)"$'\n' ;;
      blocked*)   blocked_txt+="$row"$'\n' ;;
      running*)   running_txt+="$row"$'\n' ;;
      done*)      done_txt+="$row"$'\n' ;;
      MAIN)       ;;
      "NO CARD")  house_txt+="- $label | no card"$'\n' ;;
      *)          house_txt+="- $label | unknown card status: $status"$'\n' ;;
    esac
  fi
  case "$status" in *STALE) house_txt+="- $label | STALE: running card not updated in over 24h"$'\n' ;; esac
  if [ "${interactive:-0}" -gt 1 ]; then
    house_txt+="- $label | $interactive interactive sessions share this worktree (in the main checkout, one is the hub)"$'\n'
  fi
  i=$((i + 1))
done
[ -s "$states" ] || echo "(no live session data: grouped from cards alone)"
for group in "NEEDS YOU:$needs_txt" "BLOCKED:$blocked_txt" "RUNNING:$running_txt" "DONE:$done_txt" "HOUSEKEEPING:$house_txt"; do
  echo "${group%%:*}"
  body="${group#*:}"
  if [ -n "$body" ]; then printf '%s' "$body"; else echo "(none)"; fi
done
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
