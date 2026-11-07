"""state.py: each worktree's effective state, the one status the hub and the pane both show.

A worktree's status used to be worked out three ways (the hub's bash, the pane's TypeScript, the WIP count), and they
disagreed. Here the facts are gathered once and one function, decide(), turns them into a status and the reason for
it:

    needs-you       a live session is waiting on you, or the card says needs-you and nothing contradicts it
    running         a session is busy, or the card says running
    waiting-ci      finished, waiting on CI, review or a merge (the card says so, or its PR is open and nobody works)
    blocked         the card says blocked (waiting on something other than you)
    done            the card says done
    ready-to-clean  nothing uncommitted, and the PR merged with no commits past its head, or every commit is on the
                    base branch: the worktree can go
    no-card         no card, or one whose status tower doesn't know
    main            the main checkout

The facts, in the order they win: live sessions (`claude agents --json`; when that isn't available, the event log's
recent events stand in), then the card, then git and, when asked, the PR. An execution slot (what the WIP limit
counts) is a worktree other than the main checkout whose effective state is running or needs-you, so a busy worker
with no card counts and a worker only waiting on CI doesn't.

Around the statuses, the report also carries the pairs of worktrees that changed the same files (overlaps.py), which
the hub lists under Housekeeping.

Every fact source degrades to "unknown" instead of failing: no `claude` CLI, no ledger, no `gh`. The note fields say
which ones were missing. `tower scan` (scan.py) prints this report inside the hub's scan, which the plugin runs through
plugin/scripts/run-tower.py under whatever `python3` it finds, so it stays 3.9-compatible, like the modules the hooks
import.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tower import events, ledger, policy, sessions
from tower.github import from_this_repo
from tower.gitrepo import COMMAND_TIMEOUT_SECONDS, git, list_worktrees, repo_name
from tower.overlaps import Overlap, find_overlaps
from tower.sessions import owning_worktree

STATUSES = ("needs-you", "running", "waiting-ci", "blocked", "done", "ready-to-clean", "no-card", "main")
CARD_STATUSES = ("running", "needs-you", "waiting-ci", "blocked", "done")
SLOT_STATUSES = ("running", "needs-you")
STALE_AFTER_SECONDS = 24 * 60 * 60  # a running card untouched this long is flagged STALE
EVENTS_WINDOW = timedelta(hours=24)  # older events don't stand in for a live session: it may have crashed
EVENTS_READ = 5000  # how many of the log's last lines to read when standing in for live sessions
LISTED_OVERLAP_FILES = 5  # a Housekeeping row names this many shared files, then says how many more
COMMIT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")  # a full SHA-1 or SHA-256 commit id, as gh prints headRefOid


@dataclass(frozen=True)
class Card:
    """The frontmatter fields of a worktree's status card that state uses. stale: running, untouched for over 24h."""

    status: str
    ticket: str
    updated: str
    pr: str
    stale: bool


@dataclass(frozen=True)
class Session:
    """One Claude session open in a worktree. activity is busy, waiting (on the user) or idle."""

    name: str
    kind: str
    activity: str
    waiting_for: str = ""


@dataclass(frozen=True)
class Pr:
    """The pull request for a worktree's branch. state is GitHub's: OPEN, MERGED or CLOSED. head_oid is the branch's
    last commit in the PR (for a merged PR, as it was when it merged); None when gh didn't say."""

    number: int
    state: str
    url: str
    head_oid: str | None = None


@dataclass(frozen=True)
class WorktreeFacts:
    """Everything known about one worktree. sessions_known is False when neither live sessions nor recent events
    could be read, so "no sessions" means "don't know". unshipped_commits, dirty and pr are None when unknown;
    dirty is also None when no rule needs it (only done cards and merged PRs are checked: `git status` is slow).
    card_unreadable is True when a card file exists but couldn't be read: card is None, but not because there's none.
    commits_after_merge counts HEAD's commits that a merged PR's head doesn't have (work the merge didn't take); None
    when the PR isn't merged or git couldn't compare (no head from gh, or that commit isn't in this clone).
    card_file is the card's file name in the Ledger (ledger.card_name), which readers use instead of rebuilding it."""

    label: str
    branch: str | None
    path: str
    card_file: str
    is_main: bool
    card: Card | None
    sessions: tuple[Session, ...]
    sessions_known: bool
    unshipped_commits: int | None
    dirty: bool | None
    pr: Pr | None
    card_unreadable: bool = False
    commits_after_merge: int | None = None


