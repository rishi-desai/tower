"""stats.py: metrics per accepted change (a merged PR), computed from the event log. Backs `tower stats`.

A change's events are the log's events in the PR's repo on the PR's head branch, after the previous merge from that
branch (if it was reused) and up to this merge. From them:

- started_at, lead_time: the first event, and from it to the merge.
- active: your active time. Each input of yours (a `prompt` or an `answer`) counts the ACTIVE_WINDOW before it, as
  reading and typing; overlapping windows count once. Approvals of permission prompts aren't in the log yet, so they
  don't count.
- waits: in each session, the stretch from when the worker stopped for you to your next input. It opens at the last
  `stop` since your last input, or with no stop, at the first `needs-you` since then. The last stop, because a Stop
  hook like card-reminder can send the worker back to work, which shows as more stops; a stop before a `needs-you`,
  because a permission prompt you approve without typing leaves no event, and the worker ran on after it. waiting is
  their sum, interruptions their count, resume_median the median.
- reports: turns that ended in a `stop`: each stop-containing stretch your input closed, plus a trailing one.
  follow_ups: your prompts after the worker's first report in that session (an `answer` to AskUserQuestion isn't
  one), so a prompt sent while it is still working on your last one counts too.
- rework: later merged PRs from the same branch (more commits after it was done), plus reopens of its tickets after
  its merge. Later PRs for the same ticket from another branch don't count: a ticket can take several changes.

Known gaps: a wait on a permission prompt you approve without typing, with no stop after it, runs on until your next
input; start -> verified working capability waits for T14's integration check; model cost isn't recorded (see
docs/research/claude-code-cost-metrics.md); rework for a recent PR grows as later fixes merge.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tower.events import Event

ACTIVE_WINDOW = timedelta(minutes=2)
INPUTS = frozenset({"prompt", "answer"})


@dataclass(frozen=True)
class MergedPr:
    """A merged PR: its repo (main checkout path, as events record it), head branch, merge time, the ticket numbers
    it closes, and how many times those tickets were reopened after it merged."""

    repo: str
    number: int
    title: str
    branch: str
    merged_at: datetime
    closes: tuple[int, ...] = ()
    reopened_after_merge: int = 0


@dataclass(frozen=True)
class ChangeStats:
    """The metrics for one merged PR that has events (see the module docstring for each definition)."""

    pr: MergedPr
    started_at: datetime
    lead_time: timedelta
    active: timedelta
    waiting: timedelta
    interruptions: int
    resume_median: timedelta | None
    reports: int
    follow_ups: int
    rework: int

    @property
    def follow_ups_per_report(self) -> float | None:
        """Follow-up prompts per report, or None with no reports."""
        return self.follow_ups / self.reports if self.reports else None


@dataclass(frozen=True)
class StatsResult:
    """Every merged PR with events, by merge time; the merged PRs with none (made outside tower); and how many events
    were left out because their ts couldn't be read."""

    changes: list[ChangeStats] = field(default_factory=list)
    untracked: list[MergedPr] = field(default_factory=list)
    unreadable: int = 0


def compute(log: Sequence[Event], prs: Sequence[MergedPr], merged_since: datetime | None = None) -> StatsResult:
    """The metrics for each PR in prs merged at or after merged_since (default: all), from the events in log (any
    order). PRs without events go to untracked; events whose ts isn't an ISO 8601 time with a zone are counted in
    unreadable and left out. PRs merged before merged_since only bound a reused branch's events."""
    timed: list[tuple[datetime, Event]] = []
    unreadable = 0
    for e in log:
        when = parse_ts(e.ts)
        if when is None:
            unreadable += 1
        else:
            timed.append((when, e))
    timed.sort(key=lambda pair: pair[0])
    by_merge = sorted(prs, key=lambda p: p.merged_at)
    result = StatsResult(unreadable=unreadable)
    for index, merged in enumerate(by_merge):
        if merged_since is not None and merged.merged_at < merged_since:
            continue
        earlier = [p.merged_at for p in by_merge[:index] if (p.repo, p.branch) == (merged.repo, merged.branch)]
        previous_merge = max(earlier, default=None)
        change_events = [
            (when, e)
            for when, e in timed
            if (e.repo, e.branch) == (merged.repo, merged.branch)
            and (previous_merge is None or when > previous_merge)
            and when <= merged.merged_at
        ]
        if not change_events:
            result.untracked.append(merged)
            continue
        result.changes.append(change_stats(merged, change_events, rework(merged, by_merge[index + 1 :])))
    return result


