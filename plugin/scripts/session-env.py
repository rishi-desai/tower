#!/usr/bin/env python3
"""session-env.py: Claude Code SessionStart hook. Gives every session its worktree's runtime variables (TOWER_SLOT,
PORT and whatever else the project's runtime config declares), so two workers' dev servers never fight over a port.

Claude Code runs each Bash command in a fresh shell, so an `export` typed in one command is gone by the next. A
SessionStart hook can append `export` lines to the file named by $CLAUDE_ENV_FILE, and Claude Code applies them to
every later Bash command in the session. This hook writes `tower env`'s lines there (src/tower/runtime.py), then tells
Claude in one line which slot and port it has and, in a worktree, the seed command for what only the main checkout has.

Stays quiet (exit 0, no output) when there's nothing to do: not a git repo, a project with no runtime config, or a
tower.conf it can't read (said on stderr, Claude Code's debug log). A bad runtime config is told to Claude (so it can
tell the user) and nothing is set. A core that won't import is said on stderr. Always exit 0: a session's start never
fails over runtime variables.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

# From this file's place, like run-tower.py: the core is reached through the plugin's lib/tower link.
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    """Set the session's runtime variables and tell Claude about them; always exit 0."""
    try:
        event = json.load(sys.stdin)
    except ValueError as error:
        print(f"tower: session-env got hook input that isn't JSON ({error})", file=sys.stderr)
        return 0
    if not isinstance(event, dict):
        print("tower: session-env got hook input that isn't a JSON object", file=sys.stderr)
        return 0
    sys.path.insert(0, os.path.join(PLUGIN_ROOT, "lib"))
    try:
        from tower import ledger, runtime
        from tower.gitrepo import NotARepo
    except Exception as error:  # noqa: BLE001 - any import failure (missing link, old python3) must not stop the session
        print(f"tower: session-env can't import tower's core: {error}", file=sys.stderr)
        return 0
    try:
        env, config = runtime.worktree_env(Path(str(event.get("cwd") or os.getcwd())))
    except NotARepo:
        return 0
    except ledger.LedgerError as error:
        # tower.conf can't be read, so the project (and whether it has a runtime config) is unknown: every repo would
        # get this, so it stays in the debug log. The hub's scan reports an unreadable tower.conf.
        print(f"tower: session-env: {error}", file=sys.stderr)
        return 0
    except runtime.RuntimeConfigError as error:
        tell(f"tower: couldn't set this worktree's runtime variables: {error}. Dev servers may clash on ports with "
             "other worktrees; tell the user.", event)  # fmt: skip
        return 0
    except Exception as error:  # noqa: BLE001 - a bug in the core must not stop the session; it's logged, not hidden
        print(f"tower: session-env failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 0
    if env is None:
        return 0
    assignments = " ".join(f"{name}={shlex.quote(value)}" for name, value in env.variables)
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    written = False
    if env_file:
        try:
            with open(env_file, "a", encoding="utf-8") as out:
                out.write(runtime.render_exports(env))
            written = True
        except OSError as error:
            print(f"tower: couldn't write {env_file}: {error}", file=sys.stderr)
    if written:
        message = (
            f"tower: this worktree has runtime slot {env.slot} (from {config}), so every Bash command here has "
            f"{assignments} set. Run dev servers and databases on these, not their defaults."
        )
    else:
        message = (
            f"tower: this worktree has runtime slot {env.slot} (from {config}), but they couldn't be set for you: "
            f"start every dev server or database command with `export {assignments};`."
        )
    if env.seed:
        message += f" If this worktree lacks data that only the main checkout has, seed it once with: {env.seed}"
    tell(message, event)
    return 0


def tell(message: str, event: dict[str, object]) -> None:
    """Give Claude message as the hook's additional context."""
    output = {"hookEventName": event.get("hook_event_name") or "SessionStart", "additionalContext": message}
    print(json.dumps({"hookSpecificOutput": output}))


if __name__ == "__main__":
    sys.exit(main())
