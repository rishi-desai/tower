#!/usr/bin/env python3
"""record-event.py: Claude Code hook for SessionStart, UserPromptSubmit, Notification, PostToolUse (AskUserQuestion),
Stop and SessionEnd. Appends one event per call to tower's event log (~/.claude/tower/events.jsonl).

The work happens in tower's core (src/tower/hook_events.py), which the plugin reaches through its `lib/tower`
symlink; a marketplace install copies the link's target into the plugin cache. Nothing reads the log yet.

Never fails or changes the session: it prints nothing and exits 0. If the core can't even be imported (the link is
missing, or this python3 is too old), that is noted in ~/.claude/tower/errors.log and the hook still exits 0.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def tower_dir() -> Path:
    """Same as tower.events.default_dir(), repeated here for when the core won't import."""
    return Path(os.environ.get("TOWER_DIR") or Path.home() / ".claude" / "tower")


def note_import_failure() -> None:
    """Append why the core wouldn't import to errors.log; if that fails too, say so on stderr."""
    message = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} record-event: can't import tower's core\n{traceback.format_exc()}"
    try:
        tower_dir().mkdir(parents=True, exist_ok=True)
        with open(tower_dir() / "errors.log", "a", encoding="utf-8") as handle:
            handle.write(message)
    except Exception:  # noqa: BLE001 - nowhere left to record it; stderr goes to Claude Code's debug log
        print(message, file=sys.stderr)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(PLUGIN_ROOT, "lib"))
    try:
        from tower import events, hook_events
    except Exception:  # noqa: BLE001 - any import failure (missing link, old python3) must still exit 0
        note_import_failure()
        sys.exit(0)
    sys.exit(hook_events.main(sys.stdin, events.default_dir()))
