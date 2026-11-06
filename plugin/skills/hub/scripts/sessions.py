#!/usr/bin/env python3
"""sessions.py: which Claude Code sessions are open in each worktree, and what state they're in.

  sessions.py [--states-file FILE] BRANCH=PATH [BRANCH=PATH ...]

--states-file also writes one tab-separated line per worktree with sessions, for hub-scan.sh to group by:
  BRANCH <tab> interactive count <tab> names of sessions waiting on the user (comma-separated, may be empty)
The possibly-empty field is last because bash's `read` merges adjacent tabs, which would shift the fields.

Reads `claude agents --json` (agent view's machine-readable list). That list includes interactive
sessions (terminal panes, Orca panes) as well as background ones, each with its working folder and
a status. We match each session to the worktree that contains its folder and print one line per
worktree that has sessions, flagging the "several sessions in one worktree" case the workflow
forbids.

The JSON fields (kind, cwd, status, state, waitingFor, name) are not a documented contract. If the
command is missing, slow, or its shape changes, we print one line saying why and exit 0, so the
rest of the hub scan still runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

TIMEOUT_SECONDS = 15


def load_sessions() -> list[dict[str, Any]] | None:
    """Run `claude agents --json`. Returns None (after printing why) if it can't be used."""
    try:
        proc = subprocess.run(
            ["claude", "agents", "--json"], check=False, capture_output=True, text=True, timeout=TIMEOUT_SECONDS
        )
    except FileNotFoundError:
        print("(claude CLI not on PATH; skipping live sessions)")
        return None
    except subprocess.TimeoutExpired:
        print(f"(claude agents --json took over {TIMEOUT_SECONDS}s; skipping live sessions)")
        return None
    if proc.returncode != 0:
        reason = (proc.stderr.strip().splitlines() or [f"exit {proc.returncode}"])[-1]
        print(f"(claude agents --json failed: {reason})")
        return None
    try:
        sessions = json.loads(proc.stdout)
    except ValueError:
        print("(claude agents --json printed something that isn't JSON; its format may have changed)")
        return None
    if not isinstance(sessions, list):
        print("(claude agents --json format changed: expected a list)")
        return None
    return sessions


def owning_worktree(cwd: str, worktrees: dict[str, str]) -> str | None:
    """The worktree path containing cwd, choosing the LONGEST match.

    Longest wins because worktrees can live inside the main checkout (poot/worktrees/tracker sits
    under poot/), so a plain "starts with" check would also credit the main checkout.
    """
    best = None
    for path in worktrees:
        inside = cwd == path or cwd.startswith(path + os.sep)
        if inside and (best is None or len(path) > len(best)):
            best = path
    return best


def describe(session: dict[str, Any]) -> str:
    """One short label: name(status), plus what it's waiting for if it says."""
    name = session.get("name") or session.get("id") or "?"
    kind = session.get("kind", "?")
    # Interactive sessions report `status` (idle/busy/waiting); background ones report `state`.
    status = session.get("status") or session.get("state") or "?"
    label = f"{name}({kind[:3]}:{status})"
    waiting_for = session.get("waitingFor")
    if waiting_for:
        label += f" waiting for: {str(waiting_for)[:60]}"
    return label


def is_waiting(session: dict[str, Any]) -> bool:
    """True when the session is stopped on the user: status/state `waiting`, or it says what it waits for."""
    return (session.get("status") or session.get("state")) == "waiting" or bool(session.get("waitingFor"))


def main(argv: list[str]) -> int:
    states_file = None
    if argv[:1] == ["--states-file"] and len(argv) > 1:
        states_file, argv = argv[1], argv[2:]
    worktrees: dict[str, str] = {}  # real path -> branch label
    for arg in argv:
        branch, _, path = arg.partition("=")
        if path:
            worktrees[os.path.realpath(path)] = branch

    sessions = load_sessions()
    if sessions is None:
        return 0

    by_worktree: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        cwd = session.get("cwd")
        if not isinstance(cwd, str):
            continue
        # A finished background session is history, not a live claim on a worktree.
        if session.get("kind") == "background" and session.get("state") == "done":
            continue
        owner = owning_worktree(os.path.realpath(cwd), worktrees)
        if owner:
            by_worktree.setdefault(owner, []).append(session)

    if not by_worktree:
        print("(no open sessions in this repo)")
        return 0

    state_lines = []
    for path, branch in worktrees.items():
        found = by_worktree.get(path, [])
        if not found:
            continue
        interactive = sum(1 for s in found if s.get("kind") == "interactive")
        flag = f"  <-- {interactive} interactive sessions share this worktree" if interactive > 1 else ""
        print(f"- {branch} | {len(found)} open: {', '.join(describe(s) for s in found)}{flag}")
        waiting = ",".join(str(s.get("name") or s.get("id") or "?") for s in found if is_waiting(s))
        state_lines.append(f"{branch}\t{interactive}\t{waiting}\n")
    if states_file:
        with open(states_file, "w") as out:
            out.writelines(state_lines)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
