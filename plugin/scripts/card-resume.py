#!/usr/bin/env python3
"""card-resume.py: Claude Code UserPromptSubmit and PostToolUse (AskUserQuestion) hook. Flips this
worktree's status card from needs-you back to running the moment the user answers.

A card says needs-you while its session waits on the user. Once the user replies (types a prompt, or
answers the session's pop-up), the session goes back to work, but its card only catches up when the
session next stops (card-reminder.py). Until then the hub and the pane would list it as needing the
user. This hook closes that gap by rewriting two frontmatter lines, `status` and `updated`, and nothing
else: the card's body keeps the old question until the session rewrites the card itself.

Stays quiet (exit 0, no output) whenever there's nothing to do or it can't tell: not a git repo, tower
not set up on this machine, no card, or a card that doesn't say needs-you.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CARD_PATH_SCRIPT = os.path.join(PLUGIN_ROOT, "skills", "status-card", "scripts", "card-path.sh")
NEEDS_YOU = re.compile(r"^status:\s*needs-you\s*$")


def run(cmd: list[str], cwd: str) -> str | None:
    """Run cmd in cwd; its stdout, or None if it failed."""
    try:
        proc = subprocess.run(cmd, check=False, cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def resumed(card_text: str, now: str) -> str | None:
    """The card with status running and updated set to now, or None if its frontmatter isn't needs-you."""
    lines = card_text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None
    status_at = next((i for i in range(1, end) if NEEDS_YOU.match(lines[i])), None)
    if status_at is None:
        return None
    lines[status_at] = "status: running"
    for i in range(1, end):
        if lines[i].startswith("updated:"):
            lines[i] = f"updated: {now}"
    return "\n".join(lines)


def write_atomically(path: str, text: str) -> None:
    """Replace path's content in one step, so the hub never reads a half-written card."""
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".card-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    cwd = event.get("cwd") or os.getcwd()
    top = run(["git", "rev-parse", "--show-toplevel"], cwd)
    if top is None:
        return 0
    top = top.strip()
    card = run(["bash", CARD_PATH_SCRIPT, top], top)
    if card is None:
        return 0                                       # tower isn't set up here (card-path.sh says why on its own)
    card = card.strip()
    try:
        with open(card, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return 0                                       # no card yet
    updated = resumed(text, time.strftime("%Y-%m-%dT%H:%M"))
    if updated is None:
        return 0
    try:
        write_atomically(card, updated)
    except OSError as error:
        print(f"tower: couldn't mark the card running ({card}): {error}", file=sys.stderr)
        return 0                                       # never block the user's answer over a card
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event.get("hook_event_name", "UserPromptSubmit"),
            "additionalContext": (
                f"tower: the user just answered, so this worktree's status card ({card}) was switched from "
                "needs-you to running. Its Needs you section still holds the old question: rewrite the card "
                "with the status-card skill when you next stop."
            ),
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
