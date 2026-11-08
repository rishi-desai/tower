#!/usr/bin/env python3
"""precompact-decisions.py: Claude Code PreCompact hook. Asks the compaction summary to keep, word for word, every
decision or discovery the worker hasn't added to its decisions log yet, and the exact next step (#97,
docs/adr/0011-precompact-asks-the-summary-for-decisions-not-yet-in-the-log.md).

On PreCompact, Claude Code adds plain stdout to the instructions for the compaction summary, after the user's own
/compact focus. The log itself comes back after compaction through worker-memory.py; this hook covers what never got
into it. hooks.json runs it on manual and auto compaction. The text comes from tower's core (src/tower/memory.py)
through the plugin's lib/tower link, under Claude Code's 10,000-character cap.

The hook never blocks or fails the compaction: input it can't use, a core that won't import, or any error prints
nothing and exits 0 (the reason goes to stderr, Claude Code's debug log). Then the summary is Claude Code's alone.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# From this file's place, like run-tower.py: the plugin's lib/ holds the link to tower's core.
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def summary_instructions(stdin_text: str) -> str:
    """The instructions to add for one PreCompact call, or "" when there's nothing to ask for."""
    call = json.loads(stdin_text)
    cwd = call.get("cwd") if isinstance(call, dict) else None
    if not isinstance(cwd, str) or not cwd:
        return ""
    sys.path.insert(0, os.path.join(PLUGIN_ROOT, "lib"))
    from tower import memory

    return memory.compaction_instructions(Path(cwd))


if __name__ == "__main__":
    try:
        text = summary_instructions(sys.stdin.read())
    except Exception as error:  # noqa: BLE001 - a PreCompact hook must never block or fail the compaction
        print(f"precompact-decisions: {type(error).__name__}: {error}", file=sys.stderr)  # Claude Code's debug log
        text = ""
    if text:
        sys.stdout.write(text)
    sys.exit(0)
