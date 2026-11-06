#!/usr/bin/env bash
# setup.sh — sets up tower (parallel Claude Code agents) on THIS machine. Run once per Mac.
#
# Usage:
#   ./setup.sh                    interactive: asks for the projects folder, WIP limit and the machine's profile
#   ./setup.sh --dry-run          show every change it would make; change nothing
#   ./setup.sh --projects-dir PATH --wip-limit N --profile work|personal|sandbox   no prompts
#   --profile NAME                this machine's default approval profile (work: everything asks; personal; sandbox:
#                                 nothing asks; or one your policy file defines). Default: keep the current one
#   --plugin-source SRC           where to install the plugin from (default: rishi-desai/tower on GitHub;
#                                 a local path works for testing before pushing)
#   --no-plugin                   skip step 5 (e.g. you install the plugin another way)
#
# The skills and the approval hook ship as a Claude Code plugin (installed under ~/.claude/plugins,
# separate from ~/.claude/skills). This script does the per-machine parts. Safe to re-run:
#   1. writes ~/.claude/tower.conf        projects folder and WIP limit
#      and ~/.claude/tower/policy.toml   the default profile (tower.conf's old push/PR approval migrated first)
#   2. creates ~/.claude/tower/rules/     personal per-project rules (protected by Claude Code)
#   3. adds CLAUDE.local.md to your global git ignore, so personal rules are never committed
#   4. refreshes the marked block in ~/.claude/CLAUDE.md (original backed up once)
#   5. adds the tower marketplace and installs the tower plugin (skills, hooks and the pane) with `claude plugin`;
#      an older install's separate tower-pane plugin is uninstalled, since tower now carries the pane

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"     # this checkout of the tower repo
claude_dir="$HOME/.claude"                # Claude Code's global config folder
conf="$claude_dir/tower.conf"
rules_dir="$claude_dir/tower/rules"
policy_file="${TOWER_DIR:-$claude_dir/tower}/policy.toml"

dry_run=0; projects_dir=""; wip_limit=""; profile=""; plugin_source="rishi-desai/tower"; plugin=1
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)       dry_run=1 ;;
    --projects-dir)  projects_dir="${2:?--projects-dir needs a path}"; shift ;;
    --wip-limit)     wip_limit="${2:?--wip-limit needs a number}"; shift ;;
    --profile)       profile="${2:?--profile needs work, personal, sandbox or a profile name}"; shift ;;
    --push-approval|--pr-approval)
                     echo "$1 was replaced by --profile work|personal|sandbox (README: Policy profiles)" >&2; exit 2 ;;
    --plugin-source) plugin_source="${2:?--plugin-source needs a GitHub repo or path}"; shift ;;
    --no-plugin)     plugin=0 ;;
    -h|--help)       sed -n '2,22p' "$0"; exit 0 ;;
    *)               echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

# run CMD... — do it, or in dry-run mode just say what would be done.
run() {
  if [ "$dry_run" -eq 1 ]; then echo "  [dry-run] $*"; else "$@"; fi
}

# ---------- preflight: fail before changing anything ----------
git_version="$(git --version | awk '{print $3}')"
if [ "$(printf '%s\n2.31\n' "$git_version" | sort -V | head -n1)" != "2.31" ]; then
  echo "git $git_version is too old: the scripts need 2.31+ (for --path-format)." >&2; exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  # The approval hook is Python; without it public GitHub actions would NOT be confirmed. Refuse.
  echo "python3 not found: the approval hook, pr-wait and the live-sessions view need it." >&2; exit 1
fi
if [ "$plugin" -eq 1 ] && ! command -v claude >/dev/null 2>&1; then
  echo "claude CLI not found: install Claude Code first, or pass --no-plugin." >&2; exit 1
fi

# ---------- 1. per-machine config ----------
# conf_get KEY [DEFAULT] — KEY's saved value, read by tower's core (src/tower/ledger.py) so it parses the file the same way.
conf_get() {
  python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from tower import ledger
print(ledger.conf_get(ledger.read_conf(Path(sys.argv[2])), sys.argv[3], sys.argv[4]))' "$here/plugin/lib" "$conf" "$1" "${2-}"
}
# policy ARGS... — `tower policy ARGS`, run from this checkout's plugin (src/tower/policy.py does the work).
policy() {
  TOWER_CONF="$conf" python3 -I "$here/plugin/scripts/run-tower.py" policy "$@"
}
# policy_py CODE — run Python CODE with this checkout's tower.policy imported as `policy`, reading this machine's tower.conf.
policy_py() {
  TOWER_CONF="$conf" python3 -I -c "import sys; sys.path.insert(0, sys.argv[1]); from tower import policy
$1" "$here/plugin/lib"
}

