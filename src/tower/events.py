"""events.py: tower's event log, one append-only JSON Lines file for every repo on this machine.

Hooks record facts here (a session started, the user answered, a session needs the user); later parts of the core
read them back. Each line is one event: `ts`, `type`, `session_id`, `repo`, `worktree`, `branch`, then `data`, the
type-specific payload. The log lives at `<tower dir>/events.jsonl`, where the tower dir is `~/.claude/tower` unless
TOWER_DIR says otherwise (tests use that).

Concurrency: many sessions append at once. Each append takes an exclusive lock on `events.lock` (a file that is never
renamed or deleted), then writes the whole line with one O_APPEND write, so lines never interleave.

Rotation: the log holds one month (UTC). When an event from a later month arrives, the current file is renamed to
`events-YYYY-MM.jsonl` after the month of its first event, and a new one starts.

The hooks import this module under whatever `python3` they find (3.9 on a stock Mac), so it stays 3.9-compatible and
uses the standard library only.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

LOG_NAME = "events.jsonl"
LOCK_NAME = "events.lock"


@dataclass(frozen=True)
class Event:
    """One fact for the log. ts is ISO 8601 UTC ("2026-11-06T04:12:33.120Z"); repo/worktree/branch are None
    outside git; data is the type-specific payload."""

    ts: str
    type: str
    session_id: str | None
    repo: str | None
    worktree: str | None
    branch: str | None
    data: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> str:
        """The event as one compact JSON line (no newline), common fields first in a fixed order."""
        record = {
            "ts": self.ts,
            "type": self.type,
            "session_id": self.session_id,
            "repo": self.repo,
            "worktree": self.worktree,
            "branch": self.branch,
            "data": self.data,
        }
        return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def default_dir() -> Path:
    """Where tower keeps its machine-wide state: $TOWER_DIR if set, else ~/.claude/tower."""
    return Path(os.environ.get("TOWER_DIR") or Path.home() / ".claude" / "tower")


def append(event: Event, directory: Path) -> None:
    """Append event to directory's log as one line, holding the log's lock; creates directory if needed."""
    directory.mkdir(parents=True, exist_ok=True)
    line = (event.to_json() + "\n").encode("utf-8")
    with open(directory / LOCK_NAME, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            rotate_if_new_month(directory, event.ts[:7])
            write_all(directory / LOG_NAME, line)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def tail(directory: Path, count: int) -> list[str]:
    """The last count lines of directory's current log (this month only), oldest first; [] if there is no log."""
    try:
        with open(directory / LOG_NAME, encoding="utf-8") as handle:
            return [line.rstrip("\n") for line in deque(handle, maxlen=count)]
    except FileNotFoundError:
        return []


def rotate_if_new_month(directory: Path, month: str) -> None:
    """If the log's first event is from a month before month ("YYYY-MM"), rename the log after that month.

    Called with the lock held. If the first line can't be read (a torn or hand-edited file), the month of the log's
    last write stands in for it, so a damaged log still rotates instead of growing forever. An existing month file is
    never overwritten; the next free `events-YYYY-MM.N.jsonl` is used.
    """
    log = directory / LOG_NAME
    try:
        with open(log, encoding="utf-8") as handle:
            first = handle.readline()
    except FileNotFoundError:
        return
    try:
        log_month = str(json.loads(first)["ts"])[:7]
    except (ValueError, KeyError, TypeError):
        log_month = time.strftime("%Y-%m", time.gmtime(log.stat().st_mtime))
    if log_month >= month:
        return
    target = directory / f"events-{log_month}.jsonl"
    suffix = 1
    while target.exists():
        suffix += 1
        target = directory / f"events-{log_month}.{suffix}.jsonl"
    os.rename(log, target)


def write_all(path: Path, data: bytes) -> None:
    """Append data to path with O_APPEND, looping over short writes (safe: the caller holds the lock)."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    finally:
        os.close(fd)