@dataclass(frozen=True)
class Verdict:
    """A worktree's effective state: one of STATUSES, why, and whether it holds an execution slot."""

    status: str
    reason: str
    holds_slot: bool


WAITING_CI_CARD = "the card says it is waiting on CI, review or a merge"


def decide(worktree: WorktreeFacts) -> Verdict:
    """The effective state of one worktree from its facts. Live sessions beat the card; the card beats git and PR."""
    card = worktree.card
    card_status = card.status if card else None
    waiting = [s for s in worktree.sessions if s.activity == "waiting"]
    busy = [s for s in worktree.sessions if s.activity == "busy"]

    if waiting:
        first = waiting[0]
        reason = f"session {first.name} is waiting on you" + (f" ({first.waiting_for})" if first.waiting_for else "")
        if card_status and card_status != "needs-you":
            reason += f"; the card says {card_status}"
        return verdict("needs-you", reason, worktree)
    if worktree.is_main:
        return verdict("main", "the main checkout", worktree)
    if busy and card_status == "waiting-ci":
        watching = f"session {busy[0].name} is busy (watching CI, or fixing it without updating the card)"
        return verdict("waiting-ci", f"{WAITING_CI_CARD}; {watching}", worktree)
    if busy:
        if card_status is None:
            return verdict("running", f"no card yet, but session {busy[0].name} is busy", worktree)
        if card_status == "needs-you" and not worktree.sessions_known:
            # Event-log "busy" is only the last prompt: asking a pop-up records nothing, so the card may be right.
            reason = f"the card says needs-you; the event log shows session {busy[0].name} active, but live sessions couldn't be read"
            return verdict("needs-you", reason, worktree)
        if card_status == "needs-you":
            reason = f"the card says needs-you but session {busy[0].name} is busy: you already answered"
            return verdict("running", reason, worktree)
        if card_status == "running":
            return verdict("running", f"session {busy[0].name} is busy", worktree)
        return verdict("running", f"the card says {card_status} but session {busy[0].name} is busy again", worktree)

    pr = worktree.pr
    # A merged PR settles a card that says the work is finished (or no card), never one that says work goes on: the
    # branch name may have been reused after an old PR merged.
    if pr and pr.state == "MERGED" and card_status not in ("running", "needs-you", "blocked"):
        # Squash merges leave none of the branch's commits on the base branch, so the PR's head is what shows the
        # worktree has no work the merge didn't take; every commit on the base branch shows it too.
        after_merge = worktree.commits_after_merge
        if worktree.dirty is False and (after_merge == 0 or worktree.unshipped_commits == 0):
            return verdict("ready-to-clean", f"PR #{pr.number} is merged", worktree)
        if worktree.dirty is not False:
            unsure = "has uncommitted changes" if worktree.dirty else "couldn't be checked for uncommitted changes"
        elif after_merge:
            unsure = f"has {plural(after_merge, 'commit')} the PR doesn't include"
        else:
            unsure = "couldn't be checked for commits the PR doesn't include"
        merged = f"PR #{pr.number} is merged but the worktree {unsure}"
        if card_status in CARD_STATUSES:
            return verdict(str(card_status), f"the card says {card_status}; {merged}", worktree)
        return verdict("no-card", merged, worktree)
    if card is None:
        return verdict("no-card", "no card yet", worktree)
    if card.status not in CARD_STATUSES:
        return verdict("no-card", f"the card's status '{card.status}' isn't one tower knows", worktree)

    nobody_working = worktree.sessions_known  # and no session is busy or waiting, or we'd have returned above
    if pr and pr.state == "OPEN" and nobody_working and card.status in ("running", "done"):
        reason = f"the card says {card.status} but no session is working and PR #{pr.number} is open"
        return verdict("waiting-ci", reason, worktree)
    if card.status == "done" and worktree.unshipped_commits == 0 and worktree.dirty is False:
        return verdict("ready-to-clean", "the card says done and every commit is on the base branch", worktree)
    if card.status == "running" and card.stale:
        return verdict("running", "the card says running, but it hasn't been updated in over 24h", worktree)
    if card.status == "waiting-ci":
        return verdict("waiting-ci", WAITING_CI_CARD, worktree)
    if card.status == "needs-you":
        return verdict(
            "needs-you", "the card says needs-you; no session here is waiting on you on this machine", worktree
        )
    return verdict(card.status, f"the card says {card.status}", worktree)


