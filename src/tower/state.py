"""state.py: each worktree's effective state, the one status the hub and the pane both show.

A worktree's status used to be worked out three ways (the hub's bash, the pane's TypeScript, the WIP count), and they
disagreed. Here the facts are gathered once and one function, decide(), turns them into a status and the reason for
it:

    needs-you       a live session is waiting on you, or the card says needs-you and nothing contradicts it
    running         a session is busy, or the card says running
    waiting-ci      finished, waiting on CI, review or a merge (the card says so, or its PR is open and nobody works)
    blocked         the card says blocked (waiting on something other than you)
    done            the card says done
    ready-to-clean  merged (or every commit on the base branch) with nothing uncommitted: the worktree can go
    no-card         no card, or one whose status tower doesn't know
    main            the main checkout

The facts, in the order they win: live sessions (`claude agents --json`; when that isn't available, the event log's
recent events stand in), then the card, then git and, when asked, the PR. An execution slot (what the WIP limit
counts) is a worktree other than the main checkout whose effective state is running or needs-you, so a busy worker
with no card counts and a worker only waiting on CI doesn't.

Every fact source degrades to "unknown" instead of failing: no `claude` CLI, no ledger, no `gh`. The note fields say
which ones were missing. hub-scan.sh runs this module through plugin/scripts/run-tower.py under whatever `python3` it
finds, so it stays 3.9-compatible, like the modules the hooks import.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tower import events

STATUSES = ("needs-you", "running", "waiting-ci", "blocked", "done", "ready-to-clean", "no-card", "main")
CARD_STATUSES = ("running", "needs-you", "waiting-ci", "blocked", "done")
SLOT_STATUSES = ("running", "needs-you")
STALE_AFTER_SECONDS = 24 * 60 * 60  # a running card untouched this long is flagged STALE (same as hub-scan.sh did)
EVENTS_WINDOW = timedelta(hours=24)  # older events don't stand in for a live session: it may have crashed
EVENTS_READ = 5000  # how many of the log's last lines to read when standing in for live sessions
COMMAND_TIMEOUT_SECONDS = 15


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
    """The pull request for a worktree's branch. state is GitHub's: OPEN, MERGED or CLOSED."""

    number: int
    state: str
    url: str


@dataclass(frozen=True)
class WorktreeFacts:
    """Everything known about one worktree. sessions_known is False when neither live sessions nor recent events
    could be read, so "no sessions" means "don't know". unshipped_commits, dirty and pr are None when unknown."""

    label: str
    branch: str | None
    path: str
    is_main: bool
    card: Card | None
    sessions: tuple[Session, ...]
    sessions_known: bool
    unshipped_commits: int | None
    dirty: bool | None
    pr: Pr | None


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
        if worktree.dirty is False:
            return verdict("ready-to-clean", f"PR #{pr.number} is merged", worktree)
        unsure = "has uncommitted changes" if worktree.dirty else "couldn't be checked for uncommitted changes"
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

    sessions_source is "live" (`claude agents --json`), "events" (the event log stood in) or "none". notes says, in
    plain words, which facts were missing and why.
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

    @property
    def slots(self) -> int:
        """How many execution slots are in use: what the WIP limit counts."""
        return sum(1 for _, v in self.worktrees if v.holds_slot)


@dataclass(frozen=True)
class GitWorktree:
    """One block of `git worktree list --porcelain`. branch is None on a detached HEAD."""

    path: str
    sha: str
    branch: str | None


def gather(repo_dir: Path, with_pr: bool = False, now: float | None = None) -> Report:
    """Gather every fact about repo_dir's worktrees and decide each one's effective state.

    Raises NotARepo when repo_dir isn't inside a git repo. with_pr asks GitHub (`gh`) for each branch's PR.
    """
    now = time.time() if now is None else now
    worktrees = list_worktrees(repo_dir)
    main = worktrees[0]
    conf = read_conf(conf_path())
    notes: list[str] = []

    name = repo_name(repo_dir)
    ledger, ledger_note = ledger_dir(name, conf)
    if ledger_note:
        notes.append(ledger_note)

    paths = [os.path.realpath(w.path) for w in worktrees]
    live, live_note = live_sessions(paths)
    sessions_source = "live"
    if live is None:
        notes.append(live_note)
        live = sessions_from_events(events.default_dir(), paths, now)
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
        if ledger is not None:
            card_file = card_name(wt.branch, wt.sha, Path(main.path))
            seen_cards.add(card_file)  # the main checkout claims its name too, so a stray main.md isn't an orphan
            card = None if is_main else card_or_note(ledger / card_file, now, notes)
        facts = WorktreeFacts(
            label=wt.branch or f"(detached {wt.sha[:7]})",
            branch=wt.branch,
            path=wt.path,
            is_main=is_main,
            card=card,
            sessions=tuple(live.get(os.path.realpath(wt.path), [])),
            sessions_known=sessions_source == "live",
            unshipped_commits=None
            if is_main or base is None
            else unshipped(Path(main.path), base, wt.branch or wt.sha),
            dirty=None if is_main else is_dirty(Path(wt.path)),
            pr=prs.get(wt.branch) if wt.branch else None,
        )
        rows.append((facts, decide(facts)))

    return Report(
        repo=name,
        main_checkout=main.path,
        ledger=str(ledger) if ledger is not None else None,
        base=base,
        wip_limit=wip_limit(conf),
        worktrees=rows,
        orphan_cards=orphan_cards(ledger, seen_cards, notes) if ledger is not None else [],
        sessions_source=sessions_source,
        notes=notes,
    )


class NotARepo(Exception):
    """The folder given to gather() isn't inside a git repo."""


