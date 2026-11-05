#!/usr/bin/env bash
# project-rules.sh — links this project's personal rules file into the current worktree.
#
# Usage: project-rules.sh            (run from anywhere inside the worktree)
# Prints the rules file path. Editing that file goes through an approval prompt.
#
# Why: a repo may already have a team CLAUDE.md. Your personal rules must load next to it without
# ever being committed. Claude Code loads a git-ignored CLAUDE.local.md beside CLAUDE.md, but that
# file exists only in the worktree where it was created. So each worktree's CLAUDE.local.md holds
# one import line pointing at a single shared file in ~/.claude, which Claude Code protects:
#   <worktree>/CLAUDE.local.md   ->   @~/.claude/tower/rules/<Project>.md
# The rules stay on this machine (work rules never leave the work Mac).

set -euo pipefail

# The shared library sits at <plugin>/scripts/, three levels up from this script.
# shellcheck source=../../../scripts/ledger-lib.sh
source "${TOWER_LIB:-$(cd "$(dirname "$0")/../../../scripts" && pwd)/ledger-lib.sh}"

project="$(basename "$(ledger_project_dir)")"        # same project name the cards use
rules_dir="$HOME/.claude/tower/rules"                 # created by setup.sh
rules_file="$rules_dir/$project.md"
import_line="@~/.claude/tower/rules/$project.md"

case "$project" in
  *[[:space:]]*)
    echo "project-rules: project name '$project' has a space, which can break the @import." >&2
    echo "  Add 'project.$(ledger_repo_name)=<NameWithoutSpaces>' to $TOWER_CONF." >&2
    exit 1 ;;
esac
if [ ! -d "$rules_dir" ]; then
  echo "project-rules: $rules_dir is missing — run setup.sh on this machine" >&2
  exit 1
fi

toplevel="$(git rev-parse --show-toplevel)"
local_md="$toplevel/CLAUDE.local.md"

# Refuse to create a personal file that git would pick up. Checked BEFORE writing anything.
# (check-ignore works on paths that don't exist yet.) setup.sh adds CLAUDE.local.md to your
# global git ignore; this catches a machine where that didn't happen.
if ! git -C "$toplevel" check-ignore -q "$local_md"; then
  echo "project-rules: $local_md would NOT be git-ignored, so it could be committed. Nothing written." >&2
  echo "  Fix: echo CLAUDE.local.md >> \"\$(git config --global core.excludesFile || echo ~/.config/git/ignore)\"" >&2
  exit 1
fi

# Link it into this worktree. Never overwrite an existing CLAUDE.local.md; only append our line.
if ! grep -qxF "$import_line" "$local_md" 2>/dev/null; then
  printf '%s\n' "$import_line" >> "$local_md"
fi

# The rules file itself is created by the agent's Write tool (or by you), so the creation goes
# through the same approval prompt as every later edit. A missing file is just an empty rule set.
[ -f "$rules_file" ] || echo "project-rules: $rules_file doesn't exist yet; create it to add the first rule." >&2
echo "$rules_file"
