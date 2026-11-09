#!/usr/bin/env python3
"""card-reminder.py: Claude Code Stop hook. Reminds a session to update its status card before it stops.

Cards only exist if the agent remembers to write one, and the hub is only as good as its cards. When a
session tries to stop, we check: did files in this worktree change during this session, after the card
was last written? If so, we send Claude back once with "update your card". The second stop always goes
through (Claude Code sets stop_hook_active), so this can never loop, and Claude may decide the change
isn't worth a card.

"During this session" matters: a worktree can hold files and commits this session didn't make. Only
changes newer than the session's own start (its transcript file's creation time) count. The main
checkout is skipped outright: it's the hub's, which edits nothing and needs no card, yet `tower clean`
pulls merge commits newer than the hub's start into it.

Stays quiet (exit 0, no output) in the main checkout, and whenever it can't tell: not a git repo,
tower not set up on this machine, no transcript. A reminder we're unsure about is worse than none.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUN_TOWER = os.path.join(PLUGIN_ROOT, "scripts", "run-tower.py")
MAX_PATHS = 5000  # stop stat-ing after this many changed files; enough to find "something changed"


def run(cmd: list[str], cwd: str) -> str | None:
    """Run cmd in cwd; its stdout, or None if it failed."""
    try:
        proc = subprocess.run(cmd, check=False, cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def session_start(transcript_path: str) -> float | None:
    """When this session began: its transcript file's creation time (macOS keeps st_birthtime)."""
    try:
        st = os.stat(os.path.expanduser(transcript_path))
    except (OSError, TypeError):
        return None
    return getattr(st, "st_birthtime", None)


def top_and_main_checkout(cwd: str) -> tuple[str, bool] | None:
    """(top folder, whether it's the main checkout) for the worktree holding cwd, or None outside one. The main
    checkout is the one whose git dir is the shared one, the same test as tower's ledger.record_card."""
    paths = run(["git", "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir"], cwd)
    lines = paths.rstrip("\n").split("\n") if paths else []
    if len(lines) != 3:
        return None
    top, git_dir, common = lines
    return top, os.path.realpath(git_dir) == os.path.realpath(common)


def newest_change(top: str) -> float:
    """Latest mtime among the worktree's uncommitted files, or of the last commit, whichever is newer."""
    newest = 0.0
    committed = run(["git", "log", "-1", "--format=%ct"], top)
    if committed and committed.strip().isdigit():
        newest = float(committed.strip())
    status = run(["git", "status", "--porcelain", "-z"], top) or ""
    entries = status.split("\0")
    i = 0
    while i < len(entries) and i < MAX_PATHS:
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            i += 1  # renames/copies carry the old path as the next entry
        try:
            newest = max(newest, os.stat(os.path.join(top, path)).st_mtime)
        except OSError:
            pass  # deleted files have no mtime; nothing to compare
    return newest


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("stop_hook_active"):
        return 0  # we already sent Claude back once; let it stop
    cwd = event.get("cwd") or os.getcwd()
    started = session_start(event.get("transcript_path", ""))
    found = top_and_main_checkout(cwd)
    if started is None or found is None:
        return 0
    top, in_main_checkout = found
    if in_main_checkout:
        return 0  # the hub's checkout needs no card (see above)
    card = run([sys.executable, "-I", RUN_TOWER, "card-path", top], top)
    if card is None:
        return 0  # tower isn't set up here, or `tower card-path` failed: unsure, so quiet (see above)
    card = card.strip()
    try:
        card_written = os.stat(card).st_mtime
    except OSError:
        card_written = 0.0  # no card yet
    changed = newest_change(top)
    if changed <= max(started, card_written):
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "Stop",
                    "additionalContext": (
                        f"tower: files in this worktree changed after its status card was last written ({card}). "
                        "Use the status-card skill to update the card before stopping. "
                        "If nothing this turn is worth reporting to the hub, say so in one line and stop."
                    ),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
