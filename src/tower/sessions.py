"""sessions.py: the Claude Code sessions open in each worktree, from `claude agents --json` (agent view's list).

That list holds interactive sessions (terminal panes, Orca panes) and background ones, each with its working folder
and a status. Its fields (kind, cwd, status, state, waitingFor, name, id) aren't a documented contract, so anything
unexpected means "unavailable", never a crash. state.py decides each worktree's status from these sessions; render()
draws the scans' LIVE SESSIONS section, flagging several interactive sessions in one worktree (the workflow forbids it).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable

from tower.gitrepo import COMMAND_TIMEOUT_SECONDS


def read_agents() -> tuple[list[object] | None, str]:
    """`claude agents --json`'s list, or None and why it couldn't be read."""
    try:
        proc = subprocess.run(
            ["claude", "agents", "--json"], check=False, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS
        )
    except FileNotFoundError:
        return None, "claude CLI not on PATH, so live sessions weren't read"
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"claude agents --json didn't run ({type(error).__name__}), so live sessions weren't read"
    if proc.returncode != 0:
        why = (proc.stderr.strip().splitlines() or [f"exit {proc.returncode}"])[-1]
        return None, f"claude agents --json failed ({why}), so live sessions weren't read"
    try:
        listed = json.loads(proc.stdout)
    except ValueError:
        listed = None
    if not isinstance(listed, list):
        return None, "claude agents --json printed something unexpected (its format may have changed)"
    return listed, ""


def owning_worktree(cwd: str, worktrees: Iterable[str]) -> str | None:
    """The worktree path containing cwd, the longest match: worktrees can sit inside the main checkout
    (poot/worktrees/tracker is under poot/), so a plain prefix check would also credit the main checkout."""
    best = None
    for path in worktrees:
        if (cwd == path or cwd.startswith(path + os.sep)) and (best is None or len(path) > len(best)):
            best = path
    return best


def by_worktree(listed: list[object], worktrees: Iterable[str]) -> dict[str, list[dict[str, object]]]:
    """The listed sessions per worktree path (real paths). A finished background session is history, not a live claim
    on a worktree, and an entry without a folder can't be placed: both are left out."""
    paths = list(worktrees)
    found: dict[str, list[dict[str, object]]] = {}
    for entry in listed:
        if not isinstance(entry, dict) or not isinstance(entry.get("cwd"), str):
            continue
        if entry.get("kind") == "background" and entry.get("state") == "done":
            continue
        owner = owning_worktree(os.path.realpath(entry["cwd"]), paths)
        if owner:
            found.setdefault(owner, []).append(entry)
    return found


def describe(entry: dict[str, object]) -> str:
    """One short label: name(kind:status), plus what it's waiting for if it says. Interactive sessions report
    `status` (idle/busy/waiting); background ones report `state`."""
    name = entry.get("name") or entry.get("id") or "?"
    kind = str(entry.get("kind") or "?")
    status = entry.get("status") or entry.get("state") or "?"
    label = f"{name}({kind[:3]}:{status})"
    waiting_for = entry.get("waitingFor")
    if waiting_for:
        label += f" waiting for: {str(waiting_for)[:60]}"
    return label


def render(listed: list[object], worktrees: list[tuple[str, str]]) -> list[str]:
    """The LIVE SESSIONS lines for (label, path) worktrees, in their order: one per worktree with sessions."""
    labels: dict[str, str] = {}  # real path -> label; a path listed twice keeps its first place and last label
    for label, path in worktrees:
        labels[os.path.realpath(path)] = label
    found = by_worktree(listed, labels)
    if not found:
        return ["(no open sessions in this repo)"]
    lines = []
    for path, label in labels.items():
        entries = found.get(path, [])
        if not entries:
            continue
        interactive = sum(1 for e in entries if e.get("kind") == "interactive")
        flag = f"  <-- {interactive} interactive sessions share this worktree" if interactive > 1 else ""
        lines.append(f"- {label} | {len(entries)} open: {', '.join(describe(e) for e in entries)}{flag}")
    return lines
