"""Tests for the rules in src/tower/state.py: from one worktree's facts to its effective state.

Each rule from #24 (T06) has its own test. The facts are built by hand here; tests/test_state.py gathers them from a
real repo.
"""

from __future__ import annotations

from tower import state
from tower.state import Card, Pr, Session, WorktreeFacts


def facts(
    card: Card | None = None,
    sessions: tuple[Session, ...] = (),
    is_main: bool = False,
    sessions_known: bool = True,
    unshipped_commits: int | None = 2,
    dirty: bool | None = False,
    pr: Pr | None = None,
    commits_after_merge: int | None = 0,
) -> WorktreeFacts:
    """A feature worktree's facts; each test changes only what its rule is about."""
    return WorktreeFacts(
        label="main" if is_main else "feat-42",
        branch="main" if is_main else "feat-42",
        path="/code/repo" if is_main else "/code/repo/worktrees/feat-42",
        is_main=is_main,
        card=card,
        sessions=sessions,
        sessions_known=sessions_known,
        unshipped_commits=unshipped_commits,
        dirty=dirty,
        pr=pr,
        commits_after_merge=commits_after_merge,
    )


def card(status: str, stale: bool = False) -> Card:
    """A card for ticket #42 with the given status."""
    return Card(status=status, ticket="#42", updated="2026-11-06T09:00", pr="none", stale=stale)


def session(activity: str, waiting_for: str = "") -> Session:
    """The worktree's one interactive session, doing activity."""
    return Session(name="feat-42-a1", kind="interactive", activity=activity, waiting_for=waiting_for)


BUSY = session("busy")
WAITING = session("waiting", "approve Bash")
IDLE = session("idle")


def test_a_waiting_session_is_needs_you_whatever_the_card_says() -> None:
    for status in ("running", "blocked", "done", "waiting-ci"):
        verdict = state.decide(facts(card(status), (WAITING,)))
        assert (verdict.status, verdict.holds_slot) == ("needs-you", True), status
        assert verdict.reason == f"session feat-42-a1 is waiting on you (approve Bash); the card says {status}"
    assert state.decide(facts(None, (WAITING,))).status == "needs-you"


def test_a_busy_session_beats_a_needs_you_card() -> None:
    verdict = state.decide(facts(card("needs-you"), (BUSY,)))
    assert (verdict.status, verdict.holds_slot) == ("running", True)
    assert verdict.reason == "the card says needs-you but session feat-42-a1 is busy: you already answered"


def test_a_busy_worker_with_no_card_holds_a_slot() -> None:
    verdict = state.decide(facts(None, (BUSY,)))
    assert (verdict.status, verdict.holds_slot) == ("running", True)
    assert verdict.reason == "no card yet, but session feat-42-a1 is busy"


def test_running_and_needs_you_cards_hold_a_slot_without_a_session() -> None:
    assert state.decide(facts(card("running"))).holds_slot
    verdict = state.decide(facts(card("needs-you")))
    assert (verdict.status, verdict.holds_slot) == ("needs-you", True)
    assert verdict.reason == "the card says needs-you; no session here is waiting on you on this machine"


def test_waiting_on_ci_holds_no_slot_even_while_its_session_watches_ci() -> None:
    assert state.decide(facts(card("waiting-ci"))) == state.Verdict(
        "waiting-ci", "the card says it is waiting on CI, review or a merge", False
    )
    verdict = state.decide(facts(card("waiting-ci"), (BUSY,)))
    assert (verdict.status, verdict.holds_slot) == ("waiting-ci", False)
    assert verdict.reason == (
        "the card says it is waiting on CI, review or a merge; session feat-42-a1 is busy"
        " (watching CI, or fixing it without updating the card)"
    )


def test_an_open_pr_with_no_session_working_is_waiting_ci() -> None:
    open_pr = Pr(number=57, state="OPEN", url="https://github.com/o/r/pull/57")
    verdict = state.decide(facts(card("running"), (IDLE,), pr=open_pr))
    assert (verdict.status, verdict.holds_slot) == ("waiting-ci", False)
    assert verdict.reason == "the card says running but no session is working and PR #57 is open"
    assert state.decide(facts(card("done"), pr=open_pr)).status == "waiting-ci"
    # Without live session data tower can't tell that nobody is working, so the card stands.
    assert state.decide(facts(card("running"), pr=open_pr, sessions_known=False)).status == "running"


