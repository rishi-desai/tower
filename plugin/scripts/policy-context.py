#!/usr/bin/env python3
"""policy-context.py: Claude Code SessionStart hook. Tells the session, in one line of context, which tower policy
applies to its repo: its profile, what it may do without asking, and what asks the user first.

On SessionStart, Claude Code adds plain stdout to the session's context, so a worker knows before it acts which
pushes, merges and GitHub writes the approval hook (confirm-public.py) will let through, and the global CLAUDE.md
block can tell it to go ahead with those instead of stopping to show the user first. It runs again after /clear,
/resume and compaction, so the line is never lost.

The policy comes from tower's core (src/tower/policy.py) through the plugin's lib/tower link. The hook never fails the
session: input it can't use, a core that won't import, or any error prints nothing and exits 0. Then the session
simply doesn't get the line, and the approval hook still enforces the policy.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# From this file's place, like run-tower.py: the plugin's lib/ holds the link to tower's core.
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def context_line(stdin_text: str) -> str:
    """The line to add for one SessionStart call, or "" when there's nothing reliable to say."""
    call = json.loads(stdin_text)
    cwd = call.get("cwd") if isinstance(call, dict) else None
    if not isinstance(cwd, str) or not cwd:
        return ""
    sys.path.insert(0, os.path.join(PLUGIN_ROOT, "lib"))
    from tower import policy

    repo_policy, slug = policy.for_checkout(Path(cwd))
    return policy.session_line(repo_policy, slug)


if __name__ == "__main__":
    try:
        line = context_line(sys.stdin.read())
    except Exception as error:  # noqa: BLE001 - a SessionStart hook must never fail the session
        print(f"policy-context: {type(error).__name__}: {error}", file=sys.stderr)  # Claude Code's debug log
        line = ""
    if line:
        print(line)
    sys.exit(0)