def verdict(status: str, reason: str, worktree: WorktreeFacts) -> Verdict:
    """A Verdict whose slot follows the rule: running or needs-you, never the main checkout (that's the hub's)."""
    return Verdict(status, reason, status in SLOT_STATUSES and not worktree.is_main)


# ---------- gathering the facts ----------


@dataclass(frozen=True)
class OrphanCard:
    """A card in the ledger whose worktree no longer exists (finished or deleted work)."""

    name: str
    status: str
    path: str


@dataclass(frozen=True)
class Report:
    """The effective state of one repo: every worktree with its verdict, plus what the hub reports around them.

    sessions_source is "live" (`claude agents --json`), "events" (the event log stood in) or "none". agents is
    `claude agents --json`'s list when it could be read, so the scan's LIVE SESSIONS section draws the same snapshot the
    statuses were decided from. overlaps are the pairs of worktrees that changed the same files (overlaps.py). notes
    says, in plain words, which facts were missing and why. policy is the approval policy for the repo (policy.py),
    which also sets wip_limit.
    """

    repo: str
    main_checkout: str
    ledger: str | None
    base: str | None
    wip_limit: int
    worktrees: list[tuple[WorktreeFacts, Verdict]]
    orphan_cards: list[OrphanCard]
    sessions_source: str
    notes: list[str] = field(default_factory=list)
    agents: list[object] | None = None
    overlaps: list[Overlap] = field(default_factory=list)
    policy: policy.RepoPolicy | None = None

    @property
    def slots(self) -> int:
        """How many execution slots are in use: what the WIP limit counts."""
        return sum(1 for _, v in self.worktrees if v.holds_slot)