def git(cwd: Path, *args: str) -> str | None:
    """Run one git command in cwd; its stdout with the trailing newline removed, or None if it failed."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args], check=False, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.rstrip("\n") if proc.returncode == 0 else None


def list_worktrees(repo_dir: Path) -> list[GitWorktree]:
    """Every worktree of the repo holding repo_dir, main checkout first (git lists it first)."""
    out = git(repo_dir, "worktree", "list", "--porcelain")
    if out is None:
        raise NotARepo(f"not a git repo: {repo_dir}")
    found: list[GitWorktree] = []
    path, sha, branch = "", "", None
    for line in (out + "\n\n").split("\n"):
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line.startswith("HEAD "):
            sha = line[len("HEAD ") :]
        elif line.startswith("branch "):
            branch = line[len("branch ") :].replace("refs/heads/", "", 1)
        elif line == "" and path:
            found.append(GitWorktree(path, sha, branch))
            path, sha, branch = "", "", None
    return found


def conf_path() -> Path:
    """tower's per-machine config: $TOWER_CONF if set, else ~/.claude/tower.conf (same as ledger-lib.sh)."""
    return Path(os.environ.get("TOWER_CONF") or Path.home() / ".claude" / "tower.conf")


def read_conf(path: Path) -> dict[str, str]:
    """The config's `key=value` lines as a dict; the last line for a key wins, as in ledger-lib.sh."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    conf: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key and not key.startswith("#"):
            conf[key] = value
    return conf


def wip_limit(conf: dict[str, str]) -> int:
    """wip_limit from the config; 3 (setup.sh's default) when absent or not a whole number."""
    value = conf.get("wip_limit", "")
    return int(value) if value.isdigit() else 3


def repo_name(repo_dir: Path) -> str:
    """The repo's name, the same from every worktree: ledger-lib.sh's ledger_repo_name."""
    common = git(repo_dir, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common is None:
        raise NotARepo(f"not a git repo: {repo_dir}")
    if os.path.basename(common) == ".git":
        return os.path.basename(os.path.dirname(common))
    name = os.path.basename(common)
    return name.removesuffix(".git")


def ledger_dir(repo: str, conf: dict[str, str]) -> tuple[Path | None, str]:
    """The repo's Ledger folder (ledger-lib.sh's ledger_project_dir + /Ledger), or None and why it can't be found."""
    projects_dir = conf.get("projects_dir", "")
    if not projects_dir:
        return None, f"no projects_dir in {conf_path()}, so no cards were read (run setup.sh on this machine)"
    if not os.path.isdir(projects_dir):
        return None, f"projects_dir does not exist: {projects_dir}, so no cards were read"
    return Path(projects_dir) / conf.get(f"project.{repo}", repo) / "Ledger", ""


def card_name(branch: str | None, sha: str, repo_dir: Path) -> str:
    """A worktree's card file name: ledger-lib.sh's ledger_card_name ("/" -> "__", detached -> detached-<sha>)."""
    if not branch:
        branch = "detached-" + (git(repo_dir, "rev-parse", "--short", sha) or sha[:7])
    return branch.replace("/", "__") + ".md"


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


def owning_worktree(cwd: str, worktrees: list[str]) -> str | None:
    """The worktree path containing cwd, the longest match (worktrees can sit inside the main checkout)."""
    best = None
    for path in worktrees:
        if (cwd == path or cwd.startswith(path + os.sep)) and (best is None or len(path) > len(best)):
            best = path
    return best


def live_sessions(worktrees: list[str]) -> tuple[dict[str, list[Session]] | None, str]:
    """Sessions per worktree path from `claude agents --json`, or None and why it couldn't be used.

    The JSON's fields (kind, cwd, status, state, waitingFor, name) aren't a documented contract, so anything unexpected
    means "unavailable", never a crash. A finished background session is history, not a session.
    """
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
    found: dict[str, list[Session]] = {}
    for entry in listed:
        if not isinstance(entry, dict) or not isinstance(entry.get("cwd"), str):
            continue
        if entry.get("kind") == "background" and entry.get("state") == "done":
            continue
        owner = owning_worktree(os.path.realpath(entry["cwd"]), worktrees)
        if owner:
            found.setdefault(owner, []).append(live_session(entry))
    return found, ""


def live_session(entry: dict[str, object]) -> Session:
    """One `claude agents --json` entry as a Session. Interactive sessions report `status`, background ones `state`;
    a background session `blocked` on a permission prompt is waiting on the user too."""
    reported = str(entry.get("status") or entry.get("state") or "")
    # Free text from Claude Code: keep it to one line with no "|", which separates fields in hub-scan.sh's rows.
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


def is_dirty(worktree: Path) -> bool | None:
    """True if the worktree has uncommitted or untracked changes; None if git can't say."""
    out = git(worktree, "status", "--porcelain")
    return None if out is None else out != ""


def pull_requests(repo_dir: Path) -> tuple[dict[str, Pr] | None, str]:
    """Each branch's PR from `gh pr list` (an open one if it has one, else its newest), or None and why not."""
    fields = "number,state,headRefName,url"
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
        if not isinstance(entry, dict) or not isinstance(entry.get("number"), int):
            continue
        pr = Pr(number=entry["number"], state=str(entry.get("state")), url=str(entry.get("url")))
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
    """The report as hub-scan.sh prints it: its WORKTREES, ORPHAN CARDS and GROUPS sections, one row per line.

    The pane parses the WORKTREES rows (`status:` is the effective state), so keep the `field: value | ...` shape and
    the path last. No value here may contain " | ".
    """
    out = ["== WORKTREES =="]
    for facts, v in report.worktrees:
        if facts.is_main:
            out.append(
                f"- {facts.label} | status: {v.status} | main checkout (no card expected) | reason: {v.reason} | {facts.path}"
            )
            continue
        card = facts.card
        fields = [
            f"- {facts.label}",
            f"status: {v.status}",
            f"card: {card.status if card else 'none'}",
            f"ticket: {card.ticket if card else 'none'}",
            f"updated: {(card.updated if card else '') or '?'}",
        ]
        if card and card.stale:
            fields.append("STALE: not updated in over 24h")
        out.append(" | ".join([*fields, f"reason: {v.reason}", facts.path]))
    limit = " — AT LIMIT" if report.slots >= report.wip_limit else ""
    out.append(
        f"WIP: {report.slots} of {report.wip_limit} (execution slots: running + needs-you, main excluded){limit}"
    )
    unshipped_rows = [f for f, _ in report.worktrees if f.unshipped_commits]
    if unshipped_rows:
        listed = ", ".join(f"{f.label} ({plural(f.unshipped_commits or 0, 'commit')})" for f in unshipped_rows)
        out.append(f"UNSHIPPED: {listed} not on {report.base}")
    else:
        out.append(f"UNSHIPPED: (none){f' — every branch is on {report.base}' if report.base else ''}")
    out.extend(f"NOTE: {note}" for note in report.notes)

    out += ["", "== ORPHAN CARDS (worktree gone) =="]
    out += [f"- {o.name} | status: {o.status or '?'} | {o.path}" for o in report.orphan_cards] or ["(none)"]

    out += ["", "== GROUPS (report in this order) =="]
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
    return "\n".join(out) + "\n"


def group_row(facts: WorktreeFacts, v: Verdict) -> str:
    """One worktree's line under its group: label, ticket, PR if known, and why it's in that group."""
    if facts.is_main:
        return f"- {facts.label} (main checkout) | {v.reason}"
    ticket = facts.card.ticket if facts.card else "none"
    pr = f" | PR #{facts.pr.number} {facts.pr.state.lower()}" if facts.pr else ""
    return f"- {facts.label} | ticket: {ticket}{pr} | {v.reason}"


def housekeeping(report: Report) -> list[str]:
    """Cleanup the hub suggests: worktrees to remove, missing or stale cards, orphans, shared worktrees."""
    lines: list[str] = []
    for facts, v in report.worktrees:
        if v.status == "ready-to-clean":
            lines.append(f"- {facts.label} | ready to clean up: {v.reason}")
        if not facts.is_main and facts.card is None:
            lines.append(f"- {facts.label} | no card")
        elif facts.card and facts.card.status not in CARD_STATUSES:
            lines.append(f"- {facts.label} | unknown card status: {facts.card.status or '?'}")
        if facts.card and facts.card.stale:
            lines.append(f"- {facts.label} | STALE: running card not updated in over 24h")
        interactive = sum(1 for s in facts.sessions if s.kind == "interactive")
        if interactive > 1:
            where = " (in the main checkout, one is the hub)" if facts.is_main else ""
            lines.append(f"- {facts.label} | {interactive} interactive sessions share this worktree{where}")
    lines += [f"- {o.name} | orphan card (worktree gone), status: {o.status or '?'}" for o in report.orphan_cards]
    return lines


def plural(count: int, noun: str) -> str:
    """ "1 commit", "3 commits"."""
    return f"{count} {noun}{'' if count == 1 else 's'}"