# Suggest the open Obsidian vault + /Projects the first time; afterwards, whatever is saved.
default_dir="$(conf_get projects_dir)"
if [ -z "$default_dir" ]; then
  vault="$(python3 -I -c '
import json, os, sys
try:
    vaults = json.load(open(os.path.expanduser("~/Library/Application Support/obsidian/obsidian.json")))["vaults"].values()
except Exception:
    sys.exit()
open_vaults = [v["path"] for v in vaults if v.get("open")] or [v["path"] for v in vaults]
print(open_vaults[0] if open_vaults else "")' 2>/dev/null || true)"
  [ -n "$vault" ] && default_dir="$vault/Projects"
fi
default_wip="$(conf_get wip_limit 3)"
# The profile as things stand (tower.conf's old push/PR approval count until they're migrated), read without writing.
default_profile="$(policy_py 'print(policy.current_default())')"

ask() {   # ask PROMPT DEFAULT -> prints the answer (or the default on Enter)
  local answer
  if [ ! -t 0 ]; then echo "no terminal to ask '$1'; pass it as a flag (see --help)" >&2; exit 2; fi
  read -r -p "$1 [${2:-none}]: " answer
  printf '%s\n' "${answer:-$2}"
}
[ -n "$projects_dir" ]  || projects_dir="$(ask "Folder holding one subfolder per project (cards go in <Project>/Ledger/)" "$default_dir")"
[ -n "$wip_limit" ]     || wip_limit="$(ask "WIP limit: worktrees running or needing you at once" "$default_wip")"
if [ -z "$profile" ]; then   # newer than the other flags: scripted runs without it keep the current profile
  if [ -t 0 ]; then
    profile="$(ask "This machine's profile: work (everything asks) | personal (force pushes, merges into main and comments ask) | sandbox (nothing asks)" "$default_profile")"
  else profile="$default_profile"; fi
fi
projects_dir="${projects_dir/#\~/$HOME}"                  # allow ~/... in the answer
case "$wip_limit" in ''|*[!0-9]*) echo "WIP limit must be a whole number, got '$wip_limit'" >&2; exit 2 ;; esac
profiles="$(policy_py 'print(" ".join(policy.available_profiles()))')"
case " $profiles " in *" $profile "*) ;; *) echo "profile must be one of: $profiles; got '$profile'" >&2; exit 2 ;; esac
[ -n "$projects_dir" ] || { echo "a projects folder is required" >&2; exit 2; }

if [ ! -d "$projects_dir" ]; then
  echo "Projects folder doesn't exist yet: $projects_dir"
  if [ "$dry_run" -eq 0 ] && [ -t 0 ]; then
    read -r -p "Create it? [y/N]: " yn
    case "$yn" in [yY]*) ;; *) echo "Not created; nothing set up."; exit 1 ;; esac
  fi
  run mkdir -p "$projects_dir"
fi

echo "1. Config: $conf, and the profile in $policy_file"
if [ -n "$(policy_py 'print(policy.migration_profile() or "")')" ]; then
  # Before tower.conf is rewritten without them: keep what push_approval/pr_approval did, as a policy file.
  if [ "$dry_run" -eq 1 ]; then echo "  [dry-run] migrate push_approval/pr_approval from $conf into $policy_file"
  else policy_py 'policy.load()'; fi
fi
new_conf="$(mktemp)"; trap 'rm -f "$new_conf"' EXIT
{
  echo "# Per-machine settings for tower. Written by setup.sh; safe to edit."
  echo "# Folder holding one subfolder per project. Cards go in <projects_dir>/<Project>/Ledger/."
  echo "projects_dir=$projects_dir"
  echo "# Max worktrees in running or needs-you before the hub suggests parking new ideas."
  echo "wip_limit=$wip_limit"
  echo "# What asks before an agent pushes, merges or writes to GitHub is the profile in $policy_file"
  echo "# (\`tower policy\` shows it; \`tower policy use <profile>\` changes it). A profile can set its own wip_limit."
  echo "# Optional: map a repo folder name to a differently named project folder, e.g."
  echo "# project.poot=Poot"
  [ -f "$conf" ] && grep '^project\.' "$conf" || true    # keep your existing mappings
} > "$new_conf"
if [ "$dry_run" -eq 1 ]; then sed 's/^/  [dry-run] | /' "$new_conf"; else mkdir -p "$claude_dir"; cp "$new_conf" "$conf"; fi
if [ "$dry_run" -eq 1 ]; then echo "  [dry-run] tower policy use $profile"
else policy use "$profile" | sed 's/^/  /'; fi