def gather(
    repo_dir: Path,
    with_pr: bool = False,
    now: float | None = None,
    agents_read: tuple[list[object] | None, str] | None = None,
) -> Report:
    """Gather every fact about repo_dir's worktrees and decide each one's effective state.

    Raises NotARepo when repo_dir isn't inside a git repo. with_pr asks GitHub (`gh`) for each branch's PR. agents_read
    is sessions.read_agents()'s result (the list, or None and why) when the caller already has it (`tower state --all` reads it once for every repo);
    None reads it here.
    """
    now = time.time() if now is None else now
    worktrees = list_worktrees(repo_dir)
    main = worktrees[0]
    notes: list[str] = []
    try:
        conf = ledger.read_conf(ledger.conf_path())
    except ledger.LedgerError as error:
        notes.append(f"{error}, so its settings weren't used")
        conf = {}

    name = repo_name(repo_dir)
    ledger_folder, ledger_note = ledger.ledger_dir(name, conf)
    if ledger_note:
        notes.append(ledger_note)
    repo_policy, _ = policy.for_checkout(Path(main.path))
    if repo_policy.problems:
        notes.append(f"the policy file has {plural(len(repo_policy.problems), 'problem')}; `tower policy` lists them")

    paths = [os.path.realpath(w.path) for w in worktrees]
    listed, live_note = sessions.read_agents() if agents_read is None else agents_read
    live = None if listed is None else live_sessions(listed, paths)
    sessions_source = "live"
    if live is None:
        notes.append(live_note)
        try:
            live = sessions_from_events(events.default_dir(), paths, now)
        except OSError as error:
            notes.append(
                f"couldn't read the event log ({error.strerror or type(error).__name__}), so it didn't stand in"
            )
            live = {}
        sessions_source = "events" if any(live.values()) else "none"

    prs: dict[str, Pr] = {}
    if with_pr:
        found, pr_note = pull_requests(Path(main.path))
        if found is None:
            notes.append(pr_note)
        prs = found or {}

    base = base_ref(Path(main.path), main.branch)
    if base is None:
        notes.append(
            "no base branch (no origin/HEAD, and the main checkout is detached), so unshipped work wasn't counted"
        )
    seen_cards: set[str] = set()
    rows: list[tuple[WorktreeFacts, Verdict]] = []
    for index, wt in enumerate(worktrees):
        is_main = index == 0
        card = None
        card_unreadable = False
        pr = prs.get(wt.branch) if wt.branch else None
        card_file = ledger.card_name(wt.branch, wt.sha, Path(main.path))
        if ledger_folder is not None:
            seen_cards.add(card_file)  # the main checkout claims its name too, so a stray main.md isn't an orphan
            notes_before = len(notes)
            card = None if is_main else card_or_note(ledger_folder / card_file, now, notes)
            card_unreadable = len(notes) > notes_before  # card_or_note notes only a card it couldn't read
        # Only the ready-to-clean rules read dirty, and `git status` is the slow call here: skip it otherwise. Commits
        # past a merged PR's head only matter once nothing is uncommitted.
        dirty = is_dirty(Path(wt.path)) if not is_main and finished(card, pr) else None
        facts = WorktreeFacts(
            label=wt.label,
            branch=wt.branch,
            path=wt.path,
            card_file=card_file,
            is_main=is_main,
            card=card,
            sessions=tuple(live.get(os.path.realpath(wt.path), [])),
            sessions_known=sessions_source == "live",
            unshipped_commits=None
            if is_main or base is None
            else unshipped(Path(main.path), base, wt.branch or wt.sha),
            dirty=dirty,
            pr=pr,
            card_unreadable=card_unreadable,
            commits_after_merge=commits_after(Path(wt.path), pr.head_oid)
            if dirty is False and pr and pr.state == "MERGED"
            else None,
        )
        rows.append((facts, decide(facts)))

    overlaps, overlap_notes = find_overlaps(Path(main.path), worktrees[1:], base)
    notes += overlap_notes
    return Report(
        repo=name,
        main_checkout=main.path,
        ledger=str(ledger_folder) if ledger_folder is not None else None,
        base=base,
        wip_limit=repo_policy.wip_limit,
        worktrees=rows,
        orphan_cards=orphan_cards(ledger_folder, seen_cards, notes) if ledger_folder is not None else [],
        sessions_source=sessions_source,
        notes=notes,
        agents=listed,
        overlaps=overlaps,
        policy=repo_policy,
    )


def frontmatter_field(lines: list[str], key: str) -> str:
    """The value of the first `key:` line, surrounding double quotes removed; "" when absent."""
    for line in lines:
        if line.startswith(f"{key}:"):
            value = line[len(key) + 1 :].strip()
            return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value
    return ""


