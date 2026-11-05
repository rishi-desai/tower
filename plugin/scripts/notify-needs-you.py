#!/usr/bin/env python3
"""notify-needs-you.py: Claude Code Notification hook. Shows a macOS notification naming the project and worktree.

Claude Code runs this when a session needs the user (an approval prompt, a finished reply nobody has read,
a background session waiting on input) and sends the event as JSON on stdin. Claude Code's own
notification says only "Claude needs your permission"; with several sessions open you can't tell which
one. We add the project and branch: "tower · poot" / "rolly-polly" / "Claude is waiting for your input".

Never blocks or fails the session: any problem is printed to stderr and we exit 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def git(cwd: str, *args: str) -> str:
    """Run one git command in cwd and return its trimmed stdout, or "" if it fails."""
    try:
        proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def where(cwd: str) -> tuple[str, str]:
    """(project, worktree label) for cwd. Same repo naming as ledger-lib.sh: the folder holding the shared .git."""
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return os.path.basename(cwd) or cwd, ""                     # not a git repo: just the folder name
    if os.path.basename(common) == ".git":
        project = os.path.basename(os.path.dirname(common))
    else:
        project = os.path.basename(common).removesuffix(".git")     # bare repo
    branch = git(cwd, "branch", "--show-current") or "detached " + git(cwd, "rev-parse", "--short", "HEAD")
    return project, branch


def notify(title: str, subtitle: str, message: str) -> None:
    """Show a macOS notification. Text goes in as arguments, never spliced into the AppleScript, so quotes are safe."""
    script = [
        "-e", "on run argv",
        "-e", "display notification (item 3 of argv) with title (item 1 of argv) subtitle (item 2 of argv)",
        "-e", "end run",
    ]
    try:
        proc = subprocess.run(["osascript", *script, title, subtitle, message], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as err:
        print(f"notify-needs-you: osascript failed: {err}", file=sys.stderr)
        return
    if proc.returncode != 0:
        print(f"notify-needs-you: osascript failed: {proc.stderr.strip()}", file=sys.stderr)


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        print("notify-needs-you: stdin wasn't JSON; nothing to show", file=sys.stderr)
        return 0
    cwd = event.get("cwd") or os.getcwd()
    project, branch = where(cwd)
    message = event.get("message") or "A session needs you"
    notify(f"tower · {project}", branch, message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