# ---------- 2. personal rules folder ----------
echo "2. Rules folder: $rules_dir"
run mkdir -p "$rules_dir"                 # never emptied by this script: your rules live here

# ---------- 3. global git ignore ----------
excludes="$(git config --global core.excludesFile || true)"
excludes="${excludes/#\~/$HOME}"
[ -n "$excludes" ] || excludes="${XDG_CONFIG_HOME:-$HOME/.config}/git/ignore"   # git's default location
echo "3. Global git ignore: $excludes"
if grep -qxF "CLAUDE.local.md" "$excludes" 2>/dev/null; then
  echo "  already ignores CLAUDE.local.md"
else
  run mkdir -p "$(dirname "$excludes")"
  if [ "$dry_run" -eq 1 ]; then echo "  [dry-run] append 'CLAUDE.local.md'"; else echo "CLAUDE.local.md" >> "$excludes"; fi
fi

# ---------- 4. CLAUDE.md block ----------
global_md="$claude_dir/CLAUDE.md"
echo "4. Block in $global_md"
if [ "$dry_run" -eq 1 ]; then
  echo "  [dry-run] back up once to $global_md.pre-tower.bak (if absent) and to $global_md.bak"
  echo "  [dry-run] replace the <!-- BEGIN/END tower --> block with $here/CLAUDE.global.md"
else
  touch "$global_md"
  [ -e "$global_md.pre-tower.bak" ] || cp "$global_md" "$global_md.pre-tower.bak"   # your original, kept forever
  cp "$global_md" "$global_md.bak"                                                   # the copy from just before this run
  new_md="$(mktemp)"
  # Copy every line except our old block (BEGIN..END inclusive), then append the current block.
  # Blank lines are held back and only printed before the next real line, so trailing blanks
  # don't pile up by one per re-run.
  awk '/<!-- BEGIN tower -->/          {skip=1}
       !skip && /^[[:space:]]*$/       {blanks++; next}
       !skip                           {while (blanks > 0) {print ""; blanks--}; print}
       /<!-- END tower -->/            {skip=0; blanks=0}' "$global_md" > "$new_md"
  printf '\n' >> "$new_md"
  cat "$here/CLAUDE.global.md" >> "$new_md"
  mv "$new_md" "$global_md"
fi

# ---------- 5. plugin ----------
if [ "$plugin" -eq 0 ]; then
  echo "5. Plugin: skipped (--no-plugin)"
else
  echo "5. Plugin: marketplace 'tower' from $plugin_source, plugin tower@tower (skills, hooks and the pane)"
  # installed ID — whether `claude plugin list --json` has a plugin with exactly this id (unreadable output: no).
  installed() {
    claude plugin list --json 2>/dev/null | python3 -I -c 'import json, sys
try:
    plugins = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
sys.exit(0 if any(p.get("id") == sys.argv[1] for p in plugins) else 1)' "$1"
  }
  # Before 0.11.0 the pane was a second plugin. tower carries it now and the marketplace no longer lists it, so an
  # old copy is left failing to load. Upgrade first (marketplace, then tower), and uninstall the old pane only once
  # that worked, so a failed update never leaves the machine with no pane. A failed uninstall is reported, not hidden.
  old_pane=0
  installed tower-pane@tower && old_pane=1
  if claude plugin marketplace list 2>/dev/null | grep -qw tower; then
    if [ "$old_pane" -eq 1 ]; then
      run claude plugin marketplace update tower
    else
      echo "  marketplace already added (update with: claude plugin marketplace update tower)"
    fi
  else
    run claude plugin marketplace add "$plugin_source"
  fi
  if ! installed tower@tower; then
    run claude plugin install tower@tower
  elif [ "$old_pane" -eq 1 ]; then
    run claude plugin update tower@tower
  else
    echo "  plugin already installed (update with: claude plugin marketplace update tower && claude plugin update tower@tower)"
  fi
  if [ "$old_pane" -eq 1 ]; then
    run claude plugin uninstall tower-pane@tower ||
      echo "  couldn't uninstall the old tower-pane plugin; run: claude plugin uninstall tower-pane@tower" >&2
  fi
fi

echo
if [ "$dry_run" -eq 1 ]; then echo "Dry run: nothing was changed."; else echo "Set up. Restart Claude Code, open a repo, and run /tower:hub (it adopts a messy one first)."; fi