def read_card(path: Path, now: float) -> Card | None:
    """The card at path, or None if there is none. Fields come from the frontmatter block when there is one.

    Raises OSError when the card exists but can't be read (permissions, an unsynced vault file): that isn't "no card".
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return None
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), len(lines))
        lines = lines[1:end]
    status = frontmatter_field(lines, "status")
    return Card(
        status=status,
        ticket=frontmatter_field(lines, "ticket") or "none",
        updated=frontmatter_field(lines, "updated"),
        pr=frontmatter_field(lines, "pr") or "none",
        stale=status == "running" and now - mtime > STALE_AFTER_SECONDS,
    )


def card_or_note(path: Path, now: float, notes: list[str]) -> Card | None:
    """read_card, with an unreadable card noted in notes (and treated as missing) instead of failing the report."""
    try:
        return read_card(path, now)
    except OSError as error:
        notes.append(f"couldn't read the card {path} ({error.strerror or type(error).__name__}), so it was skipped")
        return None


def orphan_cards(ledger: Path, seen: set[str], notes: list[str]) -> list[OrphanCard]:
    """Cards in the ledger that no worktree claimed (the backlog file isn't a card). A ledger folder that doesn't exist
    yet has none; one that can't be listed is noted in notes."""
    try:
        names = sorted(p.name for p in ledger.iterdir() if p.suffix == ".md" and p.name != "_backlog.md")
    except FileNotFoundError:
        return []
    except OSError as error:
        notes.append(
            f"couldn't list the ledger {ledger} ({error.strerror or type(error).__name__}), so orphans weren't checked"
        )
        return []
    orphans = []
    for name in names:
        if name not in seen:
            card = card_or_note(ledger / name, 0, notes)
            orphans.append(OrphanCard(name.removesuffix(".md"), card.status if card else "", str(ledger / name)))
    return orphans


def finished(card: Card | None, pr: Pr | None) -> bool:
    """Whether decide() may call this worktree ready-to-clean, and so needs to know if it's dirty."""
    return (card is not None and card.status == "done") or (pr is not None and pr.state == "MERGED")


def live_sessions(listed: list[object], worktrees: list[str]) -> dict[str, list[Session]]:
    """`claude agents --json`'s list as Sessions per worktree path."""
    return {
        path: [live_session(entry) for entry in entries]
        for path, entries in sessions.by_worktree(listed, worktrees).items()
    }


def live_session(entry: dict[str, object]) -> Session:
    """One `claude agents --json` entry as a Session. Interactive sessions report `status`, background ones `state`;
    a background session `blocked` on a permission prompt is waiting on the user too."""
    reported = str(entry.get("status") or entry.get("state") or "")
    # Free text from Claude Code: keep it to one line with no "|", which separates fields in the scan's rows.
    waiting_for = " ".join(str(entry.get("waitingFor") or "").replace("|", "/").split())
    if reported in ("waiting", "blocked") or waiting_for:
        activity = "waiting"
    elif reported in ("busy", "running"):
        activity = "busy"
    else:
        activity = "idle"
    name = str(entry.get("name") or entry.get("id") or "?")
    return Session(name=name, kind=str(entry.get("kind") or "?"), activity=activity, waiting_for=waiting_for[:80])


# The event that last happened in a session -> what it's doing now. A Notification of these kinds means it's stopped
# on the user; idle_prompt only means it finished a while ago and sits at its prompt.
WAITING_NOTIFICATIONS = ("permission_prompt", "elicitation_dialog", "agent_needs_input")


def sessions_from_events(directory: Path, worktrees: list[str], now: float) -> dict[str, list[Session]]:
    """Sessions per worktree from the event log's recent events, for when `claude agents --json` can't be read.

    Each session's latest event in the last 24h says what it is doing: a prompt or an answer means busy, a needs-you
    notification means waiting, a stop or a start means idle, and a session-end means it's gone. Older events are
    ignored: a session that crashed never records its end. Known limit: approving a permission prompt records nothing,
    so a session can read as waiting until its turn ends; live sessions don't have that problem.
    """
    cutoff = datetime.fromtimestamp(now, timezone.utc) - EVENTS_WINDOW
    now_utc = datetime.fromtimestamp(now, timezone.utc)
    latest: dict[str, dict[str, object]] = {}
    for line in recent_event_lines(directory, cutoff, now_utc):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or not isinstance(event.get("session_id"), str):
            continue
        when = parse_ts(event.get("ts"))
        if when is None or when < cutoff:
            continue
        previous = latest.get(event["session_id"])
        if previous is None or str(event.get("ts")) >= str(previous.get("ts")):
            latest[event["session_id"]] = event
    found: dict[str, list[Session]] = {}
    for session_id, event in latest.items():
        place = event.get("worktree")
        owner = owning_worktree(os.path.realpath(place), worktrees) if isinstance(place, str) else None
        activity = event_activity(event)
        if owner and activity:
            found.setdefault(owner, []).append(Session(name=session_id[:8], kind="?", activity=activity))
    return found


def recent_event_lines(directory: Path, cutoff: datetime, now: datetime) -> list[str]:
    """The log's last lines, oldest first, starting in last month's file when cutoff falls in last month (just after
    midnight on the 1st, the current file holds only today). Rotated files are `events-YYYY-MM.jsonl`; when a month has
    several (`events-YYYY-MM.N.jsonl`, rare), only the first is read."""
    lines = events.tail(directory, EVENTS_READ)
    if cutoff.strftime("%Y-%m") < now.strftime("%Y-%m"):
        try:
            with open(directory / f"events-{cutoff:%Y-%m}.jsonl", encoding="utf-8", errors="replace") as handle:
                lines = [line.rstrip("\n") for line in deque(handle, maxlen=EVENTS_READ)] + lines
        except FileNotFoundError:
            pass  # no rotation yet: the current file still holds last month's events
    return lines


def event_activity(event: dict[str, object]) -> str | None:
    """What a session whose latest event is this one is doing: busy, waiting, idle, or None when it has ended."""
    kind = event.get("type")
    if kind in ("prompt", "answer"):
        return "busy"
    if kind == "needs-you":
        data = event.get("data")
        notification = data.get("notification_type") if isinstance(data, dict) else None
        return "waiting" if notification in WAITING_NOTIFICATIONS else "idle"
    if kind == "session-end":
        return None
    return "idle"


def parse_ts(ts: object) -> datetime | None:
    """An event's ts ("2026-11-06T04:12:33.120Z") as an aware datetime; None if it isn't one. 3.9's fromisoformat
    doesn't take "Z", hence the replace."""
    if not isinstance(ts, str):
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def base_ref(main_checkout: Path, main_branch: str | None) -> str | None:
    """What "shipped" means: origin's default branch (origin/HEAD) if known, else the main checkout's branch."""
    return git(main_checkout, "rev-parse", "--abbrev-ref", "origin/HEAD") or main_branch


def unshipped(repo_dir: Path, base: str, ref: str) -> int | None:
    """How many commits ref has that base doesn't; None if git can't say. Squash-merged work still counts."""
    out = git(repo_dir, "rev-list", "--count", f"{base}..{ref}")
    return int(out) if out is not None and out.isdigit() else None


def commits_after(worktree: Path, head_oid: str | None) -> int | None:
    """How many commits the worktree's HEAD has that head_oid doesn't; None if git can't say (no head_oid, or that
    commit isn't in this clone)."""
    if not head_oid:
        return None
    out = git(worktree, "rev-list", "--count", f"{head_oid}..HEAD")
    return int(out) if out is not None and out.isdigit() else None


def is_dirty(worktree: Path) -> bool | None:
    """True if the worktree has uncommitted or untracked changes; None if git can't say."""
    out = git(worktree, "status", "--porcelain")
    return None if out is None else out != ""


def pull_requests(repo_dir: Path) -> tuple[dict[str, Pr] | None, str]:
    """Each branch's PR from `gh pr list` (an open one if it has one, else its newest; never a fork's), or None and
    why not."""
    fields = "number,state,headRefName,url,headRefOid,isCrossRepository"
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--limit", "200", "--json", fields],
            cwd=repo_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return None, "gh not installed, so PRs weren't read"
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"gh pr list didn't run ({type(error).__name__}), so PRs weren't read"
    if proc.returncode != 0:
        why = (proc.stderr.strip().splitlines() or [f"exit {proc.returncode}"])[-1]
        return None, f"gh pr list failed ({why}), so PRs weren't read"
    try:
        listed = json.loads(proc.stdout)
    except ValueError:
        return None, "gh pr list printed something that isn't JSON"
    if not isinstance(listed, list):
        return None, "gh pr list printed something unexpected (expected a list)"
    found: dict[str, Pr] = {}
    for entry in listed:
        if not isinstance(entry, dict) or not isinstance(entry.get("number"), int) or not from_this_repo(entry):
            continue
        head = entry.get("headRefOid")
        pr = Pr(
            number=entry["number"],
            state=str(entry.get("state")),
            url=str(entry.get("url")),
            # Only a commit id goes on to git: never something it could read as an option.
            head_oid=head if isinstance(head, str) and COMMIT_ID.fullmatch(head) else None,
        )
        current = found.get(str(entry.get("headRefName")))
        if (
            current is None
            or (pr.state == "OPEN") > (current.state == "OPEN")
            or ((pr.state == "OPEN") == (current.state == "OPEN") and pr.number > current.number)
        ):
            found[str(entry.get("headRefName"))] = pr
    return found, ""


# ---------- output ----------


def to_json(report: Report) -> dict[str, object]:
    """The report as `tower state --json` prints it. Readers depend on these keys: add, don't rename or remove."""
    worktrees = []
    for facts, v in report.worktrees:
        card = facts.card
        worktrees.append(
            {
                "label": facts.label,
                "branch": facts.branch,
                "path": facts.path,
                "card_file": facts.card_file,
                "is_main": facts.is_main,
                "status": v.status,
                "reason": v.reason,
                "holds_slot": v.holds_slot,
                "card": None if card is None else {k: getattr(card, k) for k in ("status", "ticket", "updated", "pr")},
                "stale": bool(card and card.stale),
                "sessions": [asdict(s) for s in facts.sessions],
                "unshipped_commits": facts.unshipped_commits,
                "dirty": facts.dirty,
                "pr": None if facts.pr is None else asdict(facts.pr),
            }
        )
    return {
        "repo": report.repo,
        "main_checkout": report.main_checkout,
        "ledger": report.ledger,
        "base": report.base,
        "wip_limit": report.wip_limit,
        "slots": report.slots,
        "at_limit": report.slots >= report.wip_limit,
        "sessions_source": report.sessions_source,
        "notes": report.notes,
        "worktrees": worktrees,
        "unshipped": [
            {"label": f.label, "commits": f.unshipped_commits} for f, _ in report.worktrees if f.unshipped_commits
        ],
        "orphan_cards": [asdict(o) for o in report.orphan_cards],
        "overlaps": [{"worktrees": [o.first, o.second], "files": list(o.files)} for o in report.overlaps],
        "housekeeping": [{"label": label, "note": note} for label, note in cleanup(report)],
        "policy": None if report.policy is None else policy.to_json(report.policy),
    }


# The hub's report groups, in report order, and the effective statuses in each. ready-to-clean and no-card are
# Housekeeping, with the leftovers below.
GROUPS = (
    ("NEEDS YOU", ("needs-you",)),
    ("BLOCKED", ("blocked",)),
    ("RUNNING", ("running",)),
    ("WAITING ON CI OR REVIEW", ("waiting-ci",)),
    ("DONE", ("done",)),
)


def render_text(report: Report) -> str:
    """The report as `tower state` prints it: the WORKTREES, ORPHAN CARDS and GROUPS sections, one row per line."""
    return "\n".join([*worktrees_section(report), "", *orphans_section(report), "", *groups_section(report)]) + "\n"


def worktrees_section(report: Report) -> list[str]:
    """The WORKTREES section: one row per worktree, then the WIP count, unshipped work and the notes.

    The hub reads these rows (`status:` is the effective state; the pane reads to_json() instead), so keep the
    `field: value | ...` shape and the path last. Every value from a card or a session goes through cell(), so none can
    contain " | ".
    """
    out = ["== WORKTREES =="]
    for facts, v in report.worktrees:
        if facts.is_main:
            out.append(
                f"- {cell(facts.label)} | status: {v.status} | main checkout (no card expected)"
                f" | reason: {cell(v.reason)} | {facts.path}"
            )
            continue
        card = facts.card
        fields = [
            f"- {cell(facts.label)}",
            f"status: {v.status}",
            f"card: {cell(card.status) if card else 'none'}",
            f"ticket: {cell(card.ticket) if card else 'none'}",
            f"updated: {cell(card.updated if card else '') or '?'}",
        ]
        if card and card.stale:
            fields.append("STALE: not updated in over 24h")
        # not through cell(): it would turn a branch's "|" into "/" and name a file that isn't there, and a git ref
        # can't hold the spaces of " | "
        fields.append(f"card_file: {facts.card_file}")
        out.append(" | ".join([*fields, f"reason: {cell(v.reason)}", facts.path]))
    limit = " — AT LIMIT" if report.slots >= report.wip_limit else ""
    out.append(
        f"WIP: {report.slots} of {report.wip_limit} (execution slots: running + needs-you, main excluded){limit}"
    )
    unshipped_rows = [f for f, _ in report.worktrees if f.unshipped_commits]
    if unshipped_rows:
        listed = ", ".join(f"{cell(f.label)} ({plural(f.unshipped_commits or 0, 'commit')})" for f in unshipped_rows)
        out.append(f"UNSHIPPED: {listed} not on {report.base}")
    else:
        out.append(f"UNSHIPPED: (none){f' — every branch is on {report.base}' if report.base else ''}")
    out.extend(f"NOTE: {note}" for note in report.notes)
    return out


def orphans_section(report: Report) -> list[str]:
    """The ORPHAN CARDS section: cards whose worktree is gone."""
    rows = [f"- {cell(o.name)} | status: {cell(o.status) or '?'} | {o.path}" for o in report.orphan_cards]
    return ["== ORPHAN CARDS (worktree gone) ==", *(rows or ["(none)"])]


def groups_section(report: Report) -> list[str]:
    """The GROUPS section: every worktree in the hub's report order, so the hub never regroups."""
    out = ["== GROUPS (report in this order) =="]
    if report.sessions_source == "events":
        out.append("(no live session data: sessions come from the event log's last 24h)")
    elif report.sessions_source == "none":
        out.append("(no live session data: grouped from cards alone)")
    for title, statuses in GROUPS:
        out.append(title)
        rows = [group_row(f, v) for f, v in report.worktrees if v.status in statuses]
        out += rows or ["(none)"]
    out.append("HOUSEKEEPING")
    out += housekeeping(report) or ["(none)"]
    return out


def group_row(facts: WorktreeFacts, v: Verdict) -> str:
    """One worktree's line under its group: label, ticket, PR if known, and why it's in that group."""
    if facts.is_main:
        return f"- {cell(facts.label)} (main checkout) | {cell(v.reason)}"
    ticket = cell(facts.card.ticket) if facts.card else "none"
    pr = f" | PR #{facts.pr.number} {facts.pr.state.lower()}" if facts.pr else ""
    return f"- {cell(facts.label)} | ticket: {ticket}{pr} | {cell(v.reason)}"


def housekeeping(report: Report) -> list[str]:
    """The Housekeeping rows of the GROUPS section, one per cleanup item."""
    return [f"- {cell(label)} | {cell(note)}" for label, note in cleanup(report)]


def cleanup(report: Report) -> list[tuple[str, str]]:
    """Cleanup the hub suggests, as (label, note): worktrees to remove, missing or stale cards, orphans, shared
    worktrees, worktrees changing the same files. The hub's Housekeeping group and the pane's both list these."""
    items: list[tuple[str, str]] = []
    for facts, v in report.worktrees:
        if v.status == "ready-to-clean":
            items.append((facts.label, f"ready to clean up: {v.reason}"))
        if not facts.is_main and facts.card is None:
            items.append((facts.label, "no card"))
        elif facts.card and facts.card.status not in CARD_STATUSES:
            items.append((facts.label, f"unknown card status: {facts.card.status or '?'}"))
        if facts.card and facts.card.stale:
            items.append((facts.label, "STALE: running card not updated in over 24h"))
        interactive = sum(1 for s in facts.sessions if s.kind == "interactive")
        if interactive > 1:
            where = " (in the main checkout, one is the hub)" if facts.is_main else ""
            items.append((facts.label, f"{interactive} interactive sessions share this worktree{where}"))
    items += [(o.name, f"orphan card (worktree gone), status: {o.status or '?'}") for o in report.orphan_cards]
    items += [(o.first, overlap_note(o)) for o in report.overlaps]
    return items


def overlap_note(overlap: Overlap) -> str:
    """The Housekeeping note for two worktrees changing the same files: the first few files, then how many more."""
    listed = ", ".join(overlap.files[:LISTED_OVERLAP_FILES])
    more = len(overlap.files) - LISTED_OVERLAP_FILES
    files = "file" if len(overlap.files) == 1 else "files"
    return f"changes the same {files} as {overlap.second}: {listed}{f' and {more} more' if more > 0 else ''}"


def cell(text: str) -> str:
    """text made safe for one field of a report row: on one line, with "|" (the field separator) turned into "/"."""
    return " ".join(text.replace("|", "/").split())


def plural(count: int, noun: str) -> str:
    """ "1 commit", "3 commits"."""
    return f"{count} {noun}{'' if count == 1 else 's'}"
