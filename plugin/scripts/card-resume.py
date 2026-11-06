#!/usr/bin/env python3
"""card-resume.py: Claude Code UserPromptSubmit and PostToolUse (AskUserQuestion) hook. Re-renders this worktree's
status card from needs-you to running the moment the user answers.

A card says needs-you while its session waits on the user. Once the user replies (types a prompt, or answers the
session's pop-up), the session goes back to work, but its card only catches up when the session next stops
(card-reminder.py). Until then the hub and the pane would list it as needing the user. The answer itself is recorded
as an event by record-event.py, the next hook in the same list; this one has tower's core re-render the card
(src/tower/card.py, resume()): status running, updated now, the reason dropped, every other property and the body kept,
so the card's Needs you section keeps the old question until the session rewrites the card itself.

Stays quiet (exit 0, no output) whenever there's nothing to do or it can't tell: not a git repo, tower not set up on
this machine, no card, a card that doesn't say needs-you, or a core that won't import.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# From this file's place, like run-tower.py: the core is reached through the plugin's lib/tower link.
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    sys.path.insert(0, os.path.join(PLUGIN_ROOT, "lib"))
    try:
        from tower import card, ledger
        from tower.gitrepo import NotARepo
    except Exception as error:  # noqa: BLE001 - any import failure (missing link, old python3) must not block the user
        print(f"tower: card-resume can't import tower's core: {error}", file=sys.stderr)
        return 0
    try:
        path = card.resume(Path(event.get("cwd") or os.getcwd()))
    except (NotARepo, ledger.LedgerError):
        return 0  # not a repo, or tower isn't set up on this machine
    except OSError as error:
        print(f"tower: couldn't mark the card running: {error}", file=sys.stderr)
        return 0  # never block the user's answer over a card
    if path is None:
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event.get("hook_event_name", "UserPromptSubmit"),
                    "additionalContext": (
                        f"tower: the user just answered, so this worktree's status card ({path}) was switched from "
                        "needs-you to running. Its Needs you section still holds the old question: rewrite the card "
                        "with the status-card skill when you next stop."
                    ),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
