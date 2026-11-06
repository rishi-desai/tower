"""hook_events.py: turns one Claude Code hook call into one event in tower's log.

plugin/scripts/record-event.py calls main() for SessionStart, UserPromptSubmit, Notification (the needs-you kinds),
PostToolUse on AskUserQuestion, Stop and SessionEnd. Each becomes an event type:

    SessionStart                  -> session-start  {source: startup|resume|clear|compact|fork}
    UserPromptSubmit              -> prompt         {prompt_chars}  (the length only; prompts can hold secrets)
    Notification                  -> needs-you      {notification_type, message}
    PostToolUse (AskUserQuestion) -> answer         {}
    Stop                          -> stop           {stop_hook_active}
    SessionEnd                    -> session-end    {reason}

Anything else is ignored. The hook must never fail or change the session: main() catches every error, appends it to
`<tower dir>/errors.log`, prints nothing to stdout, and returns 0.

Imported by the hooks under whatever `python3` they find (3.9 on a stock Mac), so it stays 3.9-compatible.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from tower import events

ERRORS_NAME = "errors.log"


def now_utc() -> str:
    """The current time as ISO 8601 UTC with milliseconds, e.g. "2026-11-06T04:12:33.120Z"."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def event_type_and_data(hook: dict[str, object]) -> tuple[str, dict[str, object]] | None:
    """The event type and payload for one hook call, or None if tower doesn't record this kind of call."""
    name = hook.get("hook_event_name")
    if name == "SessionStart":
        return "session-start", {"source": hook.get("source")}
    if name == "UserPromptSubmit":
        prompt = hook.get("prompt")
        return "prompt", {"prompt_chars": len(prompt) if isinstance(prompt, str) else None}
    if name == "Notification":
        return "needs-you", {"notification_type": hook.get("notification_type"), "message": hook.get("message")}
    if name == "PostToolUse" and hook.get("tool_name") == "AskUserQuestion":
        return "answer", {}
    if name == "Stop":
        return "stop", {"stop_hook_active": bool(hook.get("stop_hook_active"))}
    if name == "SessionEnd":
        return "session-end", {"reason": hook.get("reason")}
    return None


def git(cwd: str, *args: str) -> str | None:
    """Run one git command in cwd; its trimmed stdout, or None if git failed or isn't there."""
    try:
        proc = subprocess.run(["git", "-C", cwd, *args], check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def place(cwd: str) -> tuple[str | None, str | None, str | None]:
    """(repo, worktree, branch) for cwd, or Nones outside a git worktree.

    repo is the main checkout's absolute path (the folder holding the shared .git; a bare repo's own path), so every
    worktree of one repo shares it. branch is None when HEAD is detached.
    """
    paths = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir", "--show-toplevel")
    if paths is None or len(paths.splitlines()) != 2:
        return None, None, None
    common, worktree = paths.splitlines()
    repo = os.path.dirname(common) if os.path.basename(common) == ".git" else common
    branch = git(cwd, "symbolic-ref", "--short", "-q", "HEAD") or None
    return repo, worktree, branch


def record(hook: dict[str, object], directory: Path) -> None:
    """Append the event for one hook call to directory's log, if tower records this kind of call."""
    kind = event_type_and_data(hook)
    if kind is None:
        return
    cwd = hook.get("cwd")
    repo, worktree, branch = place(cwd if isinstance(cwd, str) and cwd else os.getcwd())
    session_id = hook.get("session_id")
    event = events.Event(
        ts=now_utc(),
        type=kind[0],
        session_id=session_id if isinstance(session_id, str) else None,
        repo=repo,
        worktree=worktree,
        branch=branch,
        data=kind[1],
    )
    events.append(event, directory)


def log_error(directory: Path, message: str) -> None:
    """Append one timestamped entry to directory's errors.log; if even that fails, say so on stderr."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / ERRORS_NAME, "a", encoding="utf-8") as handle:
            handle.write(f"{now_utc()} record-event: {message.rstrip()}\n")
    except Exception as error:  # noqa: BLE001 - nowhere left to record it; stderr goes to Claude Code's debug log
        print(f"tower record-event: {message.rstrip()} (and errors.log failed: {error})", file=sys.stderr)


def main(stdin: IO[str], directory: Path) -> int:
    """Record the hook call read from stdin. Always returns 0; errors go to errors.log, never to the session."""
    try:
        hook = json.load(stdin)
        if not isinstance(hook, dict):
            raise TypeError(f"hook input is a JSON {type(hook).__name__}, not an object")
        record(hook, directory)
    except Exception:  # noqa: BLE001 - a hook must never fail the session; the traceback goes to errors.log
        log_error(directory, traceback.format_exc())
    return 0