def change_stats(merged: MergedPr, change_events: list[tuple[datetime, Event]], rework_count: int) -> ChangeStats:
    """The metrics for one PR from its (time, event) pairs, sorted by time."""
    started_at = change_events[0][0]
    waits, reports, follow_ups = turns(change_events)
    inputs = [when for when, e in change_events if e.type in INPUTS]
    return ChangeStats(
        pr=merged,
        started_at=started_at,
        lead_time=merged.merged_at - started_at,
        active=active_time(inputs),
        waiting=sum(waits, timedelta()),
        interruptions=len(waits),
        resume_median=timedelta(seconds=statistics.median(w.total_seconds() for w in waits)) if waits else None,
        reports=reports,
        follow_ups=follow_ups,
        rework=rework_count,
    )


def turns(change_events: list[tuple[datetime, Event]]) -> tuple[list[timedelta], int, int]:
    """(waits, reports, follow-ups) over the (time, event) pairs, walking each session on its own."""
    waits: list[timedelta] = []
    reports = follow_ups = 0
    sessions: dict[str | None, list[tuple[datetime, Event]]] = {}
    for when, e in change_events:
        sessions.setdefault(e.session_id, []).append((when, e))
    for session_events in sessions.values():
        first_needs_you: datetime | None = None
        last_stop: datetime | None = None
        reported = False  # the worker has stopped for you at least once in this session
        for when, e in session_events:
            if e.type == "needs-you" and first_needs_you is None:
                first_needs_you = when
            elif e.type == "stop":
                last_stop = when
                reported = True
            elif e.type in INPUTS:
                opened = last_stop or first_needs_you
                if opened is not None:
                    waits.append(when - opened)
                reports += last_stop is not None
                follow_ups += reported and e.type == "prompt"
                first_needs_you = last_stop = None
        if last_stop is not None:
            reports += 1  # the trailing report nobody answered in this session
    return waits, reports, follow_ups


def active_time(inputs: list[datetime]) -> timedelta:
    """The union of the ACTIVE_WINDOW before each input (inputs sorted)."""
    total = timedelta()
    covered_until: datetime | None = None
    for when in inputs:
        start = when - ACTIVE_WINDOW
        if covered_until is not None and start < covered_until:
            start = covered_until
        if when > start:
            total += when - start
        covered_until = max(when, covered_until) if covered_until else when
    return total


def rework(merged: MergedPr, later: Sequence[MergedPr]) -> int:
    """Later merged PRs in the same repo from the same branch, plus reopens of merged's tickets."""
    return sum(1 for p in later if (p.repo, p.branch) == (merged.repo, merged.branch)) + merged.reopened_after_merge


def parse_ts(ts: str) -> datetime | None:
    """An event's ts ("2026-11-06T04:12:33.120Z") as an aware datetime, or None if it isn't one."""
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def render(result: StatsResult) -> str:
    """result as a plain-text table, one row per change, then a line for untracked PRs if any."""
    if not result.changes:
        lines = ["No merged PR in the window has events."]
    else:
        lines = table(result.changes)
    if result.untracked:
        count = len(result.untracked)
        labels = ", ".join(pr_label(p) for p in result.untracked)
        lines += ["", f"{count} merged PR{'s' if count > 1 else ''} with no events (made outside tower): {labels}"]
    return "\n".join(lines)


def table(changes: list[ChangeStats]) -> list[str]:
    """The changes as aligned text rows under a header; the last column (the title) isn't padded."""
    header = ["PR", "start→merged", "active", "waiting", "interrupts", "resume", "follow-ups/report", "rework", "title"]
    rows = [header]
    for change in changes:
        rate = change.follow_ups_per_report
        rows.append(
            [
                pr_label(change.pr),
                duration(change.lead_time),
                duration(change.active),
                duration(change.waiting),
                str(change.interruptions),
                duration(change.resume_median) if change.resume_median is not None else "-",
                f"{rate:.1f}" if rate is not None else "-",
                str(change.rework),
                change.pr.title,
            ]
        )
    widths = [max(len(row[column]) for row in rows) for column in range(len(header) - 1)]
    return ["  ".join([*(cell.ljust(width) for cell, width in zip(row, widths)), row[-1]]) for row in rows]


def pr_label(merged: MergedPr) -> str:
    """A PR as "<repo folder>#<number>", e.g. "tower#44"."""
    return f"{merged.repo.rstrip('/').rsplit('/', 1)[-1]}#{merged.number}"


def duration(span: timedelta) -> str:
    """span rounded down to minutes: "9m", "3h05m", "2d04h10m"."""
    minutes = int(span.total_seconds() // 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d{hours:02d}h{minutes:02d}m"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"
