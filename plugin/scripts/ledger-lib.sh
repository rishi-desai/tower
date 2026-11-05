#!/usr/bin/env bash
# ledger-lib.sh — shared helpers for the status-card and hub scripts. Source it; don't run it.
#
# One place for the rules both sides must agree on:
#   - where the per-machine config lives and how to read it
#   - what a repo is called, and which project folder in the notes vault it maps to
#   - how a branch name becomes a card file name
# If card-path.sh and hub-scan.sh each had their own copy of these, they could drift apart
# (that is exactly how detached-HEAD cards used to go missing).

# Per-machine settings written by install.sh. Overridable so tests can point at a fake one.
TOWER_CONF="${TOWER_CONF:-$HOME/.claude/tower.conf}"

# tower_conf_get KEY [DEFAULT]
# Prints the value of KEY from the config file, or DEFAULT if the key is absent.
# The file is plain `key=value` lines; we parse it instead of `source`-ing it, so a typo in the
# config can never run as a shell command. Values may contain spaces (iCloud paths do).
tower_conf_get() {
  local key="$1" default="${2-}" line value=""
  if [ -f "$TOWER_CONF" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in
        "$key="*) value="${line#"$key="}" ;;     # last match wins, like most config formats
      esac
    done < "$TOWER_CONF"
  fi
  printf '%s\n' "${value:-$default}"
}

# ledger_repo_name [DIR]
# Prints the repo's name, the same from the main checkout and from every worktree.
# --git-common-dir points at the repo's shared git folder even from inside a worktree:
#   normal repo: /code/poot/.git   -> name is the parent folder, "poot"
#   bare repo:   /code/poot.git    -> no checked-out files of its own; name is "poot"
ledger_repo_name() {
  local dir="${1:-.}" common
  common="$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir)" || return 1
  if [ "$(basename "$common")" = ".git" ]; then
    basename "$(dirname "$common")"
  else
    basename "$common" .git
  fi
}

# ledger_project_dir [DIR]
# Prints this repo's project folder in the vault: <projects_dir>/<project name>.
# Project name is `project.<repo>=` from the config if set, else the repo name.
# Fails loudly (instead of guessing a fallback) when the config or projects folder is missing,
# so a card is never written somewhere the hub won't look.
ledger_project_dir() {
  local repo projects_dir project
  repo="$(ledger_repo_name "${1:-.}")" || return 1
  projects_dir="$(tower_conf_get projects_dir)"
  if [ -z "$projects_dir" ]; then
    echo "ledger: no projects_dir in $TOWER_CONF — run install.sh on this machine" >&2
    return 1
  fi
  if [ ! -d "$projects_dir" ]; then
    # Don't create it: a missing folder usually means a typo or an unsynced/unmounted vault.
    echo "ledger: projects_dir does not exist: $projects_dir (check $TOWER_CONF)" >&2
    return 1
  fi
  project="$(tower_conf_get "project.$repo" "$repo")"
  printf '%s\n' "$projects_dir/$project"
}

# ledger_card_name BRANCH [SHA] [DIR]
# Prints the card file name (no folder) for a branch.
#   - "/" becomes "__" because a "/" in a file name would create a subfolder
#     (feat/42-x -> feat__42-x.md)
#   - no branch (detached HEAD) -> "detached-<short sha>.md", so the card still has a unique name
ledger_card_name() {
  local branch="$1" sha="${2:-HEAD}" dir="${3:-.}"
  if [ -z "$branch" ]; then
    branch="detached-$(git -C "$dir" rev-parse --short "$sha")"
  fi
  printf '%s.md\n' "${branch//\//__}"
}
