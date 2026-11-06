"""Tests for src/tower/stats.py: metrics per merged PR, computed over a fixture event log with hand-worked numbers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tower import stats
from tower.events import Event

REPO = "/code/app"


def at(clock: str, day: int = 5) -> str:
    """An event ts on 2026-11-<day> at clock ("HH:MM" or "HH:MM:SS"), UTC."""
    if clock.count(":") == 1:
        clock += ":00"
    return f"2026-11-{day:02d}T{clock}.000Z"


def event(
    clock: str, event_type: str, branch: str = "feat-7", session: str = "s1", repo: str = REPO, **data: object
) -> Event:
    """One event in repo on branch; day defaults to 2026-11-05."""
    return Event(at(clock), event_type, session, repo, f"{repo}/worktrees/{branch}", branch, dict(data))


def pr(
    number: int, branch: str, merged: str, closes: tuple[int, ...] = (), reopened: int = 0, day: int = 5
) -> stats.MergedPr:
    """A PR in REPO merged at clock on 2026-11-<day>."""
    merged_at = datetime.fromisoformat(at(merged, day))
    return stats.MergedPr(REPO, number, f"PR {number}", branch, merged_at, closes, reopened)


# One worker on feat-7, worked by hand:
#   waits      09:25:30 stop   .. 09:40 prompt = 14m30s (the last stop, not the permission needs-you before it)
#              10:00 stop      .. 11:00 prompt = 60m    (the stop, not the idle needs-you after it)
#              11:30 needs-you .. 11:32 answer =  2m    (no stop) -> 76m30s waiting, median 14m30s, 3 interruptions
#   reports    stops before 09:40 and 11:00, plus the trailing 11:50 stop = 3
#   follow-ups the prompts at 09:40, 11:00 and 11:01 (all after the first report) = 3; 11:32 is an answer
#   active     2 min before each input, overlaps merged: 09:00:05 2m, 09:40 2m, 11:00+11:01 3m, 11:32 2m = 9m
#   lead time  09:00 first event .. 12:00 merge = 3h
FEATURE_7 = [
    event("09:00", "session-start", source="startup"),
    event("09:00:05", "prompt", prompt_chars=40),
    event("09:20", "needs-you", notification_type="permission_prompt"),
    event("09:25", "stop", stop_hook_active=False),
    event("09:25:30", "stop", stop_hook_active=True),
    event("09:40", "prompt", prompt_chars=12),
    event("10:00", "stop", stop_hook_active=False),
    event("10:30", "needs-you", notification_type="idle_prompt"),
    event("11:00", "prompt", prompt_chars=8),
    event("11:01", "prompt", prompt_chars=8),
    event("11:30", "needs-you", notification_type="elicitation_dialog"),
    event("11:32", "answer"),
    event("11:50", "stop", stop_hook_active=False),
]
NOISE = [
    event("08:00", "prompt", branch="other"),  # another branch
    event("08:00", "prompt", repo="/code/elsewhere"),  # same branch name, another repo
    event("12:30", "prompt"),  # on feat-7 after its merge: belongs to no merged PR
]


def test_a_merged_pr_gets_its_numbers_from_its_branchs_events() -> None:
    result = stats.compute(FEATURE_7 + NOISE, [pr(7, "feat-7", "12:00")])

    [change] = result.changes
    assert change.pr.number == 7
    assert change.started_at == datetime.fromisoformat(at("09:00"))
    assert change.lead_time == timedelta(hours=3)
    assert change.active == timedelta(minutes=9)
    assert change.waiting == timedelta(minutes=76, seconds=30)
    assert change.interruptions == 3
    assert change.resume_median == timedelta(minutes=14, seconds=30)
    assert change.reports == 3
    assert change.follow_ups == 3
    assert change.rework == 0
    assert result.untracked == []


def test_rework_counts_later_prs_from_the_same_branch_and_reopened_tickets() -> None:
    fix = [event("08:00", "prompt", branch="fix-7", day=6), event("08:30", "stop", branch="fix-7", day=6)]
    prs = [
        pr(7, "feat-7", "12:00", closes=(7,), reopened=1),
        pr(9, "fix-7", "09:00", closes=(7,), day=6),  # later, same ticket, another branch: planned work, not rework
        pr(10, "feat-7", "10:00", day=6),  # later, same branch reused
        pr(11, "docs", "11:00", closes=(3,), day=6),  # later, unrelated, and no events at all
    ]

    result = stats.compute(FEATURE_7 + fix, prs)

    by_number = {change.pr.number: change for change in result.changes}
    assert by_number[7].rework == 2  # #10 and one reopen
    assert by_number[9].rework == 0
    assert [p.number for p in result.untracked] == [10, 11]


def test_a_reused_branch_splits_its_events_at_each_merge() -> None:
    log = [
        event("09:00", "prompt"),
        event("10:00", "stop"),
        event("13:00", "prompt"),  # after #7 merged: the start of #8 on the same branch
        event("13:30", "stop"),
    ]

    result = stats.compute(log, [pr(8, "feat-7", "14:00"), pr(7, "feat-7", "12:00")])

    assert [(c.pr.number, c.started_at, c.lead_time) for c in result.changes] == [
        (7, datetime(2026, 11, 5, 9, tzinfo=timezone.utc), timedelta(hours=3)),
        (8, datetime(2026, 11, 5, 13, tzinfo=timezone.utc), timedelta(hours=1)),
    ]


def test_a_change_with_no_waits_or_reports_has_no_median_and_no_follow_up_rate() -> None:
    result = stats.compute([event("09:00", "prompt")], [pr(7, "feat-7", "09:30")])

    [change] = result.changes
    assert (change.interruptions, change.resume_median, change.reports, change.follow_ups_per_report) == (
        0,
        None,
        0,
        None,
    )


def test_events_with_an_unreadable_ts_are_counted_and_left_out() -> None:
    log = [
        Event("yesterday", "prompt", "s1", REPO, None, "feat-7"),
        Event("2026-11-05T09:00:00", "prompt", "s1", REPO, None, "feat-7"),  # no zone
        event("09:10", "prompt"),
    ]

    result = stats.compute(log, [pr(7, "feat-7", "09:30")])

    assert result.unreadable == 2
    assert result.changes[0].started_at == datetime(2026, 11, 5, 9, 10, tzinfo=timezone.utc)


def test_render_says_so_when_no_merged_pr_has_events() -> None:
    result = stats.compute([], [pr(8, "typo", "09:00")])

    assert stats.render(result) == (
        "No merged PR in the window has events.\n\n1 merged PR with no events (made outside tower): app#8"
    )


def test_a_pr_merged_before_the_window_is_not_listed_but_still_bounds_its_branch() -> None:
    log = [event("09:00", "prompt"), event("13:00", "prompt"), event("13:30", "stop")]
    since = datetime(2026, 11, 5, 12, 30, tzinfo=timezone.utc)

    result = stats.compute(log, [pr(7, "feat-7", "12:00"), pr(8, "feat-7", "14:00")], merged_since=since)

    assert [(c.pr.number, c.lead_time) for c in result.changes] == [(8, timedelta(hours=1))]
    assert result.untracked == []
