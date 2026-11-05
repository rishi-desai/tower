#!/usr/bin/env bash
# adopt-scan.sh — facts for bringing an in-flight repo under tower. Read-only: it changes nothing
# except creating the project's empty Ledger folder.
#
# Run from anywhere inside the repo. Prints:
#   1. CONFIG        repo, ledger folder, default branch, WIP limit
#   2. WORKTREES     per worktree: card?, uncommitted files, ahead/behind the default branch,
#                    last commit, PR for the branch, ticket guessed from the branch name
#   3. LIVE SESSIONS which Claude sessions are open in which worktree (from agent view)
#   4. BRANCHES      local branches with unmerged commits but no worktree (parked work)

set -uo pipefail    # not -e: one failing probe (e.g. no GitHub) must not stop the scan

here="$(cd "$(dirname "$0")" && pwd)"
plugin="$(cd "$here/../../.." && pwd)"                   # <plugin>/skills/adopt/scripts -> <plugin>
# shellcheck source=../../../scripts/ledger-lib.sh
source "${TOWER_LIB:-$plugin/scripts/ledger-lib.sh}"

repo="$(ledger_repo_name)" || exit 1
project_dir="$(ledger_project_dir)" || exit 1
ledger="$project_dir/Ledger"
mkdir -p "$ledger"

# Default branch: what origin/HEAD points at, else main, else master.
default="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"
if [ -z "$default" ]; then
  if git show-ref --verify --quiet refs/heads/main; then default=main; else default=master; fi
fi
# Compare against origin's copy when it exists, so "ahead" means "not yet on the remote default".
base="$default"
git show-ref --verify --quiet "refs/remotes/origin/$default" && base="origin/$default"

has_gh=0
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1 && gh repo view >/dev/null 2>&1; then
  has_gh=1
fi

echo "== CONFIG =="
echo "repo: $repo | ledger: $ledger | default branch: $default (compared with $base) | WIP limit: $(tower_conf_get wip_limit 3) | GitHub: $([ $has_gh -eq 1 ] && echo yes || echo no)"
echo

echo "== WORKTREES =="
session_args=()
checked_out=" "
path=""; branch=""; sha=""; is_first=1
while IFS= read -r line; do
  case "$line" in
    "worktree "*) path="${line#worktree }" ;;
    "HEAD "*)     sha="${line#HEAD }" ;;
    "branch "*)   branch="${line#branch refs/heads/}" ;;
    "detached")   branch="" ;;
    "")
      if [ -n "$path" ]; then
        label="${branch:-(detached ${sha:0:7})}"
        session_args+=("$label=$path")
        checked_out="$checked_out$branch "
        role="worktree"; [ "$is_first" -eq 1 ] && role="MAIN CHECKOUT"
        card="$ledger/$(ledger_card_name "$branch" "$sha")"
        card_state="no card"; [ -f "$card" ] && card_state="card: $(grep -m1 '^status:' "$card" | sed 's/^status:[[:space:]]*//')"
        dirty="$(git -C "$path" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
        counts="$(git -C "$path" rev-list --left-right --count "$base...HEAD" 2>/dev/null)"   # "<behind> <ahead>"
        behind="${counts%%[[:space:]]*}"; ahead="${counts##*[[:space:]]}"
        last="$(git -C "$path" log -1 --format='%cr: %s' 2>/dev/null | cut -c1-70)"
        ticket="$(printf '%s' "$branch" | grep -oE '[0-9]+' | head -n1)"
        if [ -n "$ticket" ]; then ticket="#$ticket"; else ticket="none"; fi
        echo "- [$role] $label | $card_state | uncommitted: $dirty | ahead $ahead / behind $behind of $base | last: $last | ticket guess: $ticket | $path"
        if [ "$dirty" -gt 0 ]; then
          git -C "$path" status --porcelain 2>/dev/null | head -n 8 | sed 's/^/    /'
          [ "$dirty" -gt 8 ] && echo "    ... and $((dirty - 8)) more"
        fi
        if [ $has_gh -eq 1 ] && [ -n "$branch" ] && [ "$is_first" -eq 0 ]; then
          pr="$(gh pr list --head "$branch" --state all --limit 1 --json number,state,title \
                 --template '{{range .}}#{{.number}} {{.state}} {{.title}}{{end}}' 2>&1)" || pr="(gh failed: $pr)"
          echo "    PR: ${pr:-none}"
        fi
        is_first=0
      fi
      path=""; branch=""; sha=""
      ;;
  esac
done < <(git worktree list --porcelain; echo)
echo

echo "== LIVE SESSIONS (agent view) =="
if command -v python3 >/dev/null 2>&1; then
  python3 -I "$plugin/skills/hub/scripts/sessions.py" "${session_args[@]}"
else
  echo "(python3 not found; skipping live sessions)"
fi
echo

echo "== BRANCHES WITH UNMERGED WORK AND NO WORKTREE =="
found=0
while IFS= read -r b; do
  case "$checked_out" in *" $b "*) continue ;; esac
  [ "$b" = "$default" ] && continue
  ahead="$(git rev-list --count "$base..$b" 2>/dev/null || echo 0)"
  if [ "$ahead" -gt 0 ]; then
    # A squash-merged branch still has commits "not on main", so show its PR state when we can.
    pr=""
    if [ $has_gh -eq 1 ]; then
      pr="$(gh pr list --head "$b" --state all --limit 1 --json number,state \
             --template '{{range .}}#{{.number}} {{.state}}{{end}}' 2>&1)" || pr="gh failed: $pr"
      pr=" | PR: ${pr:-none}"
    fi
    echo "- $b | $ahead commit(s) not on $base$pr | last: $(git log -1 --format='%cr: %s' "$b" | cut -c1-70)"
    found=1
  fi
done < <(git for-each-ref --format='%(refname:short)' refs/heads/)
[ $found -eq 0 ] && echo "(none)"