def test_done_work_that_is_shipped_and_clean_is_ready_to_clean() -> None:
    merged = Pr(number=57, state="MERGED", url="https://github.com/o/r/pull/57")
    assert state.decide(facts(card("done"), pr=merged)) == state.Verdict("ready-to-clean", "PR #57 is merged", False)
    assert state.decide(facts(None, pr=merged)).status == "ready-to-clean"
    shipped = state.decide(facts(card("done"), unshipped_commits=0))
    assert shipped == state.Verdict(
        "ready-to-clean", "the card says done and every commit is on the base branch", False
    )


def test_uncommitted_changes_are_never_ready_to_clean() -> None:
    merged = Pr(number=57, state="MERGED", url="https://github.com/o/r/pull/57")
    verdict = state.decide(facts(card("done"), pr=merged, dirty=True))
    assert verdict.status == "done"
    assert verdict.reason == "the card says done; PR #57 is merged but the worktree has uncommitted changes"
    assert state.decide(facts(card("done"), unshipped_commits=0, dirty=True)).status == "done"


def test_commits_the_merged_pr_doesnt_include_are_never_ready_to_clean() -> None:
    """Commits made after the PR's head merged would be lost with the worktree. A squash merge puts none of the
    branch's commits on the base branch, so only the PR's own head can tell."""
    merged = Pr(number=57, state="MERGED", url="https://github.com/o/r/pull/57")
    verdict = state.decide(facts(card("done"), pr=merged, commits_after_merge=2))
    assert verdict.status == "done"
    assert (
        verdict.reason == "the card says done; PR #57 is merged but the worktree has 2 commits the PR doesn't include"
    )
    assert state.decide(facts(None, pr=merged, commits_after_merge=1)) == state.Verdict(
        "no-card", "PR #57 is merged but the worktree has 1 commit the PR doesn't include", False
    )


def test_a_merged_pr_whose_head_couldnt_be_compared_is_not_ready_to_clean() -> None:
    merged = Pr(number=57, state="MERGED", url="https://github.com/o/r/pull/57")
    verdict = state.decide(facts(card("done"), pr=merged, commits_after_merge=None))
    assert verdict.status == "done"
    assert verdict.reason == (
        "the card says done; PR #57 is merged but the worktree couldn't be checked for commits the PR doesn't include"
    )


def test_the_main_checkout_is_main_and_never_holds_a_slot() -> None:
    assert state.decide(facts(is_main=True, sessions=(BUSY,))) == state.Verdict("main", "the main checkout", False)
    waiting = state.decide(facts(is_main=True, sessions=(WAITING,)))
    assert (waiting.status, waiting.holds_slot) == ("needs-you", False)


def test_cards_alone_map_to_their_own_status() -> None:
    assert state.decide(facts(card("blocked"))) == state.Verdict("blocked", "the card says blocked", False)
    assert state.decide(facts(card("done"))) == state.Verdict("done", "the card says done", False)
    assert state.decide(facts(None, (IDLE,))) == state.Verdict("no-card", "no card yet", False)


def test_a_stale_running_card_still_holds_its_slot_and_says_so() -> None:
    verdict = state.decide(facts(card("running", stale=True)))
    assert (verdict.status, verdict.holds_slot) == ("running", True)
    assert verdict.reason == "the card says running, but it hasn't been updated in over 24h"


def test_a_card_status_tower_doesnt_know_is_treated_as_no_card() -> None:
    verdict = state.decide(facts(card("finished")))
    assert verdict == state.Verdict("no-card", "the card's status 'finished' isn't one tower knows", False)


def test_a_busy_session_beats_a_blocked_or_done_card() -> None:
    verdict = state.decide(facts(card("done"), (BUSY,)))
    assert (verdict.status, verdict.holds_slot) == ("running", True)
    assert verdict.reason == "the card says done but session feat-42-a1 is busy again"


def test_a_merged_pr_never_overrides_a_running_or_needs_you_card() -> None:
    """A branch name reused after an old merged PR, with new work: the card wins, not the stale PR."""
    merged = Pr(number=57, state="MERGED", url="https://github.com/o/r/pull/57")
    for status in ("running", "needs-you"):
        verdict = state.decide(facts(card(status), pr=merged))
        assert (verdict.status, verdict.holds_slot) == (status, True), status
    assert state.decide(facts(card("waiting-ci"), pr=merged)).status == "ready-to-clean"


def test_only_a_live_busy_session_beats_a_needs_you_card() -> None:
    """From the event log, "busy" only means the turn's prompt was the last event: the session may be stopped on a
    pop-up (asking records nothing) or have crashed, so the card's needs-you stands."""
    verdict = state.decide(facts(card("needs-you"), (BUSY,), sessions_known=False))
    assert (verdict.status, verdict.holds_slot) == ("needs-you", True)
    assert verdict.reason == (
        "the card says needs-you; the event log shows session feat-42-a1 active, but live sessions couldn't be read"
    )
