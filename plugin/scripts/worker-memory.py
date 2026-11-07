#!/usr/bin/env python3
"""worker-memory.py: Claude Code SessionStart hook. Gives a worker its memory back when its context restarts: the hub's
kickoff, its current card and its decisions log (#93, docs/adr/0008-worker-memory-kickoff-in-the-card-decisions-log-
beside-it.md).

On SessionStart, Claude Code adds plain stdout to the session's context, after compaction too, so a compacted worker
still knows which reviews to run before its PR and what it already decided. hooks.json runs it on startup, clear and
compact only; a resumed or forked session still has its transcript. The text comes from tower's core
(src/tower/memory.py) through the plugin's lib/tower link, under Claude Code's 10,000-character cap.

The hook never fails the session: input it can't use, a core that won't import, or any error prints nothing and exits
0 (the reason goes to stderr, Claude Code's debug log). Then the session simply starts without its memory.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# From this file's place, like run-tower.py: the plugin's lib/ holds the link to tower's core.
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def memory_text(stdin_text: str) -> str:
    """The context to add for one SessionStart call, or "" when there's nothing to give back."""
    call = json.loads(stdin_text)
    cwd = call.get("cwd") if isinstance(call, dict) else None
    source = call.get("source") if isinstance(call, dict) else None
    if not isinstance(cwd, str) or not cwd or not isinstance(source, str):
        return ""
    sys.path.insert(0, os.path.join(PLUGIN_ROOT, "lib"))
    from tower import memory

    return memory.session_context(Path(cwd), source)


if __name__ == "__main__":
    try:
        text = memory_text(sys.stdin.read())
    except Exception as error:  # noqa: BLE001 - a SessionStart hook must never fail the session
        print(f"worker-memory: {type(error).__name__}: {error}", file=sys.stderr)  # Claude Code's debug log
        text = ""
    if text:
        sys.stdout.write(text)
    sys.exit(0)
