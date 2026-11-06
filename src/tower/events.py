"""events.py: tower's event log, one append-only JSON Lines file for every repo on this machine.

Hooks record facts here (a session started, the user answered, a session needs the user); later parts of the core
read them back. Each line is one event: `ts`, `type`, `session_id`, `repo`, `worktree`, `branch`, then `data`, the
type-specific payload. The log lives at `<tower dir>/events.jsonl`, where the tower dir is `~/.claude/tower` unless
TOWER_DIR says otherwise (tests use that).

Concurrency: many sessions append at once. Each append takes an exclusive lock on `events.lock` (a file that is never
renamed or deleted), then writes the whole line with one O_APPEND write, so lines never interleave.

Rotation: the log holds one month (UTC). When an event from a later month arrives, the current file is renamed to
`events-YYYY-MM.jsonl` after the month of its first event, and a new one starts.

Order: lines are in append order. ts is stamped by the hook before it waits for the lock, so two sessions' events can
be a few milliseconds out of ts order, and an event stamped in a month's last instant can land in the next month's
file. Readers that need strict order sort by ts.

The hooks import this module under whatever `python3` they find (3.9 on a stock Mac), so it stays 3.9-compatible and
uses the standard library only.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

LOG_NAME = "events.jsonl"
LOCK_NAME = "events.lock"
MONTH = re.compile(r"\d{4}-\d{2}")


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
        # ASCII-only output: an unpaired surrogate (valid in JSON input, not encodable as UTF-8) can't lose the event.
        return json.dumps(record, separators=(",", ":"))


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
    """The last count lines of directory's current log (this month only), oldest first; [] if there is no log.

    Bytes that aren't UTF-8 (a damaged line) come back as U+FFFD instead of failing the whole read.
    """
    try:
        with open(directory / LOG_NAME, encoding="utf-8", errors="replace") as handle:
            return [line.rstrip("\n") for line in deque(handle, maxlen=count)]
    except FileNotFoundError:
        return []


def read_all(directory: Path) -> tuple[list[Event], int]:
    """Every event in directory's logs (this month's and every rotated month), sorted by ts, plus how many lines were
    skipped because they aren't events (torn, not UTF-8, not a JSON object with a string ts and type).

    Holds the log's lock shared while reading, so a rotation can't move a file between listing and reading it.
    """
    if not directory.is_dir():
        return [], 0
    read: list[Event] = []
    damaged = 0
    with open(directory / LOCK_NAME, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        try:
            for path in sorted(directory.glob("events*.jsonl")):
                with open(path, "rb") as handle:
                    for line in handle:
                        event = parse_line(line)
                        if event is None:
                            damaged += 1
                        else:
                            read.append(event)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    read.sort(key=lambda event: event.ts)  # stable: equal ts keep append order
    return read, damaged


def parse_line(line: bytes) -> Event | None:
    """The event on one log line, or None if the line isn't one (see read_all)."""
    try:
        record = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("ts"), str) or not isinstance(record.get("type"), str):
        return None
    data = record.get("data")

    def text(key: str) -> str | None:
        value = record.get(key)
        return value if isinstance(value, str) else None

    return Event(
        ts=record["ts"],
        type=record["type"],
        session_id=text("session_id"),
        repo=text("repo"),
        worktree=text("worktree"),
        branch=text("branch"),
        data=data if isinstance(data, dict) else {},
    )


def rotate_if_new_month(directory: Path, month: str) -> None:
    """If the log's first event is from a month before month ("YYYY-MM"), rename the log after that month.

    Called with the lock held. If the first line can't be read (a torn or hand-edited file), the month of the log's
    last write stands in for it, so a damaged log still rotates instead of growing forever. An existing month file is
    never overwritten; the next free `events-YYYY-MM.N.jsonl` is used.
    """
    log = directory / LOG_NAME
    try:
        with open(log, "rb") as handle:
            first = handle.readline()
    except FileNotFoundError:
        return
    if not first:
        return  # empty: the event about to be appended becomes the first
    log_month = first_event_month(first) or time.strftime("%Y-%m", time.gmtime(log.stat().st_mtime))
    if log_month >= month:
        return
    target = directory / f"events-{log_month}.jsonl"
    suffix = 1
    while target.exists():
        suffix += 1
        target = directory / f"events-{log_month}.{suffix}.jsonl"
    os.rename(log, target)


def first_event_month(first_line: bytes) -> str | None:
    """The "YYYY-MM" of the event on first_line, or None if the line isn't an event with an ISO date in ts."""
    try:
        ts = json.loads(first_line.decode("utf-8")).get("ts")
    except (UnicodeDecodeError, ValueError, AttributeError):
        return None
    if not isinstance(ts, str) or not MONTH.match(ts):
        return None
    return ts[:7]


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
