#!/usr/bin/env bash
# card-path.sh — prints the file path where THIS worktree's status card lives.
# Works in any git repo, any worktree, personal or work.
#
# Usage: card-path.sh [WORKTREE_DIR]     (default: the current directory)
#
# Cards live in your notes vault, outside every repo: <projects_dir>/<Project>/Ledger/<branch>.md
# (projects_dir comes from ~/.claude/tower.conf, written by setup.sh). That means:
#   - every worktree of the same repo writes to the same folder, so the hub can see them all
#   - cards never get committed or show up in a PR, even on a shared work repo
#   - you can read them in Obsidian

set -euo pipefail   # stop on any error (-e), on unset variables (-u), and on failures inside pipes (-o pipefail)

# The shared library sits at <plugin>/scripts/, three levels up from <plugin>/skills/status-card/scripts/.
# shellcheck source=../../../scripts/ledger-lib.sh
source "${TOWER_LIB:-$(cd "$(dirname "$0")/../../../scripts" && pwd)/ledger-lib.sh}"

dir="${1:-.}"
project_dir="$(ledger_project_dir "$dir")"           # fails with a clear message if not configured
branch="$(git -C "$dir" branch --show-current)"      # empty on a detached HEAD; the lib handles that
card_name="$(ledger_card_name "$branch" HEAD "$dir")"

mkdir -p "$project_dir/Ledger"                       # -p = no error if it's already there
echo "$project_dir/Ledger/$card_name"
